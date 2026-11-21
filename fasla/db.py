"""Database tables. In production this is Supabase Postgres (schema in supabase/migrations);
locally and in tests the same code runs on SQLite."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Column, DateTime, ForeignKey, MetaData, String, Table, Text, Uuid, create_engine,
                        event)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
J = JSON().with_variant(JSONB(), "postgresql")


def now():
    return datetime.now(timezone.utc)


brands = Table(
    "brands", metadata,
    Column("id", Uuid, primary_key=True, default=uuid.uuid4),
    Column("user_id", Uuid, nullable=False, index=True),
    Column("name", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, default=now),
)

plans = Table(
    "plans", metadata,
    Column("id", Uuid, primary_key=True, default=uuid.uuid4),
    Column("brand_id", Uuid, ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("user_id", Uuid, nullable=False, index=True),
    Column("status", String(16), nullable=False, default="draft"),   # draft | queued | running | done | failed
    Column("input_name", Text),
    Column("input_path", Text),
    Column("report_path", Text),
    Column("settings", J),
    Column("checks", J),
    Column("summary", J),
    Column("view", J),
    Column("error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, default=now),
    Column("finished_at", DateTime(timezone=True)),
)

# only used with AUTH_MODE=dev (local testing); production accounts live in Supabase Auth
dev_users = Table(
    "dev_users", metadata,
    Column("id", Uuid, primary_key=True, default=uuid.uuid4),
    Column("email", String(320), nullable=False, unique=True),
    Column("pw_hash", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, default=now),
)


def make_engine(url: str):
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    kw = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False}
    eng = create_engine(url, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _fk(dbapi_con, _):
            dbapi_con.execute("PRAGMA foreign_keys=ON")
        metadata.create_all(eng)          # Postgres tables come from the Supabase migration
    return eng
