"""Full API flow on SQLite and on Postgres with the real Supabase migration."""
import importlib
import io
import os
import sys
import time
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(__file__))
PG_ADMIN = "postgresql://postgres@/postgres?host=/tmp&port=54329"


def _pg_ready():
    try:
        with psycopg.connect(PG_ADMIN, autocommit=True) as c:
            c.execute("drop database if exists fasla_api")
            c.execute("create database fasla_api")
        url = PG_ADMIN.replace("/postgres?", "/fasla_api?")
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(open(os.path.join(ROOT, "tests", "supabase_stub.sql")).read())
            c.execute(open(os.path.join(ROOT, "supabase", "migrations", "0001_fasla_init.sql")).read())
            # dev logins need the dev_users table and a matching auth.users row (Supabase would own these)
            c.execute("create table if not exists dev_users (id uuid primary key, email varchar(320) unique not null, pw_hash text not null, created_at timestamptz not null default now())")
            c.execute("""create or replace function sync_dev_user() returns trigger language plpgsql as $$
                         begin insert into auth.users(id,email) values (new.id,new.email) on conflict do nothing; return new; end $$""")
            c.execute("create trigger dev_user_sync after insert on dev_users for each row execute function sync_dev_user()")
        return url
    except psycopg.OperationalError:
        return None


@pytest.fixture(scope="module", params=["sqlite", "postgres"])
def client(request, tmp_path_factory):
    d = tmp_path_factory.mktemp(request.param)
    if request.param == "postgres":
        url = _pg_ready()
        if not url:
            pytest.skip("local Postgres not running")
    else:
        url = f"sqlite:///{d / 'test.db'}"
    os.environ.update(AUTH_MODE="dev", STORAGE_MODE="local", DATABASE_URL=url, LOCAL_STORAGE_DIR=str(d / "files"),
                      SOLVER_SECONDS="8", MAX_UPLOAD_MB="10")
    for m in [m for m in sys.modules if m.startswith("fasla")]:
        del sys.modules[m]
    main = importlib.import_module("fasla.main")
    with TestClient(main.app) as c:
        yield c


