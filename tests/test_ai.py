"""The app + the AI sidecar together (the sidecar runs with a deterministic fake model).
Needs Node and a built AI service (cd ai && npm ci && npm run build); skipped otherwise."""
from __future__ import annotations

import importlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
AI_MAIN = ROOT / "ai" / "dist" / "main.js"

ARABIC_CSV = (
    "رقم الطلب,تاريخ الطلب,اسم المنتج,المقاس,اللون,الكمية,السعر,المحافظة,حالة الطلب\n"
    + "".join(
        f"{1000 + i},2025-{9 + i % 4:02d}-{1 + i % 27:02d},تيشيرت أوفر سايز,{['S', 'M', 'L', 'XL'][i % 4]},"
        f"{'أسود' if i % 3 else 'أبيض'},1,550,{'القاهرة' if i % 2 else 'الجيزة'},"
        f"{'مرتجع' if i % 5 == 0 else 'استبدال مقاس' if i % 11 == 0 else 'تم التسليم'}\n"
        for i in range(260))
    + "".join(f"{2000 + i},2025-10-{1 + i % 27:02d},بنطلون كارجو,{['M', 'L'][i % 2]},زيتي,1,980,القاهرة,تم التسليم\n"
              for i in range(120))
).encode("utf-8")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def sidecar():
    if not AI_MAIN.exists():
        pytest.skip("AI service not built (cd ai && npm ci && npm run build)")
    port = _free_port()
    env = {**os.environ, "AI_PROVIDER": "fake", "AI_PORT": str(port), "AI_INTERNAL_KEY": "test-internal-key-1",
           "AI_DATABASE_URL": "", "DATABASE_URL": "", "AI_LIMIT_EXPLAIN": "2", "AI_LIMIT_ASK": "3", "AI_RATE_PER_MINUTE": "50"}
    p = subprocess.Popen(["node", str(AI_MAIN)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    import httpx
    for _ in range(50):
        try:
            if httpx.get(url + "/healthz", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        p.kill()
        pytest.fail("AI sidecar did not start: " + (p.stdout.read() or b"").decode()[-2000:])
    yield url
    p.terminate()
    p.wait(timeout=10)


@pytest.fixture(scope="module")
def client(sidecar, tmp_path_factory):
    d = tmp_path_factory.mktemp("ai")
    os.environ.update(AUTH_MODE="dev", STORAGE_MODE="local", DATABASE_URL=f"sqlite:///{d / 'test.db'}",
                      LOCAL_STORAGE_DIR=str(d / "files"), SOLVER_SECONDS="8", AI_URL=sidecar,
                      AI_INTERNAL_KEY="test-internal-key-1")
    for m in [m for m in sys.modules if m.startswith("fasla")]:
        del sys.modules[m]
    main = importlib.import_module("fasla.main")
    with TestClient(main.app) as c:
        yield c
    for k in ("AI_URL", "AI_INTERNAL_KEY"):
        os.environ.pop(k, None)


def _signup(c, email):
    r = c.post("/api/dev/signup", json={"email": email, "password": "correct horse 1"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _wait(c, pid, h):
    t0 = time.time()
    while True:
        p = c.get(f"/api/plans/{pid}", headers=h).json()
        if p["status"] in ("done", "failed") or time.time() - t0 > 300:
            return p
        time.sleep(1)


def test_arabic_file_is_read_with_ai_and_plan_can_be_explained_and_asked(client):
    h = _signup(client, "arabic@x.com")
    assert client.get("/api/ai/usage", headers=h).json()["enabled"] is True
    bid = client.post("/api/brands", json={"name": "براند"}, headers=h).json()["id"]
    r = client.post(f"/api/brands/{bid}/uploads", headers=h, files={"file": ("طلبات.csv", ARABIC_CSV, "text/csv")})
    assert r.status_code == 201, r.text
    j = r.json()
    st = j["checks"]["stats"]
    assert st["mode"] == "orders" and st["ai_mapped"] is True
    assert st["columns"]["date"] == "تاريخ_الطلب" and st["columns"]["style"] == "اسم_المنتج"
    assert {s["id"] for s in st["style_list"]} == {"تيشيرت أوفر سايز", "بنطلون كارجو"}
    assert j["settings"]["_mapping"]["status"]["مرتجع"] == "refused"
    pid = j["plan_id"]
    r = client.post(f"/api/plans/{pid}/run", headers=h, json={"settings": {
        "budget": 150000, "start_cash": 250000, "factory_moq": 0,
        "costs": {"تيشيرت أوفر سايز": 230, "بنطلون كارجو": 420}, "courier_fees": {"Your courier": 60},
        "_mapping": {"columns": {"date": "nonsense"}}}})          # users can't override the stored mapping
    assert r.status_code == 200, r.text
    p = _wait(client, pid, h)
    assert p["status"] == "done", p.get("error")
    assert 0.1 < p["view"]["refusal"]["overall"] < 0.3        # the AI-classified 'مرتجع' rows count as refused

    e1 = client.post(f"/api/plans/{pid}/explain", headers=h)
    assert e1.status_code == 200, e1.text
    assert e1.json()["text"].startswith("FAKE ANSWER") and e1.json()["usage"]["used"] == 1
    assert client.post(f"/api/plans/{pid}/explain", headers=h).json()["cached"] is True

    for i in range(3):
        a = client.post(f"/api/plans/{pid}/ask", headers=h, json={"question": f"Why so many tees? {i}"})
        assert a.status_code == 200, a.text
    over = client.post(f"/api/plans/{pid}/ask", headers=h, json={"question": "one more"})
    assert over.status_code == 429 and over.json()["code"] == "limit"
    u = client.get("/api/ai/usage", headers=h).json()
    assert u["ask"] == {"used": 3, "limit": 3} and u["explain"]["used"] == 1


def test_ai_endpoints_respect_ownership(client):
    h1 = _signup(client, "owner1@x.com")
    h2 = _signup(client, "owner2@x.com")
    bid = client.post("/api/brands", json={"name": "B"}, headers=h1).json()["id"]
    r = client.post(f"/api/brands/{bid}/uploads", headers=h1, files={"file": ("o.csv", ARABIC_CSV, "text/csv")})
    pid = r.json()["plan_id"]
    assert client.post(f"/api/plans/{pid}/explain", headers=h2).status_code == 404
    assert client.post(f"/api/plans/{pid}/explain", headers=h1).status_code == 409   # not planned yet
    assert client.post(f"/api/plans/{pid}/ask", headers=h1, json={"question": "x" * 501}).status_code == 422
