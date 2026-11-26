"""Fasla web app: FastAPI backend + static frontend.
Run locally:  AUTH_MODE=dev STORAGE_MODE=local uvicorn fasla.main:app --port 8000
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, insert, select, update

from .auth import User, check_pw, current_user, dev_token, hash_pw
from .config import ROOT, Settings, get_settings
from .db import brands, consents, dev_users, events, init_private, make_engine, plans, shopify_shops
from . import shopify as shp
from . import shopify_batch as shb
from .alerts import restock_alerts
from . import ai
from . import trial
from .ai_context import plan_context
from .queue import make_queue
from .planner import WorkbookError, clean_settings, read_orders_table, read_workbook, run_plan_on, template_bytes
from .simple import ai_sample, file_shape, minimal_csv, personal_columns, scrub_file, unknown_statuses
from .storage import StorageError, make_storage

log = logging.getLogger("fasla")
S: Settings = get_settings()
engine = make_engine(S.database_url)
storage = make_storage(S)
pool = ThreadPoolExecutor(max_workers=S.workers)
plan_queue = make_queue(S.plan_queue_url, S.aws_region)   # None locally: plans run on `pool` instead
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"



async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.url.path.startswith("/shopify"):
        shop = request.query_params.get("shop", "")
        anc = "https://admin.shopify.com" + (f" https://{shop}" if shp.is_shop_domain(shop) else "")
        resp.headers["Content-Security-Policy"] = f"frame-ancestors {anc};"
    else:
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return resp


def _recover():
    # jobs that were running when the server stopped can't finish: mark them so the user can retry
    try:
        with engine.connect() as c:
            c.execute(select(1))
    except Exception as e:  # say clearly why the app can't start instead of a bare stack trace
        log.error("Cannot reach the database (%s). Check DATABASE_URL: host, port 5432, user and password.",
                  type(e).__name__ + ": " + str(e).splitlines()[0][:300])
        raise
    init_private(engine)
    if plan_queue is not None:   # the worker service owns queued/running plans (and retries them)
        return _check_config()
    with engine.begin() as c:
        c.execute(update(plans).where(plans.c.status.in_(["queued", "running"]))
                  .values(status="failed", error="The server restarted during planning. Please run it again.",
                          finished_at=datetime.now(timezone.utc)))
    _check_config()


def _check_config():
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
NEXT_OUT = ROOT / "web-next" / "out"   # the Next.js static export (built in the Docker image)


def _public_config() -> dict:
    return dict(authMode=S.auth_mode, supabaseUrl=S.supabase_url, supabaseAnonKey=S.supabase_anon_key,
                googleLogin=S.google_login, maxUploadMb=S.max_upload_mb)


@app.get("/config.js", response_class=PlainTextResponse)
def config_js():
    return PlainTextResponse("window.FASLA_CONFIG=" + json.dumps(_public_config()) + ";", media_type="application/javascript")


@app.get("/api/config")
def api_config():
    return _public_config()


def _classic():
    return HTMLResponse((ROOT / "web" / "index.html").read_text(encoding="utf-8"))


@app.get("/classic", response_class=HTMLResponse)
@app.get("/classic/", response_class=HTMLResponse)
def classic_index():
    return _classic()


if not (NEXT_OUT / "index.html").exists():   # no Next.js build (e.g. tests): keep serving the classic page at /
    app.get("/", response_class=HTMLResponse)(_classic)


@app.get("/healthz")
def health():
    with engine.connect() as c:
        c.execute(select(1))
    return {"ok": True, "config_problems": S.validate(), "ai": _ai_status()}


def _ai_status() -> str:
    """Informational only: the site stays healthy when the AI sidecar is down."""
    if not ai.enabled():
        return "off"
    try:
        import httpx
        return "up" if httpx.get(f"{S.ai_url}/healthz", timeout=2).status_code == 200 else "down"
    except Exception:  # noqa: BLE001
        return "down"


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


# ---------------- Shopify app (embedded in the Shopify admin) ----------------
SHOPIFY_PAGE = ROOT / "fasla" / "shopify_app.html"


def _shopify_on():
    if not (S.shopify_api_key and S.shopify_api_secret):
        raise HTTPException(404, "Not found")


def _bearer(request: Request) -> str:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else ""


def _load_tokens(shop: str) -> shp.Tokens | None:
    with engine.connect() as con:
        r = con.execute(select(shopify_shops).where(shopify_shops.c.shop == shop)).mappings().first()
    if not r or not r["access_token"] or r["uninstalled_at"]:
        return None
    dec = lambda v: shp.decrypt(v, S.shopify_token_key, S.shopify_api_secret) if v else None  # noqa: E731
    utc = lambda d: d.replace(tzinfo=timezone.utc) if d is not None and d.tzinfo is None else d  # noqa: E731
    return shp.Tokens(dec(r["access_token"]), utc(r["access_expires_at"]), dec(r["refresh_token"]),
                      utc(r["refresh_expires_at"]), r["scope"] or "")


def _save_tokens(shop: str, t: shp.Tokens) -> None:
    enc = lambda v: shp.encrypt(v, S.shopify_token_key, S.shopify_api_secret) if v else None  # noqa: E731
    vals = dict(user_id=shp.shop_user_id(shop), access_token=enc(t.access_token), access_expires_at=t.expires_at,
                refresh_token=enc(t.refresh_token), refresh_expires_at=t.refresh_expires_at, scope=t.scope,
                uninstalled_at=None, updated_at=datetime.now(timezone.utc))
    with engine.begin() as con:
        if con.execute(select(shopify_shops.c.shop).where(shopify_shops.c.shop == shop)).first():
            con.execute(update(shopify_shops).where(shopify_shops.c.shop == shop).values(**vals))
        else:
            con.execute(insert(shopify_shops).values(shop=shop, **vals))


_token_lock = threading.Lock()


def _admin(shop: str, id_token: str = "") -> shp.Admin:
    """An Admin API client for the shop: stored token, refreshed, or exchanged from the session token."""
    with _token_lock:
        t = _load_tokens(shop)
        if t is None or not t.access_valid():
            if t is not None and t.refresh_valid():
                t = shp.refresh_token(shop, t.refresh_token, S.shopify_api_key, S.shopify_api_secret)
            elif id_token:
                t = shp.exchange_token(shop, id_token, S.shopify_api_key, S.shopify_api_secret)
            else:
                raise shp.ShopifyError("Open Fasla from your Shopify admin to reconnect.", 401)
            _save_tokens(shop, t)

    def reauth():
        # The stored token was rejected (revoked on uninstall): swap the session token for a new one.
        if not id_token:
            return None
        with _token_lock:
            fresh = shp.exchange_token(shop, id_token, S.shopify_api_key, S.shopify_api_secret)
            _save_tokens(shop, fresh)
        return fresh.access_token

    return shp.Admin(shop, t.access_token, S.shopify_api_version, reauth=reauth)


def _shop_tier(request: Request, shop: str) -> str:
    try:
        info = _admin(shop, _bearer(request)).query(shp.SHOP_QUERY)
    except shp.ShopifyError:
        return "free"
    return shp.tier(((info.get("currentAppInstallation") or {}).get("activeSubscriptions")) or [])


def _free_limit_reached(con, user: User, pid: uuid.UUID) -> bool:
    """Free Shopify plan: one plan a month (re-running that same plan is fine)."""
    start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    n = con.execute(select(func.count()).select_from(plans).where(and_(
        plans.c.user_id == user.id, plans.c.id != pid, plans.c.finished_at >= start))).scalar() or 0
    return n >= shp.FREE_PLANS_PER_MONTH


def _size_check(tier: str, checks: dict | None) -> None:
    """Plans are sized by the store (products and orders a month): stop with an upgrade message when it's bigger."""
    msg = shp.size_limit_message(tier, (checks or {}).get("stats") or {})
    if msg:
        raise HTTPException(402, msg)


