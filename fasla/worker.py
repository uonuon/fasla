"""Planning worker: reads plan jobs from SQS and runs them.

Runs as its own ECS service (same image, `python -m fasla.worker`). It starts when a job arrives
(a CloudWatch alarm on the queue scales the service from 0 to 1) and scales itself back to 0 after
IDLE_MINUTES with nothing to do, so an idle worker costs nothing.

Retries: SQS hands a message out again if the worker dies mid-plan. On the 3rd attempt we mark
the plan failed instead of trying again, so a plan never stays "running" forever.
"""
from __future__ import annotations

import logging
import os
import signal
import time
import uuid

from sqlalchemy import select

log = logging.getLogger("fasla.worker")
MAX_ATTEMPTS = 3
IDLE_MINUTES = float(os.environ.get("WORKER_IDLE_MINUTES", "10"))


class Worker:
    def __init__(self, queue, run_job, fail, plan_status, scale_to_zero=None, idle_minutes: float = IDLE_MINUTES,
                 clock=time.monotonic):
        self.queue, self.run_job, self.fail, self.plan_status = queue, run_job, fail, plan_status
        self.scale_to_zero = scale_to_zero
        self.idle_seconds = idle_minutes * 60
        self.clock = clock
        self.last_work = clock()
        self.stopping = False

    def handle(self, msg) -> None:
        try:
            pid = uuid.UUID(msg.plan_id)
            uid = uuid.UUID(msg.user_id)
        except ValueError:
            log.warning("dropping malformed job %s", msg)
            self.queue.delete(msg)
            return
        status = self.plan_status(pid)
        if status not in ("queued", "running"):        # deleted, already done, or re-run by the user
            self.queue.delete(msg)
            return
        if msg.receive_count >= MAX_ATTEMPTS:
            self.fail(pid, "Planning stopped unexpectedly twice. Please try again, or contact us if it keeps happening.")
            self.queue.delete(msg)
            return
        log.info("planning %s (attempt %s)", pid, msg.receive_count)
        t0 = time.time()
        self.run_job(pid, uid)                          # marks the plan done or failed itself
        self.queue.delete(msg)
        log.info("planned %s in %.0fs", pid, time.time() - t0)

    def run_once(self, wait_seconds: int = 20) -> bool:
        msgs = self.queue.receive(wait_seconds)
        for m in msgs:
            self.handle(m)
            self.last_work = self.clock()
        return bool(msgs)

    def idle_too_long(self) -> bool:
        return self.clock() - self.last_work >= self.idle_seconds

    def loop(self) -> None:
        while not self.stopping:
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 — keep the worker alive; SQS will hand the job out again
                log.exception("worker iteration failed")
                time.sleep(5)
                continue
            if self.scale_to_zero and self.idle_too_long():
                try:
                    if self.queue.backlog() == 0:
                        log.info("idle for %.0f min with an empty queue: scaling to zero", self.idle_seconds / 60)
                        self.scale_to_zero()
                        self.last_work = self.clock()   # ECS stops us shortly; don't hammer the API meanwhile
                except Exception:  # noqa: BLE001
                    log.exception("scale-to-zero failed")
                    self.last_work = self.clock()


def ecs_scale_to_zero():
    cluster, service = os.environ.get("WORKER_CLUSTER"), os.environ.get("WORKER_SERVICE")
    if not (cluster and service):
        return None
    import boto3
    ecs = boto3.client("ecs", region_name=os.environ.get("AWS_REGION", "eu-north-1"))
    return lambda: ecs.update_service(cluster=cluster, service=service, desiredCount=0)


def main():
    logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    from . import main as app
    from .db import init_private
    init_private(app.engine)
    if app.plan_queue is None:
        raise SystemExit("PLAN_QUEUE_URL is not set: nothing to work on")

    def status(pid):
        with app.engine.connect() as con:
            return con.execute(select(app.plans.c.status).where(app.plans.c.id == pid)).scalar()

    w = Worker(app.plan_queue, app._job, app._fail, status, ecs_scale_to_zero())

    def stop(*_):
        log.info("stopping after the current job")
        w.stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("worker started")
    w.loop()


if __name__ == "__main__":
    main()
