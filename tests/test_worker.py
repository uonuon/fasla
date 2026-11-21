"""The planning queue and worker, with an in-memory queue (no AWS)."""
from __future__ import annotations

import importlib
import os
import sys
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from fasla.queue import Message, MemoryQueue
from fasla.worker import MAX_ATTEMPTS, Worker


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _worker(queue, status="queued", **kw):
    ran, failed, scaled = [], [], []
    w = Worker(queue, lambda p, u: ran.append(p), lambda p, m: failed.append((p, m)),
               lambda p: status, scale_to_zero=lambda: scaled.append(1), **kw)
    return w, ran, failed, scaled


def test_runs_job_and_deletes_message():
    q = MemoryQueue()
    pid, uid = uuid.uuid4(), uuid.uuid4()
    q.send(str(pid), str(uid))
    w, ran, failed, _ = _worker(q)
    assert w.run_once(0) is True
    assert ran == [pid] and not failed and q.backlog() == 0


def test_skips_plans_that_are_no_longer_waiting():
    q = MemoryQueue()
    q.send(str(uuid.uuid4()), str(uuid.uuid4()))
    w, ran, _, _ = _worker(q, status="done")
    w.run_once(0)
    assert ran == [] and q.backlog() == 0


def test_gives_up_after_repeated_crashes():
    q = MemoryQueue()
    pid = uuid.uuid4()
    w, ran, failed, _ = _worker(q)
    w.handle(Message(str(pid), str(uuid.uuid4()), "h1", receive_count=MAX_ATTEMPTS))
    assert ran == [] and failed and failed[0][0] == pid


def test_drops_malformed_messages():
    q = MemoryQueue()
    q.send("not-a-uuid", "x")
    w, ran, failed, _ = _worker(q)
    w.run_once(0)
    assert ran == [] and not failed and q.backlog() == 0


def test_scales_to_zero_only_when_idle_and_queue_empty():
    q = MemoryQueue()
    clock = FakeClock()
    w, _, _, scaled = _worker(q, idle_minutes=10, clock=clock)
    clock.t = 9 * 60
    assert not w.idle_too_long()
    clock.t = 10 * 60
    assert w.idle_too_long()
    q.send(str(uuid.uuid4()), str(uuid.uuid4()))
    w.run_once(0)                       # work resets the idle timer
    assert not w.idle_too_long()


@pytest.fixture()
def queued_client(tmp_path, monkeypatch):
    os.environ.update(AUTH_MODE="dev", STORAGE_MODE="local", DATABASE_URL=f"sqlite:///{tmp_path / 'q.db'}",
                      LOCAL_STORAGE_DIR=str(tmp_path / "files"), SOLVER_SECONDS="8")
    os.environ.pop("AI_URL", None)
    for m in [m for m in sys.modules if m.startswith("fasla") and m not in ("fasla.queue", "fasla.worker")]:
        del sys.modules[m]
    main = importlib.import_module("fasla.main")
    q = MemoryQueue()
    monkeypatch.setattr(main, "plan_queue", q)
    with TestClient(main.app) as c:
        yield c, main, q


def test_web_app_queues_plans_and_worker_runs_them(queued_client):
    c, main, q = queued_client
    h = {"Authorization": "Bearer " + c.post("/api/dev/signup", json={"email": "q@x.com", "password": "correct horse 1"}).json()["access_token"]}
    bid = c.post("/api/brands", json={"name": "Q"}, headers=h).json()["id"]
    up = c.post(f"/api/brands/{bid}/uploads", headers=h,
                files={"file": ("ex.xlsx", c.get("/api/templates/example").content)}).json()
    pid = up["plan_id"]
    r = c.post(f"/api/plans/{pid}/run", json={"settings": {}}, headers=h)
    assert r.status_code == 200 and r.json()["queued"] is True
    assert c.get(f"/api/plans/{pid}", headers=h).json()["status"] == "queued"
    assert q.backlog() == 1                                  # nothing ran in the web process

    def status(p):
        from sqlalchemy import select
        with main.engine.connect() as con:
            return con.execute(select(main.plans.c.status).where(main.plans.c.id == p)).scalar()

    w = Worker(q, main._job, main._fail, status)
    t0 = time.time()
    assert w.run_once(0)
    p = c.get(f"/api/plans/{pid}", headers=h).json()
    assert p["status"] == "done", p.get("error")
    assert q.backlog() == 0
    assert time.time() - t0 < 300