def _shopify_user(request: Request, user: User) -> str:
    _shopify_on()
    if not user.shop:
        raise HTTPException(403, "This is for Fasla inside Shopify")
    return user.shop


@app.get("/shopify", response_class=HTMLResponse)
@app.get("/shopify/", response_class=HTMLResponse)
def shopify_page():
    _shopify_on()
    html = SHOPIFY_PAGE.read_text(encoding="utf-8").replace("{{SHOPIFY_API_KEY}}", S.shopify_api_key)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/shopify/session")
def shopify_session(request: Request, user: User = Depends(current_user)):
    shop = _shopify_user(request, user)
    try:
        admin = _admin(shop, _bearer(request))
        info = admin.query(shp.SHOP_QUERY)
    except shp.ShopifyError as e:
        raise HTTPException(e.status, e.message)
    name = (info.get("shop") or {}).get("name") or shop.split(".")[0]
    subs = ((info.get("currentAppInstallation") or {}).get("activeSubscriptions")) or []
    with engine.begin() as con:
        b = con.execute(select(brands).where(brands.c.user_id == user.id).order_by(brands.c.created_at)).mappings().first()
        if b is None:
            bid = uuid.uuid4()
            con.execute(insert(brands).values(id=bid, user_id=user.id, name=name[:120]))
            track("shopify_install", user.id, shop=shop)
            if _consent(user.id) is None:   # installing the app = accepting its terms (shown on the listing)
                con.execute(insert(consents).values(user_id=user.id, terms_version="shopify", improve_data=False))
        else:
            bid = b["id"]
        rows = con.execute(select(plans).where(plans.c.brand_id == bid).order_by(plans.c.created_at.desc())
                           .limit(10)).mappings().all()
    return {"shop": shop, "name": name, "currency": (info.get("shop") or {}).get("currencyCode"),
            "brand_id": str(bid), "plan": subs[0]["name"] if subs else None, "tier": shp.tier(subs),
            "pricing_url": shp.pricing_url(shop, S.shopify_app_handle),
            "plans": [_ser(r, ["id", "status", "input_name", "created_at", "finished_at", "summary", "error"]) for r in rows]}


class ImportIn(BaseModel):
    days: int = Field(default=365, ge=30, le=1100)


