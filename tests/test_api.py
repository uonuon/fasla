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
    r = client.post(f"/api/brands/{bid}/uploads", files={"file": ("x.txt", b"a,b")}, headers=h)
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


SHOPIFY_CSV = (
    "Name,Financial Status,Fulfillment Status,Created at,Lineitem quantity,Lineitem name,Lineitem price,Shipping City,Cancelled at\n"
    + "".join(
        f"#{1000 + i},paid,fulfilled,2025-{9 + i % 4:02d}-{1 + i % 27:02d} 10:00:00 +0200,1,"
        f"Classic Tee - {'Black' if i % 3 else 'White'} / {['S', 'M', 'L', 'XL'][i % 4]},550,{'Cairo' if i % 2 else 'Giza'},\n"
        f"#{1000 + i},,,,1,Cargo Pants - Olive / {['M', 'L'][i % 2]},980,,\n"
        for i in range(240))
    + "#2000,voided,,2025-10-05 10:00:00 +0200,1,Classic Tee - Black / M,550,Cairo,2025-10-06\n"
).encode()


def test_orders_only_csv_upload_and_run(client):
    h = signup(client, "shopify@x.com")
    bid = client.post("/api/brands", json={"name": "Shop"}, headers=h).json()["id"]
    r = client.post(f"/api/brands/{bid}/uploads", headers=h,
                    files={"file": ("orders_export.csv", SHOPIFY_CSV, "text/csv")})
    assert r.status_code == 201, r.text
    j = r.json()
    st = j["checks"]["stats"]
    assert st["mode"] == "orders" and st["styles"] == 2 and st["zones"] == 2
    assert {s["id"] for s in st["style_list"]} == {"Classic Tee", "Cargo Pants"}
    assert all(s["cost"] is None for s in st["style_list"])
    pid = j["plan_id"]
    # costs are required before planning
    r = client.post(f"/api/plans/{pid}/run", json={"settings": {"budget": 200000}}, headers=h)
    assert r.status_code == 400 and "cost" in r.json()["detail"]
    r = client.post(f"/api/plans/{pid}/run", headers=h, json={"settings": {
        "budget": 200000, "start_cash": 300000, "factory_moq": 0,
        "costs": {"Classic Tee": 230, "Cargo Pants": 420}, "courier_fees": {"Your courier": 65}}})
    assert r.status_code == 200, r.text
    p = wait_done(client, pid, h)
    assert p["status"] == "done", p.get("error")
    v = p["view"]
    assert v["refusal"]["overall"] > 0                      # the voided/cancelled order counts as refused
    assert {s["name"] for s in v["styles"]} == {"Classic Tee", "Cargo Pants"}
    assert client.get(f"/api/plans/{pid}/input", headers=h).headers["content-type"].startswith("text/csv")


def test_orders_only_errors_are_plain(client):
    h = signup(client, "badcsv@x.com")
    bid = client.post("/api/brands", json={"name": "B"}, headers=h).json()["id"]
    r = client.post(f"/api/brands/{bid}/uploads", headers=h,
                    files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")})
    assert r.status_code == 422
    assert any("order date" in e for e in r.json()["errors"])


def wait_done(c, pid, h, limit=400):
    t0 = time.time()
    while True:
        p = c.get(f"/api/plans/{pid}", headers=h).json()
        if p["status"] in ("done", "failed") or time.time() - t0 > limit:
            return p
        time.sleep(1)


def test_batch_plan_flow(client):
    for kind in ("empty", "example"):
        r = client.get(f"/api/batch/templates/{kind}")
        assert r.status_code == 200 and r.content[:2] == b"PK"
    example = client.get("/api/batch/templates/example").content
    files = {"file": ("fasla_batch_example.xlsx", io.BytesIO(example), "application/octet-stream")}
    assert client.post("/api/batch/plan", files=files).status_code == 401
    h = signup(client, "batch@x.com")
    r = client.post("/api/batch/plan", headers=h,
                    files={"file": ("fasla_batch_example.xlsx", io.BytesIO(example), "application/octet-stream")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_cost"] > 0 and len(body["plan"]) == 7 and body["materials"]
    assert sum(p["make"] for p in body["plan"]) > 0
    r = client.post("/api/batch/report", headers=h,
                    files={"file": ("x.xlsx", io.BytesIO(example), "application/octet-stream")})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    empty = client.get("/api/batch/templates/empty").content
    r = client.post("/api/batch/plan", headers=h, files={"file": ("e.xlsx", io.BytesIO(empty), "application/octet-stream")})
    assert r.status_code == 422 and "detail" in r.json()
    r = client.post("/api/batch/plan", headers=h, files={"file": ("e.csv", io.BytesIO(b"a,b"), "text/csv")})
    assert r.status_code == 400


def _wait_try(c, tid, secs=120):
    t0 = time.time()
    while time.time() - t0 < secs:
        r = c.get(f"/api/try/{tid}")
        assert r.status_code == 200, r.text
        if r.json()["status"] != "running":
            return r.json()
        time.sleep(0.5)
    raise AssertionError("try plan did not finish")


def test_try_without_account_keeps_a_scrubbed_copy(client):
    import fasla.main as main
    csv = ("order_id,date,style,size,color,qty,price,status,email,phone\n"
           + "".join(f"#{i},2026-0{1 + i % 9}-1{i % 9},Linen Dress,{'SML'[i % 3]},Sand,1,40,delivered,"
                     f"buyer{i}@mail.com,0100123{i:04d}\n" for i in range(150)))
    r = client.post("/api/try", files={"file": ("orders.csv", csv.encode(), "text/csv")}, data={"contact": "@brand"})
    assert r.status_code == 202, r.text
    tid = r.json()["id"]
    out = _wait_try(client, tid)
    assert out["status"] == "done", out
    st = out["result"]["styles"][0]
    assert st["name"] == "Linen Dress" and st["units"] > 0 and {s["size"] for s in st["sizes"]} <= {"S", "M", "L"}
    kept = main.storage.get(f"_try/{tid}.csv").decode()
    assert "buyer1@mail.com" not in kept and "01001230001" not in kept and "Linen Dress" in kept


def test_try_bad_file_and_unknown_id(client):
    r = client.post("/api/try", files={"file": ("x.csv", b"hello,world\n1,2\n", "text/csv")})
    assert r.status_code == 202
    out = _wait_try(client, r.json()["id"])
    assert out["status"] == "error" and out["error"]
    assert client.get("/api/try/zzzz").status_code == 404
    assert client.get("/api/try/" + "0" * 16).status_code == 404


def test_try_example_needs_no_upload(client):
    r = client.get("/api/try-example")
    assert r.status_code == 200 and r.json()["result"]["styles"]
