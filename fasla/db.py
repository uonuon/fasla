"""Database tables. In production this is Supabase Postgres (schema in supabase/migrations);
locally and in tests the same code runs on SQLite."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, Text,
                        Uuid, create_engine, event, text)
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


# Server-only tables (Postgres schema fasla_app, not exposed through Supabase's public API).
# They create themselves at startup, so no manual SQL step is needed.
PRIVATE = "fasla_app"
private = MetaData(schema=PRIVATE)
BigId = BigInteger().with_variant(Integer(), "sqlite")   # SQLite only auto-increments INTEGER keys

events = Table(
    "events", private,
    Column("id", BigId, primary_key=True, autoincrement=True),
    Column("user_id", Uuid, index=True),
    Column("name", String(40), nullable=False, index=True),
    Column("props", J),
    Column("created_at", DateTime(timezone=True), nullable=False, default=now, index=True),
)

consents = Table(
    "consents", private,
    Column("user_id", Uuid, primary_key=True),
    Column("terms_version", String(20), nullable=False),
    Column("improve_data", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=False, default=now),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=now),
)


shopify_shops = Table(
    "shopify_shops", private,
    Column("shop", String(120), primary_key=True),
    Column("user_id", Uuid, nullable=False, index=True),
    Column("access_token", Text),                  # encrypted
    Column("access_expires_at", DateTime(timezone=True)),
    Column("refresh_token", Text),                 # encrypted
    Column("refresh_expires_at", DateTime(timezone=True)),
    Column("scope", Text),
    Column("installed_at", DateTime(timezone=True), nullable=False, default=now),
    Column("uninstalled_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=now),
)


def make_engine(url: str):
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    kw = {"pool_pre_ping": True}
    if url.startswith("postgresql"):
        # fail fast (instead of hanging past the load balancer's health check) when the database is unreachable
        kw["connect_args"] = {"connect_timeout": 10}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False}
    eng = create_engine(url, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _fk(dbapi_con, _):
            dbapi_con.execute("PRAGMA foreign_keys=ON")
        metadata.create_all(eng)          # Postgres tables come from the Supabase migration
        eng = eng.execution_options(schema_translate_map={PRIVATE: None})   # SQLite has no schemas
        private.create_all(eng)
    return eng


def init_private(eng) -> None:
    """Create the server-only schema and tables on Postgres (idempotent). Called at startup."""
    if eng.dialect.name != "postgresql":
        return
    with eng.begin() as con:
        con.execute(text(f"create schema if not exists {PRIVATE}"))
        con.execute(text(f"""do $$ begin
            if exists (select 1 from pg_roles where rolname = 'anon') then
                execute 'revoke all on schema {PRIVATE} from anon, authenticated';
            end if; end $$;"""))
    # supabase/migrations/0003: Shopify shops are users without a Supabase login (idempotent).
    # Never block startup on it: if the role can't alter the tables, log it and run the SQL file by hand.
    try:
        with eng.begin() as con:
            con.execute(text("""do $$ begin
                if to_regclass('public.brands') is not null then
                    alter table public.brands drop constraint if exists brands_user_id_fkey;
                end if;
                if to_regclass('public.plans') is not null then
                    alter table public.plans drop constraint if exists plans_user_id_fkey;
                end if; end $$;"""))
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger("fasla").warning("Couldn't apply migration 0003 (run it in the SQL editor): %s", str(e)[:200])
    private.create_all(eng)