@app.post("/api/shopify/import", status_code=201)
def shopify_import(body: ImportIn, request: Request, user: User = Depends(current_user)):
    shop = _shopify_user(request, user)
    with engine.connect() as con:
        b = con.execute(select(brands.c.id).where(brands.c.user_id == user.id).order_by(brands.c.created_at)).first()
    if b is None:
        raise HTTPException(409, "Open the app home first")
    since = (datetime.now(timezone.utc) - timedelta(days=min(body.days, S.shopify_history_days))).date()
    try:
        orders = shp.fetch_orders(_admin(shop, _bearer(request)), since)
    except shp.ShopifyError as e:
        raise HTTPException(e.status, e.message)
    rows = shp.order_rows(orders)
    track("shopify_import", user.id, orders=len(orders), lines=len(rows), days=body.days)
    if not rows:
        return JSONResponse({"ok": False, "errors": ["We didn't find any orders in this period."]}, status_code=422)
    data = shp.rows_to_csv(rows)
    res = _plan_from_bytes(b[0], user, data, f"Shopify orders since {since.isoformat()}.csv", "csv")
    if isinstance(res, dict) and body.days > 70 and shp.only_recent_window(orders):
        res["checks"].setdefault("warnings", []).insert(0, shp.WINDOW_WARNING)
    if isinstance(res, dict):
        # Shopify order lines never carry a city (no addresses read) or production numbers: those hints are noise here
        res["checks"]["warnings"] = [w for w in res["checks"].get("warnings") or []
                                     if not w.startswith(shp.WEBSITE_ONLY_WARNINGS)]
        # clothing or batch (food, oils, cosmetics): a guess the owner can switch in the app
        res["size"] = shp.store_size(res["checks"].get("stats") or {})
        res["mode"] = shb.detect_mode(rows)
        res["batch_products"] = shb.products_for_app(data)
    return res


class ShopifyBatchIn(BaseModel):
    plan_id: uuid.UUID
    products: dict = {}
    budget: float | None = Field(default=None, ge=0, le=1e10)
    months_ahead: int = Field(default=4, ge=1, le=12)


@app.post("/api/shopify/batch-plan")
def shopify_batch_plan(body: ShopifyBatchIn, request: Request, user: User = Depends(current_user)):
    """Batch mode: plan the next batches from the orders this store already imported (same stored file)."""
    shop = _shopify_user(request, user)
    with engine.connect() as con:
        r = _plan(con, body.plan_id, user)
        tier = _shop_tier(request, shop)
        if tier == "free" and _free_limit_reached(con, user, body.plan_id):
            raise HTTPException(402, "The free plan includes one plan a month. Upgrade to Starter for unlimited plans.")
    _size_check(tier, r["checks"])
    try:
        res = shb.plan_from_csv(storage.get(r["input_path"]), body.products, body.budget, body.months_ahead)
    except ValueError as e:
        track("batch_failed", user.id, error=str(e)[:200], shopify=True)
        raise HTTPException(422, str(e))
    plan = res["plan"]
    with engine.begin() as con:   # counts toward the free plan's one a month, like a clothing plan
        con.execute(update(plans).where(plans.c.id == body.plan_id).values(finished_at=datetime.now(timezone.utc)))
    track("batch_plan", user.id, skus=len(plan), make=int(plan.make.sum()), cost=round(res["total_cost"]), shopify=True)
    return {"start": str(res["start"]), "total_cost": round(res["total_cost"]), "notes": res["notes"],
            "plan": _records(plan.round(3)), "batches": _records(res["batches"].round(1))}


@app.get("/api/shopify/alerts")
def shopify_alerts(request: Request, user: User = Depends(current_user)):
    """Restock alerts (Growth and Scale) from the store's latest imported orders and stock."""
    shop = _shopify_user(request, user)
    tier = _shop_tier(request, shop)
    if tier not in shp.ALERT_TIERS:
        raise HTTPException(402, "Restock alerts are on Growth and Scale: Fasla tells you which sizes and colours "
                                 "will run out before new stock could arrive.")
    with engine.connect() as con:
        r = con.execute(select(plans).where(and_(plans.c.user_id == user.id, plans.c.input_path.is_not(None)))
                        .order_by(plans.c.created_at.desc()).limit(1)).mappings().first()
    if r is None:
        raise HTTPException(409, "Read your orders first.")
    _size_check(tier, r["checks"])
    lead = float((r["settings"] or {}).get("lead_weeks") or 4)
    try:
        res = restock_alerts(storage.get(r["input_path"]), lead)
    except (WorkbookError, StorageError, ValueError) as e:
        raise HTTPException(422, f"Couldn't check stock: {e}")
    track("shopify_alerts", user.id, alerts=len(res["alerts"]), checked=res["checked"])
    return res


@app.post("/webhooks/shopify")
async def shopify_webhook(request: Request):
    _shopify_on()
    body = await request.body()
    if not shp.verify_webhook(body, request.headers.get("x-shopify-hmac-sha256", ""), S.shopify_api_secret):
        raise HTTPException(401, "Invalid HMAC")
    topic = request.headers.get("x-shopify-topic", "")
    shop = request.headers.get("x-shopify-shop-domain", "").lower()
    if not shp.is_shop_domain(shop):
        return {"ok": True}
    uid = shp.shop_user_id(shop)
    if topic == "app/uninstalled":
        with engine.begin() as con:
            con.execute(update(shopify_shops).where(shopify_shops.c.shop == shop).values(
                access_token=None, refresh_token=None, access_expires_at=None, refresh_expires_at=None,
                uninstalled_at=datetime.now(timezone.utc)))
        track("shopify_uninstall", uid)
    elif topic == "shop/redact":
        # 48h after uninstall: delete everything Fasla holds for this shop
        with engine.begin() as con:
            paths = [p for r in con.execute(select(plans.c.input_path, plans.c.report_path)
                                            .where(plans.c.user_id == uid)).all() for p in r if p]
            con.execute(delete(plans).where(plans.c.user_id == uid))
            con.execute(delete(brands).where(brands.c.user_id == uid))
            con.execute(delete(consents).where(consents.c.user_id == uid))
            con.execute(delete(shopify_shops).where(shopify_shops.c.shop == shop))
        _delete_files(paths)
        track("shopify_redact", None, topic=topic)
    elif topic in ("customers/data_request", "customers/redact"):
        # Fasla never stores customer names, emails, phones or addresses: nothing to send or delete
        track("shopify_redact", None, topic=topic)
    return {"ok": True}


