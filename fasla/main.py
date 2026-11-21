"""Fasla web app: FastAPI backend + static frontend.
Run locally:  AUTH_MODE=dev STORAGE_MODE=local uvicorn fasla.main:app --port 8000
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, insert, select, update

from .auth import User, check_pw, current_user, dev_token, hash_pw
from .config import ROOT, Settings, get_settings
from .db import brands, dev_users, make_engine, plans
from .planner import WorkbookError, clean_settings, read_workbook, run_plan_on, template_bytes
from .storage import StorageError, make_storage

log = logging.getLogger("fasla")
S: Settings = get_settings()
engine = make_engine(S.database_url)
storage = make_storage(S)
pool = ThreadPoolExecutor(max_workers=S.workers)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"



async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return resp


def _recover():
    # jobs that were running when the server stopped can't finish: mark them so the user can retry
    with engine.begin() as c:
        c.execute(update(plans).where(plans.c.status.in_(["queued", "running"]))
                  .values(status="failed", error="The server restarted during planning. Please run it again.",
                          finished_at=datetime.now(timezone.utc)))
    probs = S.validate()
    if any(p.startswith("AUTH_MODE=dev") for p in probs):
        raise RuntimeError(probs[-1])
    if probs:
        log.warning("Configuration problems: %s", probs)


@asynccontextmanager
async def lifespan(_app):
    _recover()
    yield


app = FastAPI(title="Fasla", docs_url=None, redoc_url=None, lifespan=lifespan)
app.middleware("http")(security_headers)


# ---------------- frontend ----------------
@app.get("/config.js", response_class=PlainTextResponse)
def config_js():
    cfg = dict(authMode=S.auth_mode, supabaseUrl=S.supabase_url, supabaseAnonKey=S.supabase_anon_key,
               googleLogin=S.google_login, maxUploadMb=S.max_upload_mb)
    return PlainTextResponse("window.FASLA_CONFIG=" + json.dumps(cfg) + ";", media_type="application/javascript")


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((ROOT / "web" / "index.html").read_text(encoding="utf-8"))


@app.get("/healthz")
def health():
    with engine.connect() as c:
        c.execute(select(1))
    return {"ok": True, "config_problems": S.validate()}


# ---------------- dev auth (local testing only) ----------------
class Creds(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)


def _dev_only():
    if S.auth_mode != "dev":
        raise HTTPException(404, "Not found")


@app.post("/api/dev/signup")
def dev_signup(c: Creds, _=Depends(_dev_only)):
    email = c.email.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(400, "Please enter a valid email address")
    with engine.begin() as con:
        if con.execute(select(dev_users.c.id).where(dev_users.c.email == email)).first():
            raise HTTPException(409, "An account with this email already exists. Sign in instead.")
        uid = uuid.uuid4()
        con.execute(insert(dev_users).values(id=uid, email=email, pw_hash=hash_pw(c.password)))
    return {"access_token": dev_token(str(uid), email, S), "email": email}


@app.post("/api/dev/login")
def dev_login(c: Creds, _=Depends(_dev_only)):
    email = c.email.strip().lower()
    with engine.connect() as con:
        r = con.execute(select(dev_users).where(dev_users.c.email == email)).mappings().first()
    if not r or not check_pw(c.password, r["pw_hash"]):
        raise HTTPException(401, "Wrong email or password")
    return {"access_token": dev_token(str(r["id"]), email, S), "email": email}


# ---------------- templates ----------------
@lru_cache(maxsize=2)
def _template(example: bool) -> bytes:
    return template_bytes(example)


@app.get("/api/templates/{kind}")
def get_template(kind: str):
    if kind not in ("empty", "example"):
        raise HTTPException(404, "Unknown template")
    name = "fasla_template.xlsx" if kind == "empty" else "fasla_example_brand.xlsx"
    return Response(_template(kind == "example"), media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------- brands ----------------
class BrandIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _brand(con, bid: uuid.UUID, user: User):
    r = con.execute(select(brands).where(and_(brands.c.id == bid, brands.c.user_id == user.id))).mappings().first()
    if not r:
        raise HTTPException(404, "Brand not found")
    return r


def _ser(r, keys):
    out = {}
    for k in keys:
        v = r[k]
        out[k] = str(v) if isinstance(v, uuid.UUID) else v.isoformat() if isinstance(v, datetime) else v
    return out


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return {"id": str(user.id), "email": user.email}


@app.get("/api/brands")
def list_brands(user: User = Depends(current_user)):
    with engine.connect() as con:
        rows = con.execute(select(brands).where(brands.c.user_id == user.id).order_by(brands.c.created_at)).mappings().all()
        out = []
        for r in rows:
            n = con.execute(select(plans.c.id).where(and_(plans.c.brand_id == r["id"], plans.c.status == "done"))).all()
            out.append({**_ser(r, ["id", "name", "created_at"]), "plans": len(n)})
    return out


@app.post("/api/brands", status_code=201)
def create_brand(b: BrandIn, user: User = Depends(current_user)):
    name = b.name.strip()
    if not name:
        raise HTTPException(400, "Give your brand a name")
    with engine.begin() as con:
        if len(con.execute(select(brands.c.id).where(brands.c.user_id == user.id)).all()) >= 20:
            raise HTTPException(400, "You can have up to 20 brands")
        bid = uuid.uuid4()
        con.execute(insert(brands).values(id=bid, user_id=user.id, name=name))
    return {"id": str(bid), "name": name}


@app.patch("/api/brands/{bid}")
def rename_brand(bid: uuid.UUID, b: BrandIn, user: User = Depends(current_user)):
    with engine.begin() as con:
        _brand(con, bid, user)
        con.execute(update(brands).where(brands.c.id == bid).values(name=b.name.strip()))
    return {"id": str(bid), "name": b.name.strip()}


@app.delete("/api/brands/{bid}", status_code=204)
def delete_brand(bid: uuid.UUID, user: User = Depends(current_user)):
    with engine.begin() as con:
        _brand(con, bid, user)
        ps = con.execute(select(plans.c.input_path, plans.c.report_path).where(plans.c.brand_id == bid)).all()
        con.execute(delete(brands).where(brands.c.id == bid))
    _delete_files([p for row in ps for p in row if p])
    return Response(status_code=204)


def _delete_files(paths):
    try:
        storage.delete(paths)
    except StorageError as e:
        log.warning("Could not delete files %s: %s", paths, e)


# ---------------- plans ----------------
PLAN_LIST = ["id", "brand_id", "status", "input_name", "summary", "error", "created_at", "finished_at"]


@app.get("/api/brands/{bid}/plans")
def list_plans(bid: uuid.UUID, user: User = Depends(current_user)):
    with engine.connect() as con:
        _brand(con, bid, user)
        rows = con.execute(select(*[plans.c[k] for k in PLAN_LIST]).where(plans.c.brand_id == bid)
                           .order_by(plans.c.created_at.desc())).mappings().all()
    return [_ser(r, PLAN_LIST) for r in rows]


@app.post("/api/brands/{bid}/uploads", status_code=201)
async def upload(bid: uuid.UUID, file: UploadFile = File(...), user: User = Depends(current_user)):
    with engine.connect() as con:
        _brand(con, bid, user)
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "Please upload an Excel .xlsx file (use the Fasla template).")
    data = await file.read(S.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > S.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"The file is larger than {S.max_upload_mb} MB.")
    try:
        parsed = read_workbook(data)
    except WorkbookError as e:
        return JSONResponse({"ok": False, "errors": e.errors}, status_code=422)
    pid = uuid.uuid4()
    path = f"{user.id}/{bid}/{pid}/input.xlsx"
    try:
        storage.put(path, data, XLSX)
    except StorageError as e:
        raise HTTPException(502, f"Couldn't save the file: {e}")
    with engine.begin() as con:
        con.execute(insert(plans).values(id=pid, brand_id=bid, user_id=user.id, status="draft",
                                         input_name=file.filename[:200], input_path=path,
                                         settings=parsed["settings"], checks=parsed["checks"]))
    return {"ok": True, "plan_id": str(pid), "settings": parsed["settings"], "checks": parsed["checks"]}


def _plan(con, pid: uuid.UUID, user: User):
    r = con.execute(select(plans).where(and_(plans.c.id == pid, plans.c.user_id == user.id))).mappings().first()
    if not r:
        raise HTTPException(404, "Plan not found")
    return r


@app.get("/api/plans/{pid}")
def get_plan(pid: uuid.UUID, user: User = Depends(current_user)):
    with engine.connect() as con:
        r = _plan(con, pid, user)
        b = con.execute(select(brands.c.name).where(brands.c.id == r["brand_id"])).scalar()
    out = _ser(r, PLAN_LIST + ["settings", "checks"])
    out["brand_name"] = b
    if r["status"] == "done":
        out["view"] = r["view"]
    return out


class RunIn(BaseModel):
    settings: dict = {}


def _job(pid: uuid.UUID, user_id: uuid.UUID):
    try:
        with engine.begin() as con:
            r = con.execute(select(plans).where(plans.c.id == pid)).mappings().first()
            name = con.execute(select(brands.c.name).where(brands.c.id == r["brand_id"])).scalar()
            con.execute(update(plans).where(plans.c.id == pid).values(status="running"))
        data = storage.get(r["input_path"])
        view, report, summary = run_plan_on(data, r["settings"] or {}, name, S.solver_seconds)
        view["brand"] = name
        rpath = f"{user_id}/{r['brand_id']}/{pid}/report.xlsx"
        storage.put(rpath, report, XLSX)
        with engine.begin() as con:
            con.execute(update(plans).where(plans.c.id == pid).values(
                status="done", view=json.loads(json.dumps(view)), summary=summary, report_path=rpath, error=None,
                finished_at=datetime.now(timezone.utc)))
    except WorkbookError as e:
        _fail(pid, "; ".join(e.errors))
    except Exception as e:  # noqa: BLE001
        log.exception("plan %s failed", pid)
        msg = str(e) if "feasible" in str(e) or "budget" in str(e).lower() else "Something went wrong while planning. Please try again."
        _fail(pid, msg)


def _fail(pid, msg):
    with engine.begin() as con:
        con.execute(update(plans).where(plans.c.id == pid).values(status="failed", error=msg[:500],
                                                                  finished_at=datetime.now(timezone.utc)))


_run_lock = threading.Lock()


@app.post("/api/plans/{pid}/run")
def run_plan(pid: uuid.UUID, body: RunIn, user: User = Depends(current_user)):
    with _run_lock:
        with engine.begin() as con:
            r = _plan(con, pid, user)
            if r["status"] in ("queued", "running"):
                raise HTTPException(409, "This plan is already running")
            busy = con.execute(select(plans.c.id).where(and_(plans.c.user_id == user.id,
                                                             plans.c.status.in_(["queued", "running"])))).first()
            if busy:
                raise HTTPException(429, "Another plan of yours is still running. Wait for it to finish.")
            try:
                st = clean_settings(body.settings, r["settings"] or {})
            except WorkbookError as e:
                raise HTTPException(400, "; ".join(e.errors))
            con.execute(update(plans).where(plans.c.id == pid).values(status="queued", settings=st, error=None, view=None))
    pool.submit(_job, pid, user.id)
    return {"ok": True, "status": "queued"}


def _file(pid: uuid.UUID, user: User, which: str):
    with engine.connect() as con:
        r = _plan(con, pid, user)
        bname = con.execute(select(brands.c.name).where(brands.c.id == r["brand_id"])).scalar() or "brand"
    path = r["report_path"] if which == "report" else r["input_path"]
    if not path:
        raise HTTPException(404, "File not ready yet")
    try:
        data = storage.get(path)
    except StorageError:
        raise HTTPException(404, "File not found")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", bname)[:40] or "brand"
    fname = f"fasla_{safe}_{which}.xlsx"
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/api/plans/{pid}/report")
def report(pid: uuid.UUID, user: User = Depends(current_user)):
    return _file(pid, user, "report")


@app.get("/api/plans/{pid}/input")
def input_file(pid: uuid.UUID, user: User = Depends(current_user)):
    return _file(pid, user, "input")


@app.delete("/api/plans/{pid}", status_code=204)
def delete_plan(pid: uuid.UUID, user: User = Depends(current_user)):
    with engine.begin() as con:
        r = _plan(con, pid, user)
        if r["status"] in ("queued", "running"):
            raise HTTPException(409, "Wait for the plan to finish before deleting it")
        con.execute(delete(plans).where(plans.c.id == pid))
    _delete_files([p for p in (r["input_path"], r["report_path"]) if p])
    return Response(status_code=204)


app.mount("/static", StaticFiles(directory=str(ROOT / "web")), name="static")
