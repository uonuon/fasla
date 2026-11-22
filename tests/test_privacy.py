"""Customer personal data is never stored; consent and usage tracking work; admin metrics are private."""
from __future__ import annotations

import importlib
import io
import os
import sys
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

ROWS = 300
CSV = ("Name,Email,Phone,Billing Name,Shipping Address1,Created at,Lineitem quantity,Lineitem name,Lineitem price,"
       "Shipping City,Financial Status,Cancelled at,Notes\n" + "".join(
           f"#{1000 + i},buyer{i}@mail.com,+2010000{i:05d},Customer {i},{i} Tahrir St,"
           f"2025-{9 + i % 4:02d}-{1 + i % 27:02d} 10:00:00 +0200,1,Tee - Black / {['S', 'M', 'L'][i % 3]},550,"
           f"{'Cairo' if i % 2 else 'Giza'},paid,,call before delivery\n" for i in range(ROWS))).encode()


@pytest.fixture()
def client(tmp_path):
    os.environ.update(AUTH_MODE="dev", STORAGE_MODE="local", DATABASE_URL=f"sqlite:///{tmp_path / 'p.db'}",
                      LOCAL_STORAGE_DIR=str(tmp_path / "files"), SOLVER_SECONDS="8", ADMIN_EMAILS="boss@x.com")
    os.environ.pop("AI_URL", None)
    for m in [m for m in sys.modules if m.startswith("fasla")]:
        del sys.modules[m]
    main = importlib.import_module("fasla.main")
    with TestClient(main.app) as c:
        yield c, tmp_path
    os.environ.pop("ADMIN_EMAILS", None)


def _signup(c, email):
    r = c.post("/api/dev/signup", json={"email": email, "password": "correct horse 1"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_personal_columns_are_never_stored(client):
    c, tmp = client
    h = _signup(c, "brand@x.com")
    bid = c.post("/api/brands", json={"name": "B"}, headers=h).json()["id"]
    r = c.post(f"/api/brands/{bid}/uploads", headers=h, files={"file": ("orders_export.csv", CSV, "text/csv")})
    assert r.status_code == 201, r.text
    st = r.json()["checks"]["stats"]
    assert {"Email", "Phone", "Billing Name", "Shipping Address1", "Notes"} <= set(st["personal_removed"])
    assert "Shipping City" not in st["personal_removed"]            # used for zones, so kept
    pid = r.json()["plan_id"]
    stored = c.get(f"/api/plans/{pid}/input", headers=h).content.decode()
    for secret in ("buyer1@mail.com", "+2010000", "Tahrir", "Customer 1", "call before"):
        assert secret not in stored
    kept = set(pd.read_csv(io.StringIO(stored)).columns)
    assert kept == {"Name", "Created at", "Lineitem quantity", "Lineitem name", "Lineitem price", "Shipping City",
                    "Financial Status", "Cancelled at"}
    # the stripped file still plans
    r = c.post(f"/api/plans/{pid}/run", headers=h, json={"settings": {"budget": 50000, "start_cash": 80000,
                                                                      "factory_moq": 0, "costs": {"Tee": 230}}})
    assert r.status_code == 200, r.text
    t0 = time.time()
    while (p := c.get(f"/api/plans/{pid}", headers=h).json())["status"] not in ("done", "failed") and time.time() - t0 < 300:
        time.sleep(1)
    assert p["status"] == "done", p.get("error")


def test_consent_events_and_admin_metrics(client):
    c, _ = client
    h = _signup(c, "owner@x.com")
    me = c.get("/api/me", headers=h).json()
    assert me["consent"] is None and me["admin"] is False
    assert c.post("/api/consent", headers=h, json={"terms_version": "old", "improve_data": True}).status_code == 400
    assert c.post("/api/consent", headers=h, json={"terms_version": me["terms_version"], "improve_data": True}).status_code == 200
    assert c.get("/api/me", headers=h).json()["consent"]["improve_data"] is True
    c.post("/api/brands", json={"name": "B"}, headers=h)
    assert c.post("/api/events", headers=h, json={"name": "page_view", "props": {"page": "plan"}}).status_code == 204
    assert c.post("/api/events", headers=h, json={"name": "made_up", "props": {}}).status_code == 204   # ignored
    assert c.get("/api/admin/metrics", headers=h).status_code == 404        # not an admin
    a = _signup(c, "boss@x.com")
    m = c.get("/api/admin/metrics", headers=a).json()
    funnel = dict(m["funnel_users"])
    assert funnel["signup"] == 1 and funnel["brand_created"] == 1 and funnel["page_view"] == 1
    assert "made_up" not in m["event_totals"]
    assert m["improve_data_opt_in"] == {"true": 1}


def test_failed_uploads_are_logged_without_customer_details(client):
    c, tmp = client
    h = _signup(c, "fail@x.com")
    bid = c.post("/api/brands", json={"name": "B"}, headers=h).json()["id"]
    bad = ("Customer Name,Mobile,Thing,When\n" + "".join(
        f"Ahmed {i},0101234{i:04d},Hoodie-{i % 3},someday\n" for i in range(30))).encode()
    r = c.post(f"/api/brands/{bid}/uploads", headers=h, files={"file": ("weird.csv", bad, "text/csv")})
    assert r.status_code == 422
    rid = r.json()["report_id"]
    a = _signup(c, "boss@x.com")
    assert c.get("/api/admin/uploads", headers=h).status_code == 404       # owners can't see the log
    logs = c.get("/api/admin/uploads", headers=a).json()
    f = next(x for x in logs if x["report_id"] == rid)
    assert f["type"] == "upload_failed" and f["ext"] == "csv" and f["error"] and f["shared"] is False
    cols = {x["name"]: x for x in f["shape"]["columns"]}
    assert cols["Customer Name"].get("personal") and cols["Mobile"].get("personal")
    assert cols["Thing"]["examples"][0].startswith("Hoodie")
    assert "Ahmed" not in str(logs) and "0101234" not in str(logs)
    # the owner chooses to share the file: a scrubbed copy is kept for the admin
    other = _signup(c, "other@x.com")
    assert c.post(f"/api/upload-reports/{rid}", headers=other, files={"file": ("weird.csv", bad)}).status_code == 404
    assert c.post(f"/api/upload-reports/{rid}", headers=h, files={"file": ("weird.csv", bad)}).status_code == 201
    assert next(x for x in c.get("/api/admin/uploads", headers=a).json() if x["report_id"] == rid)["shared"] is True
    assert c.get(f"/api/admin/upload-reports/{rid}", headers=h).status_code == 404
    got = c.get(f"/api/admin/upload-reports/{rid}", headers=a)
    assert got.status_code == 200
    text = got.content.decode()
    assert "Hoodie-1" in text and "Ahmed" not in text and "0101234" not in text


def test_reader_crash_is_logged_and_explained(client, monkeypatch):
    c, _ = client
    import fasla.main as main
    def boom(*a, **k):
        raise KeyError("lineitem_name")
    monkeypatch.setattr(main, "_smart_parse", boom)
    h = _signup(c, "crash@x.com")
    bid = c.post("/api/brands", json={"name": "B"}, headers=h).json()["id"]
    r = c.post(f"/api/brands/{bid}/uploads", headers=h, files={"file": ("orders_export.csv", CSV, "text/csv")})
    assert r.status_code == 422 and "our side" in r.json()["errors"][0]
    a = _signup(c, "boss@x.com")
    f = c.get("/api/admin/uploads", headers=a).json()[0]
    assert f["type"] == "upload_crashed" and "KeyError" in f["error"] and "main.py" in f["where"]
    assert "buyer1@mail.com" not in str(f)