# ---------------- try Fasla without an account ----------------
# Anyone can upload an orders file and see a plan. We keep a scrubbed copy (customer details removed) to
# improve how Fasla reads files and forecasts, as the privacy policy says.
_try_hits: dict[str, list[float]] = {}
_try_lock = threading.Lock()
TRY_PER_HOUR = 6


def _put_lenient(path: str, data: bytes, ctype: str) -> None:
    """The Supabase bucket may only allow xlsx (supabase/migrations/0002 not applied yet): the bytes are what matter."""
    try:
        storage.put(path, data, ctype)
    except StorageError:
        if ctype == XLSX:
            raise
        storage.put(path, data, XLSX)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else "") or (request.client.host if request.client else "?")


def _try_rate(ip: str) -> None:
    now = time.time()
    with _try_lock:
        hits = [t for t in _try_hits.get(ip, []) if now - t < 3600]
        if len(hits) >= TRY_PER_HOUR:
            raise HTTPException(429, "You've planned a few files already. Create a free account to keep going.")
        hits.append(now)
        _try_hits[ip] = hits


def _try_job(tid: str, data: bytes, who: str) -> None:
    try:
        _, mapping = _smart_parse(data, who)
        res = trial.plan_file(data, mapping=mapping or None)
        out = {"status": "done", "result": res}
        track("try_done", None, try_id=tid, styles=len(res["styles"]), units=res["units"], seconds=res["seconds"])
    except WorkbookError as e:
        out = {"status": "error", "error": " ".join(e.errors[:3])}
        track("try_failed", None, try_id=tid, error=out["error"][:300])
    except Exception:  # noqa: BLE001
        log.exception("try plan %s failed", tid)
        out = {"status": "error", "error": "Something went wrong while planning this file. We've been told and will look into it."}
        track("try_failed", None, try_id=tid, error="crash")
    try:
        _put_lenient(f"_try/{tid}.json", trial.encode(out), "application/json")
    except StorageError:
        log.exception("could not save try result %s", tid)


@app.post("/api/try", status_code=202)
async def try_upload(request: Request, file: UploadFile = File(...), contact: str = Form("")):
    ip = _client_ip(request)
    _try_rate(ip)
    data = await file.read(S.max_upload_mb * 1024 * 1024 + 1)
    if not data:
        raise HTTPException(400, "The file is empty.")
    if len(data) > S.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"The file is larger than {S.max_upload_mb} MB.")
    try:
        clean, ext = scrub_file(data)
    except Exception:  # noqa: BLE001
        raise HTTPException(422, "We couldn't open this file. Upload your orders export as .xlsx or .csv.")
    tid = uuid.uuid4().hex[:16]
    try:
        _put_lenient(f"_try/{tid}.{ext}", clean, XLSX if ext == "xlsx" else "text/csv")
        _put_lenient(f"_try/{tid}.json", trial.encode({"status": "running"}), "application/json")
    except StorageError as e:
        raise HTTPException(502, f"Couldn't save the file: {e}")
    track("try_upload", None, try_id=tid, ext=ext, file_name=(file.filename or "")[:120], contact=contact.strip()[:200])
    pool.submit(_try_job, tid, clean, "try-" + hashlib.sha256(ip.encode()).hexdigest()[:12])
    return {"id": tid}


@app.get("/api/try/{tid}")
def try_status(tid: str):
    if not re.fullmatch(r"[0-9a-f]{16}", tid):
        raise HTTPException(404, "Not found")
    try:
        raw = storage.get(f"_try/{tid}.json")
    except StorageError:
        raise HTTPException(404, "Not found")
    try:
        body = json.loads(raw)
    except ValueError:
        body = {"status": "running"}  # caught mid-write; the next poll gets it
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@lru_cache(maxsize=1)
def _example_plan() -> dict:
    return trial.plan_file(template_bytes(True))


@app.get("/api/try-example")
def try_example():
    return {"status": "done", "result": _example_plan()}


# ---------------- batch products (food in jars, oils, creams): plan the next batch ----------------
# The file is read in memory and never stored; only the plan goes back to the browser.
@lru_cache(maxsize=2)
def _batch_template(example: bool) -> bytes:
    import io
    import tempfile

    from brandopt.batch_excel import write_template
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
        write_template(f.name, example=example)
        return io.open(f.name, "rb").read()


@app.get("/api/batch/templates/{kind}")
def get_batch_template(kind: str):
    if kind not in ("empty", "example"):
        raise HTTPException(404, "Unknown template")
    name = "fasla_batch_template.xlsx" if kind == "empty" else "fasla_batch_example.xlsx"
    return Response(_batch_template(kind == "example"), media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def _batch_run(file: UploadFile, user: User):
    import io

    from brandopt.batch_excel import read_inputs, run
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "Please upload the Fasla batch workbook (.xlsx).")
    data = await file.read(S.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > S.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"The file is larger than {S.max_upload_mb} MB.")
    try:
        return run(read_inputs(io.BytesIO(data)))
    except ValueError as e:
        track("batch_failed", user.id, error=str(e)[:200])
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("batch plan crashed")
        track("batch_failed", user.id, error=f"{type(e).__name__}: {str(e)[:150]}", where=_where(e))
        raise HTTPException(422, "Something went wrong on our side while reading this file. We've logged it.")