def signup(c, email):
    r = c.post("/api/dev/signup", json={"email": email, "password": "correct horse 1"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_needs_login(client):
    assert client.get("/api/brands").status_code == 401
    assert client.get("/api/brands", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_signup_login_rules(client):
    signup(client, "dup@x.com")
    assert client.post("/api/dev/signup", json={"email": "dup@x.com", "password": "correct horse 1"}).status_code == 409
    assert client.post("/api/dev/login", json={"email": "dup@x.com", "password": "wrong password"}).status_code == 401
    assert client.post("/api/dev/login", json={"email": "dup@x.com", "password": "correct horse 1"}).status_code == 200
    assert client.post("/api/dev/signup", json={"email": "bad", "password": "short"}).status_code == 422


def test_templates_download(client):
    for kind in ("empty", "example"):
        r = client.get(f"/api/templates/{kind}")
        assert r.status_code == 200 and r.content[:2] == b"PK"
        assert "attachment" in r.headers["content-disposition"]


def test_empty_template_is_a_valid_upload(client):
    h = signup(client, "tmpl@x.com")
    bid = client.post("/api/brands", json={"name": "Tiny"}, headers=h).json()["id"]
    data = client.get("/api/templates/empty").content
    r = client.post(f"/api/brands/{bid}/uploads", files={"file": ("t.xlsx", data)}, headers=h)
    assert r.status_code == 201, r.text
    assert any("Fewer than 300" in w for w in r.json()["checks"]["warnings"])


def test_bad_uploads_explain_the_problem(client):
    h = signup(client, "bad@x.com")
    bid = client.post("/api/brands", json={"name": "B"}, headers=h).json()["id"]
    r = client.post(f"/api/brands/{bid}/uploads", files={"file": ("x.csv", b"a,b")}, headers=h)
    assert r.status_code == 400
    r = client.post(f"/api/brands/{bid}/uploads", files={"file": ("x.xlsx", b"not excel")}, headers=h)
    assert r.status_code == 422 and "not an Excel" in r.json()["errors"][0]
    # workbook missing a sheet
    import pandas as pd
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame({"style_id": ["A"]}).to_excel(xw, sheet_name="Products", index=False)
    r = client.post(f"/api/brands/{bid}/uploads", files={"file": ("x.xlsx", buf.getvalue())}, headers=h)
    assert r.status_code == 422
    errs = " ".join(r.json()["errors"])
    assert "Sheet 'Orders' is missing" in errs and "missing column(s)" in errs


def test_full_flow_and_isolation(client):
    h1 = signup(client, f"owner{uuid.uuid4().hex[:6]}@x.com")
    h2 = signup(client, f"other{uuid.uuid4().hex[:6]}@x.com")
    b = client.post("/api/brands", json={"name": "Nile Thread"}, headers=h1)
    assert b.status_code == 201
    bid = b.json()["id"]
    assert [x["name"] for x in client.get("/api/brands", headers=h1).json()] == ["Nile Thread"]
    assert client.get("/api/brands", headers=h2).json() == []
    data = client.get("/api/templates/example").content
    up = client.post(f"/api/brands/{bid}/uploads", files={"file": ("example.xlsx", data)}, headers=h1)
    assert up.status_code == 201, up.text
    pid = up.json()["plan_id"]
    st = up.json()["checks"]["stats"]
    assert st["orders"] > 5000 and st["styles"] == 12 and len(st["seasons"]) == 2
    # other user can't see, run, download or delete it
    assert client.get(f"/api/plans/{pid}", headers=h2).status_code == 404
    assert client.post(f"/api/plans/{pid}/run", json={}, headers=h2).status_code == 404
    assert client.get(f"/api/plans/{pid}/input", headers=h2).status_code == 404
    assert client.delete(f"/api/plans/{pid}", headers=h2).status_code == 404
    assert client.post(f"/api/brands/{bid}/uploads", files={"file": ("e.xlsx", data)}, headers=h2).status_code == 404
    # invalid setting rejected
    assert client.post(f"/api/plans/{pid}/run", json={"settings": {"leftover_share": 3}}, headers=h1).status_code == 400
    r = client.post(f"/api/plans/{pid}/run", json={"settings": {"budget": 1_800_000}}, headers=h1)
    assert r.status_code == 200
    assert client.post(f"/api/plans/{pid}/run", json={}, headers=h1).status_code == 409
    t0 = time.time()
    while True:
        p = client.get(f"/api/plans/{pid}", headers=h1).json()
        if p["status"] in ("done", "failed") or time.time() - t0 > 400:
            break
        time.sleep(2)
    assert p["status"] == "done", p.get("error")
    assert p["settings"]["budget"] == 1_800_000
    v = p["view"]
    assert v["variants"][v["base"]]["cash_used"] <= 1_800_000 + 1
    assert p["summary"]["units"] > 1000
    rep = client.get(f"/api/plans/{pid}/report", headers=h1)
    assert rep.status_code == 200 and rep.content[:2] == b"PK"
    assert client.get(f"/api/plans/{pid}/input", headers=h1).content == data
    lst = client.get(f"/api/brands/{bid}/plans", headers=h1).json()
    assert lst[0]["status"] == "done" and "view" not in lst[0]
    # delete plan then brand
    assert client.delete(f"/api/plans/{pid}", headers=h1).status_code == 204
    assert client.get(f"/api/plans/{pid}", headers=h1).status_code == 404
    assert client.delete(f"/api/brands/{bid}", headers=h1).status_code == 204
    assert client.get("/api/brands", headers=h1).json() == []


def test_brand_rules(client):
    h = signup(client, f"rules{uuid.uuid4().hex[:6]}@x.com")
    assert client.post("/api/brands", json={"name": ""}, headers=h).status_code == 422
    assert client.post("/api/brands", json={"name": "x" * 81}, headers=h).status_code == 422
    bid = client.post("/api/brands", json={"name": "Old"}, headers=h).json()["id"]
    assert client.patch(f"/api/brands/{bid}", json={"name": "New"}, headers=h).json()["name"] == "New"
    assert client.get(f"/api/brands/{uuid.uuid4()}/plans", headers=h).status_code == 404


def test_config_and_health(client):
    assert "authMode" in client.get("/config.js").text
    assert client.get("/healthz").json()["ok"] is True


def test_public_config_and_pages(client):
    cfg = client.get("/api/config").json()
    assert cfg["authMode"] == "dev" and "supabaseAnonKey" in cfg and "maxUploadMb" in cfg
    assert "FASLA_CONFIG" in client.get("/config.js").text
    assert client.get("/").status_code == 200          # Next.js build if present, else the classic page
    assert client.get("/classic").status_code == 200
