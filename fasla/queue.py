"""The planning queue. In AWS it's an SQS queue read by the worker service (fasla/worker.py);
locally and in tests there is no queue and plans run on a thread inside the web app."""
from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass


@dataclass
class Message:
    plan_id: str
    user_id: str
    handle: str          # what delete() needs
    receive_count: int = 1


class Queue:
    def send(self, plan_id: str, user_id: str) -> None: ...
    def receive(self, wait_seconds: int = 20) -> list[Message]: ...
    def delete(self, msg: Message) -> None: ...
    def backlog(self) -> int: ...   # visible + in flight


class SqsQueue(Queue):
    def __init__(self, url: str, region: str | None = None):
        import boto3
        from botocore.config import Config
        self.url = url
        self.sqs = boto3.client("sqs", region_name=region,
                                config=Config(retries={"max_attempts": 5, "mode": "adaptive"}, read_timeout=30))

    def send(self, plan_id: str, user_id: str) -> None:
        self.sqs.send_message(QueueUrl=self.url, MessageBody=json.dumps({"plan_id": plan_id, "user_id": user_id}))

    def receive(self, wait_seconds: int = 20) -> list[Message]:
        r = self.sqs.receive_message(QueueUrl=self.url, MaxNumberOfMessages=1, WaitTimeSeconds=wait_seconds,
                                     MessageSystemAttributeNames=["ApproximateReceiveCount"])
        out = []
        for m in r.get("Messages", []):
            try:
                body = json.loads(m["Body"])
                out.append(Message(str(body["plan_id"]), str(body["user_id"]), m["ReceiptHandle"],
                                   int(m.get("Attributes", {}).get("ApproximateReceiveCount", 1))))
            except (ValueError, KeyError):          # not ours: drop it so it can't block the queue
                self.sqs.delete_message(QueueUrl=self.url, ReceiptHandle=m["ReceiptHandle"])
        return out

    def delete(self, msg: Message) -> None:
        self.sqs.delete_message(QueueUrl=self.url, ReceiptHandle=msg.handle)

    def backlog(self) -> int:
        a = self.sqs.get_queue_attributes(QueueUrl=self.url, AttributeNames=[
            "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        return int(a["ApproximateNumberOfMessages"]) + int(a["ApproximateNumberOfMessagesNotVisible"])


class MemoryQueue(Queue):
    """For tests: same behaviour as SQS minus durability."""
    def __init__(self):
        self.items: deque[Message] = deque()
        self.inflight: dict[str, Message] = {}
        self._n = 0
        self._lock = threading.Lock()

    def send(self, plan_id: str, user_id: str) -> None:
        with self._lock:
            self._n += 1
            self.items.append(Message(plan_id, user_id, f"h{self._n}"))

    def receive(self, wait_seconds: int = 0) -> list[Message]:
        with self._lock:
            if not self.items:
                return []
            m = self.items.popleft()
            self.inflight[m.handle] = m
            return [m]

    def delete(self, msg: Message) -> None:
        with self._lock:
            self.inflight.pop(msg.handle, None)

    def backlog(self) -> int:
        return len(self.items) + len(self.inflight)


def make_queue(url: str, region: str | None = None) -> Queue | None:
    return SqsQueue(url, region) if url else None