def _records(df):
    import math as _m
    out = []
    for r in df.to_dict("records"):
        out.append({k: (None if isinstance(v, float) and (_m.isnan(v) or _m.isinf(v)) else
                        (str(v) if hasattr(v, "isoformat") else v)) for k, v in r.items()})
    return out


@app.post("/api/batch/plan")
async def batch_plan(file: UploadFile = File(...), user: User = Depends(current_user)):
    res = await _batch_run(file, user)
    plan = res["plan"]
    track("batch_plan", user.id, skus=len(plan), make=int(plan.make.sum()), cost=round(res["total_cost"]),
          months=res["fc"]["history_months"], example=(file.filename or "").startswith("fasla_batch_example"))
    return {"start": str(res["start"]), "total_cost": round(res["total_cost"]), "notes": res["notes"],
            "plan": _records(plan.round(3)), "batches": _records(res["batches"].round(1)),
            "materials": _records(res["materials"]), "forecast": _records(res["forecast"].round(0)),
            "history": [{"month": str(m), "units": round(float(v))} for m, v in res["fc"]["history_total"].items()]}


@app.post("/api/batch/report")
async def batch_report(file: UploadFile = File(...), user: User = Depends(current_user)):
    import tempfile

    from brandopt.batch_excel import write_report
    res = await _batch_run(file, user)
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
        write_report(res, f.name)
        body = open(f.name, "rb").read()
    return Response(body, media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="fasla_batch_plan.xlsx"'})


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
    c = _consent(user.id)
    return {"id": str(user.id), "email": user.email, "admin": _is_admin(user), "terms_version": TERMS_VERSION,
            "consent": None if not c else {"terms_version": c["terms_version"], "improve_data": c["improve_data"]}}


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
    track("brand_created", user.id)
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
    fname = (file.filename or "").lower()
    if not fname.endswith((".xlsx", ".csv")):
        raise HTTPException(400, "Please upload an Excel (.xlsx) or CSV file, such as your Shopify orders export.")
    ext = "csv" if fname.endswith(".csv") else "xlsx"
    data = await file.read(S.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > S.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"The file is larger than {S.max_upload_mb} MB.")
    return _plan_from_bytes(bid, user, data, file.filename or "", ext)


def _plan_from_bytes(bid: uuid.UUID, user: User, data: bytes, filename: str, ext: str):
    """Read an orders file (uploaded, or built from a Shopify store), store the minimal copy, create a draft plan."""
    if ext == "xlsx" and not data.startswith(b"PK\x03\x04"):
        return JSONResponse({"ok": False, "errors": ["This is not an Excel .xlsx file. Save it again as .xlsx or CSV and retry."]},
                            status_code=422)
    try:
        parsed, mapping = _smart_parse(data, str(user.id))
    except WorkbookError as e:
        rid = _upload_log("upload_failed", user.id, data, filename, errors=e.errors)
        return JSONResponse({"ok": False, "errors": e.errors, "report_id": rid}, status_code=422)
    except Exception as e:  # noqa: BLE001  a bug in our reader: record it, tell the owner kindly
        log.exception("upload crashed")
        msg = "Something went wrong on our side while reading this file. We've logged it and will fix it."
        rid = _upload_log("upload_crashed", user.id, data, filename, errors=[f"{type(e).__name__}: {str(e)[:200]}"],
                          where=_where(e))
        return JSONResponse({"ok": False, "errors": [msg], "report_id": rid}, status_code=422)
    data_in = data
    if mapping:
        parsed["settings"] = {**parsed["settings"], "_mapping": mapping}
    # Privacy: for orders files keep only the columns Fasla reads; customer details never get stored.
    removed: list[str] = []
    if parsed["checks"]["stats"].get("mode") == "orders":
        table = read_orders_table(data)
        if table is not None:
            used = dict(parsed["checks"]["stats"].get("columns") or {})
            removed = personal_columns(table, used)
            data, ext = minimal_csv(table, used), "csv"
            parsed["checks"]["stats"]["dropped_columns"] = len(table.columns) - len(used)
            parsed["checks"]["stats"]["personal_removed"] = removed[:12]
    pid = uuid.uuid4()
    path = f"{user.id}/{bid}/{pid}/input.{ext}"
    try:
        try:
            storage.put(path, data, XLSX if ext == "xlsx" else "text/csv")
        except StorageError:
            if ext != "csv":
                raise
            # bucket not yet migrated to allow text/csv (supabase/migrations/0002): the bytes are what matter
            storage.put(path, data, XLSX)
    except StorageError as e:
        raise HTTPException(502, f"Couldn't save the file: {e}")
    st_ = parsed["checks"]["stats"]
    warns = parsed["checks"].get("warnings") or []
    if (mapping or warns) and not (filename or "").startswith("fasla_example"):
        # it worked, but only with AI help or with warnings: keep the file's shape so the rules can learn it
        _upload_log("upload_hard", user.id, data_in, filename, errors=warns[:10], mapping=mapping,
                    mode=st_.get("mode"), orders=st_.get("orders"))
    track("upload_ok", user.id, mode=st_.get("mode"), orders=st_.get("orders"), styles=st_.get("styles"),
          seasons=len(st_.get("seasons") or []), ai_mapped=bool(st_.get("ai_mapped")),
          example=(filename or "").startswith("fasla_example"))
    with engine.begin() as con:
        con.execute(insert(plans).values(id=pid, brand_id=bid, user_id=user.id, status="draft",
                                         input_name=filename[:200], input_path=path,
                                         settings=parsed["settings"], checks=parsed["checks"]))
    return {"ok": True, "plan_id": str(pid), "settings": parsed["settings"], "checks": parsed["checks"]}


UPLOAD_LOGS = ("upload_failed", "upload_crashed", "upload_hard")


def _report_event(con, rid: str, user_id=None):
    q = select(events).where(events.c.name.in_(UPLOAD_LOGS)).order_by(events.c.created_at.desc()).limit(2000)
    if user_id is not None:
        q = q.where(events.c.user_id == user_id)
    return next((r for r in con.execute(q).mappings() if (r["props"] or {}).get("report_id") == rid), None)


def _purge_shared_reports() -> None:
    """Shared files are kept 30 days. Runs opportunistically (on share and on the admin view); never raises."""
    try:
        now = datetime.now(timezone.utc)
        with engine.connect() as con:
            rows = con.execute(select(events.c.props).where(and_(
                events.c.name == "upload_shared", events.c.created_at < now - timedelta(days=30),
                events.c.created_at >= now - timedelta(days=60)))).all()
        paths = [f"_reports/{(r.props or {}).get('report_id')}.{(r.props or {}).get('ext')}" for r in rows]
        if paths:
            storage.delete(paths)
    except Exception:  # noqa: BLE001
        log.warning("could not purge shared reports", exc_info=True)


@app.post("/api/upload-reports/{rid}", status_code=201)
async def share_failed_upload(rid: str, file: UploadFile = File(...), user: User = Depends(current_user)):
    """The owner chose to send us a file we couldn't read. We keep a scrubbed copy (customer details
    removed) only to fix the reader, and delete it after 30 days."""
    with engine.connect() as con:
        if not _report_event(con, rid[:12], user.id):
            raise HTTPException(404, "Not found")
    data = await file.read(S.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > S.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"The file is larger than {S.max_upload_mb} MB.")
    try:
        clean, ext = scrub_file(data)
    except Exception:  # noqa: BLE001
        raise HTTPException(422, "We couldn't open this file to remove customer details, so we didn't keep it.")
    try:
        storage.put(f"_reports/{rid[:12]}.{ext}", clean, XLSX)
    except StorageError as e:
        raise HTTPException(502, f"Couldn't save the file: {e}")
    track("upload_shared", user.id, report_id=rid[:12], ext=ext, contact=(user.email or "")[:320])
    _purge_shared_reports()
    return {"ok": True}


AI_FIELDS = ["date", "style", "size", "color", "qty", "price", "zone", "status", "courier", "cost", "category"]


def _smart_parse(data: bytes, user_id: str) -> tuple[dict, dict]:
    """Read the upload with Fasla's own rules first; if they can't, ask the AI which column is which.
    Then, if status values aren't recognised (e.g. Arabic), ask the AI to classify them.
    Returns (parsed, mapping) where mapping is stored with the plan so planning reads the file the same way."""
    mapping: dict = {}
    try:
        parsed = read_workbook(data)
    except WorkbookError as e:
        if not (e.columns_problem and ai.enabled()):
            raise
        table = read_orders_table(data)
        if table is None:
            raise
        try:
            res = ai.map_columns(user_id, ai_sample(table), AI_FIELDS)
        except ai.AIError as ae:
            raise WorkbookError(e.errors + [f"Our AI couldn't help this time: {ae.message}"])
        mapping["columns"] = res.get("columns") or {}
        try:
            parsed = read_workbook(data, mapping=mapping)
        except WorkbookError as e2:
            raise WorkbookError(e2.errors + ["Even with AI help we couldn't read this file. "
                                             "Rename the columns to date, product, size, price and upload it again."])
    if parsed["checks"]["stats"].get("mode") == "orders":
        table = read_orders_table(data)
        unknown = unknown_statuses(table, mapping.get("columns")) if table is not None else []
        if unknown and ai.enabled():
            try:
                mapping["status"] = ai.map_statuses(user_id, unknown).get("status") or {}
                parsed = read_workbook(data, mapping=mapping)
            except ai.AIError as ae:
                log.warning("status mapping skipped: %s", ae.message)
        unmapped = [v for v in unknown if v not in (mapping.get("status") or {})]
        if unmapped:
            parsed["checks"]["warnings"].append(
                f"We didn't recognise these status values and counted them as delivered: {', '.join(unmapped[:6])}"
                + ("…" if len(unmapped) > 6 else "."))
    return parsed, mapping


def _where(e: Exception) -> str:
    """file:line of the innermost frame in our own code, so a crash points at the line to fix."""
    import traceback
    frames = [f for f in traceback.extract_tb(e.__traceback__) if "/fasla/" in f.filename or "/brandopt/" in f.filename]
    f = frames[-1] if frames else None
    return f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno} {f.name}" if f else ""


