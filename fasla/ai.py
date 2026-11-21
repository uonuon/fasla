"""Client for the AI service (the Node sidecar in ai/). It runs next to this app on localhost.

The AI service owns the models, the usage ledger, the per-user allowances and the monthly
budget cap. This app authenticates the user and only ever sends that user's own data.
Every call degrades gracefully: if the AI service is off, unreachable or paused, Fasla keeps
working without it and the caller gets an AIError with a message to show.
"""
from __future__ import annotations

import logging

import httpx

from .config import get_settings

log = logging.getLogger("fasla.ai")


class AIError(Exception):
    def __init__(self, message: str, status: int = 503, body: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.body = body or {}


def enabled() -> bool:
    return bool(get_settings().ai_url)


def _post(path: str, payload: dict, timeout: float = 60.0) -> dict:
    s = get_settings()
    if not s.ai_url:
        raise AIError("AI features aren't switched on here.")
    try:
        r = httpx.post(f"{s.ai_url}{path}", json=payload, timeout=timeout,
                       headers={"X-Internal-Key": s.ai_internal_key})
    except httpx.HTTPError as e:
        log.warning("AI service unreachable: %s", e)
        raise AIError("The AI assistant isn't reachable right now. Everything else still works.")
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400:
        raise AIError(body.get("message") or "The AI assistant couldn't answer. Please try again.", r.status_code, body)
    return body


def _get(path: str, params: dict) -> dict:
    s = get_settings()
    if not s.ai_url:
        raise AIError("AI features aren't switched on here.")
    try:
        r = httpx.get(f"{s.ai_url}{path}", params=params, timeout=10.0, headers={"X-Internal-Key": s.ai_internal_key})
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise AIError("The AI assistant isn't reachable right now.") from e


def usage(user_id: str) -> dict:
    return _get("/v1/usage", {"userId": user_id})


def map_columns(user_id: str, sample: dict, fields: list[str]) -> dict:
    return _post("/v1/map-columns", {"userId": user_id, **sample, "fields": fields}, timeout=45)


def map_statuses(user_id: str, values: list[str]) -> dict:
    return _post("/v1/map-statuses", {"userId": user_id, "values": values}, timeout=45)


def explain(user_id: str, plan_key: str, context: dict) -> dict:
    return _post("/v1/explain", {"userId": user_id, "planKey": plan_key, "context": context}, timeout=90)


def ask(user_id: str, plan_key: str, context: dict, question: str, history: list[dict]) -> dict:
    return _post("/v1/ask", {"userId": user_id, "planKey": plan_key, "context": context,
                             "question": question, "history": history}, timeout=60)