def _upload_log(name: str, user_id, data: bytes, filename: str | None, **props) -> str:
    """Record a hard or failed upload with the file's shape (headers, example values, never customer details).
    Returns an id the owner can use to share the actual file with us."""
    rid = uuid.uuid4().hex[:12]
    fn = (filename or "").lower()
    track(name, user_id, report_id=rid, ext=fn.rsplit(".", 1)[-1][:5] if "." in fn else "",
          error=" | ".join(props.pop("errors", []) or [])[:600], shape=file_shape(data), **props)
    return rid


# ---------------- usage tracking (first-party, no third parties) ----------------
EVENT_NAMES = {
    # server-side
    "signup", "brand_created", "upload_ok", "upload_failed", "upload_crashed", "upload_hard", "upload_shared", "plan_started", "plan_done", "plan_failed", "replan",
    "ai_explain", "ai_ask", "consent", "shopify_install", "shopify_import", "shopify_uninstall", "shopify_redact",
    "batch_plan", "batch_failed",
    # sent by the browser
    "page_view", "budget_change", "style_open", "report_download", "replan_open", "example_used", "setup_step",
}


def track(name: str, user_id=None, **props) -> None:
    """Record a product event. Never raises: tracking must not break the product."""
    try:
        with engine.begin() as con:
            con.execute(insert(events).values(user_id=user_id, name=name[:40], props=props or None))
    except Exception:  # noqa: BLE001
        log.warning("could not record event %s", name, exc_info=True)


class EventIn(BaseModel):
    name: str = Field(max_length=40)
    props: dict = Field(default_factory=dict)


@app.post("/api/events", status_code=204)
def post_event(e: EventIn, user: User = Depends(current_user)):
    if e.name in EVENT_NAMES and len(json.dumps(e.props)) <= 1000:
        track(e.name, user.id, **{str(k)[:30]: v for k, v in list(e.props.items())[:10]})
    return Response(status_code=204)


TERMS_VERSION = "2026-11-22"


class ConsentIn(BaseModel):
    terms_version: str = Field(max_length=20)
    improve_data: bool = False


def _consent(user_id):
    with engine.connect() as con:
        return con.execute(select(consents).where(consents.c.user_id == user_id)).mappings().first()


@app.post("/api/consent")
def post_consent(c: ConsentIn, user: User = Depends(current_user)):
    if c.terms_version != TERMS_VERSION:
        raise HTTPException(400, "Please reload the page to see the latest terms.")
    first = _consent(user.id) is None
    with engine.begin() as con:
        con.execute(delete(consents).where(consents.c.user_id == user.id))
        con.execute(insert(consents).values(user_id=user.id, terms_version=c.terms_version, improve_data=c.improve_data,
                                            updated_at=datetime.now(timezone.utc)))
    track("consent", user.id, improve_data=c.improve_data)
    if first:
        track("signup", user.id)
    return {"ok": True}


def _is_admin(user: User) -> bool:
    admins = {a.strip().lower() for a in S.admin_emails.split(",") if a.strip()}
    return bool(user.email) and user.email.lower() in admins


@app.get("/api/admin/metrics")
def admin_metrics(days: int = 14, user: User = Depends(current_user)):
    if not _is_admin(user):
        raise HTTPException(404, "Not found")
    from sqlalchemy import func
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 180)))
    with engine.connect() as con:
        by_name = dict(con.execute(select(events.c.name, func.count(func.distinct(events.c.user_id)))
                                   .where(events.c.created_at >= since).group_by(events.c.name)).all())
        totals = dict(con.execute(select(events.c.name, func.count()).where(events.c.created_at >= since)
                                  .group_by(events.c.name)).all())
        rows = con.execute(select(events.c.name, events.c.created_at, events.c.props)
                           .where(events.c.created_at >= since).order_by(events.c.created_at.desc()).limit(5000)).all()
        failures = [dict(at=r.created_at.isoformat(), **(r.props or {})) for r in rows if r.name in ("upload_failed", "plan_failed")][:30]
        consent_rows = con.execute(select(consents.c.improve_data, func.count()).group_by(consents.c.improve_data)).all()
    daily: dict[str, dict[str, int]] = {}
    for r in rows:
        d = r.created_at.date().isoformat()
        daily.setdefault(d, {}).setdefault(r.name, 0)
        daily[d][r.name] += 1
    funnel = [(k, by_name.get(k, 0)) for k in ("signup", "brand_created", "upload_ok", "plan_started", "plan_done",
                                               "page_view", "replan", "report_download", "ai_explain", "ai_ask")]
    return {"days": days, "funnel_users": funnel, "event_totals": totals,
            "daily": dict(sorted(daily.items(), reverse=True)), "recent_failures": failures,
            "improve_data_opt_in": {str(bool(k)).lower(): n for k, n in consent_rows}}


@app.get("/api/admin/uploads")
def admin_uploads(days: int = 30, user: User = Depends(current_user)):
    """Uploads that failed, crashed or needed AI/warnings, with the file's shape, newest first."""
    if not _is_admin(user):
        raise HTTPException(404, "Not found")
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 180)))
    with engine.connect() as con:
        rows = con.execute(select(events.c.name, events.c.created_at, events.c.props)
                           .where(and_(events.c.created_at >= since, events.c.name.in_(UPLOAD_LOGS + ("upload_shared",))))
                           .order_by(events.c.created_at.desc()).limit(300)).all()
    _purge_shared_reports()
    shared = {(r.props or {}).get("report_id"): (r.props or {}).get("contact", "") for r in rows if r.name == "upload_shared"}
    return [dict(type=r.name, at=r.created_at.isoformat(), shared=(r.props or {}).get("report_id") in shared,
                 contact=shared.get((r.props or {}).get("report_id"), ""), **(r.props or {}))
            for r in rows if r.name != "upload_shared"]


@app.get("/api/admin/upload-reports/{rid}")
def admin_upload_file(rid: str, user: User = Depends(current_user)):
    if not _is_admin(user):
        raise HTTPException(404, "Not found")
    rid = re.sub(r"[^0-9a-f]", "", rid)[:12]
    for ext, ctype in (("xlsx", XLSX), ("csv", "text/csv")):
        try:
            data = storage.get(f"_reports/{rid}.{ext}")
        except StorageError:
            continue
        return Response(data, media_type=ctype, headers={"Content-Disposition": f'attachment; filename="report_{rid}.{ext}"'})
    raise HTTPException(404, "Not shared")


def _ai_error(e: ai.AIError) -> JSONResponse:
    code = e.body.get("code", "ai_error")
    status = e.status if e.status in (400, 413, 429, 502, 503) else 503
    return JSONResponse({"detail": e.message, "code": code}, status_code=status)


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
    """Run one plan. Used by the in-process pool (local) and by fasla/worker.py (AWS)."""
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
        track("plan_done", user_id, units=summary.get("units"), seasons=view["history"]["seasons"])
    except WorkbookError as e:
        _fail(pid, "; ".join(e.errors))
    except Exception as e:  # noqa: BLE001
        log.exception("plan %s failed", pid)
        msg = str(e) if "feasible" in str(e) or "budget" in str(e).lower() else "Something went wrong while planning. Please try again."
        _fail(pid, msg)


def _fail(pid, msg):
    track("plan_failed", None, plan=str(pid), error=msg[:300])
    with engine.begin() as con:
        con.execute(update(plans).where(plans.c.id == pid).values(status="failed", error=msg[:500],
                                                                  finished_at=datetime.now(timezone.utc)))


_run_lock = threading.Lock()


@app.post("/api/plans/{pid}/run")
def run_plan(pid: uuid.UUID, body: RunIn, request: Request, user: User = Depends(current_user)):
    if user.shop:
        tier = _shop_tier(request, user.shop)
        with engine.connect() as con:
            if tier == "free" and _free_limit_reached(con, user, pid):
                raise HTTPException(402, "The free plan includes one plan a month. Upgrade to Starter for unlimited plans.")
            _size_check(tier, _plan(con, pid, user)["checks"])
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
            styles = ((r["checks"] or {}).get("stats") or {}).get("style_list") or []
            costs = st.get("costs") or {}
            missing = [x["name"] for x in styles if x.get("cost") is None and not costs.get(x["id"])]
            if missing:
                raise HTTPException(400, "Add the cost to make each style. Missing: " + ", ".join(missing[:8])
                                    + (f" and {len(missing) - 8} more." if len(missing) > 8 else "."))
            was_done = r["status"] == "done"
            con.execute(update(plans).where(plans.c.id == pid).values(status="queued", settings=st, error=None, view=None))
    track("replan" if was_done else "plan_started", user.id)
    if plan_queue is not None:
        try:
            plan_queue.send(str(pid), str(user.id))
        except Exception:  # noqa: BLE001
            log.exception("could not queue plan %s", pid)
            _fail(pid, "We couldn't start planning. Please try again.")
            raise HTTPException(503, "We couldn't start planning. Please try again in a minute.")
    else:
        pool.submit(_job, pid, user.id)
    return {"ok": True, "status": "queued", "queued": plan_queue is not None}


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
    ext = "csv" if path.endswith(".csv") else "xlsx"
    fname = f"fasla_{safe}_{which}.{ext}"
    return Response(data, media_type=XLSX if ext == "xlsx" else "text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/api/plans/{pid}/report")
def report(pid: uuid.UUID, request: Request, user: User = Depends(current_user)):
    if user.shop and _shop_tier(request, user.shop) == "free":
        raise HTTPException(402, "The Excel plan (sizes and colours per style) is on Starter and above.")
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


# ---------------- AI assistant ----------------
class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    history: list[dict] = Field(default_factory=list, max_length=12)


def _done_plan(pid: uuid.UUID, user: User):
    with engine.connect() as con:
        r = _plan(con, pid, user)
        b = con.execute(select(brands.c.name).where(brands.c.id == r["brand_id"])).scalar() or "Your brand"
    if r["status"] != "done" or not r["view"]:
        raise HTTPException(409, "The plan isn't ready yet.")
    key = f"{pid}:{r['finished_at'].isoformat() if r['finished_at'] else ''}"
    return key, plan_context(r["view"], b)


@app.get("/api/ai/usage")
def ai_usage(user: User = Depends(current_user)):
    if not ai.enabled():
        return {"enabled": False}
    try:
        return {"enabled": True, **ai.usage(str(user.id))}
    except ai.AIError:
        return {"enabled": False}


@app.post("/api/plans/{pid}/explain")
def explain_plan(pid: uuid.UUID, user: User = Depends(current_user)):
    key, ctx = _done_plan(pid, user)
    try:
        r = ai.explain(str(user.id), key, ctx)
        track("ai_explain", user.id, cached=bool(r.get("cached")))
        return r
    except ai.AIError as e:
        return _ai_error(e)


@app.post("/api/plans/{pid}/ask")
def ask_plan(pid: uuid.UUID, body: AskIn, user: User = Depends(current_user)):
    key, ctx = _done_plan(pid, user)
    history = [{"role": str(m.get("role", "")), "text": str(m.get("text", ""))[:1500]} for m in body.history[-6:]]
    try:
        r = ai.ask(str(user.id), key, ctx, body.question.strip(), history)
        track("ai_ask", user.id)
        return r
    except ai.AIError as e:
        return _ai_error(e)


app.mount("/static", StaticFiles(directory=str(ROOT / "web")), name="static")
if (NEXT_OUT / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(NEXT_OUT), html=True), name="next")
