"""All settings come from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from .env without overriding real environment variables."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    # An empty value (or "-", the placeholder our AWS secrets are created with) counts as unset.
    v = (os.environ.get(name) or "").strip()
    return default if v in ("", "-") else v


@dataclass(frozen=True)
class Settings:
    # "supabase" in production; "dev" uses a built-in email/password login for local testing only
    auth_mode: str = _env("AUTH_MODE", "supabase")
    storage_mode: str = _env("STORAGE_MODE", "supabase")       # supabase | local
    supabase_url: str = _env("SUPABASE_URL").rstrip("/")
    supabase_anon_key: str = _env("SUPABASE_ANON_KEY")
    supabase_service_key: str = _env("SUPABASE_SERVICE_ROLE_KEY")
    supabase_jwt_secret: str = _env("SUPABASE_JWT_SECRET")      # only for projects still on the legacy HS256 secret
    supabase_bucket: str = _env("SUPABASE_BUCKET", "fasla-files")
    google_login: bool = _env("GOOGLE_LOGIN", "0") == "1"
    database_url: str = _env("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'fasla.db'}")
    local_storage_dir: str = _env("LOCAL_STORAGE_DIR", str(ROOT / "data" / "files"))
    dev_secret: str = _env("DEV_JWT_SECRET", "dev-only-secret-change-me-0123456789")
    max_upload_mb: int = int(_env("MAX_UPLOAD_MB", "10"))
    workers: int = int(_env("PLAN_WORKERS", "2"))
    solver_seconds: int = int(_env("SOLVER_SECONDS", "25"))
    public_url: str = _env("PUBLIC_URL", "")
    ai_url: str = _env("AI_URL", "").rstrip("/")                 # the AI sidecar, e.g. http://127.0.0.1:3001; empty = AI off
    ai_internal_key: str = _env("AI_INTERNAL_KEY", "")
    admin_emails: str = _env("ADMIN_EMAILS", "")                   # comma-separated; can see /api/admin/metrics
    plan_queue_url: str = _env("PLAN_QUEUE_URL", "")             # SQS: plans run on the worker service; empty = in-process
    aws_region: str = _env("AWS_REGION", "eu-north-1")
    # Shopify app (empty = the Shopify app is off). Client id/secret come from the Shopify Dev Dashboard.
    shopify_api_key: str = _env("SHOPIFY_API_KEY")
    shopify_api_secret: str = _env("SHOPIFY_API_SECRET")
    shopify_api_version: str = _env("SHOPIFY_API_VERSION", "2026-10")
    shopify_app_handle: str = _env("SHOPIFY_APP_HANDLE", "fasla")
    shopify_token_key: str = _env("SHOPIFY_TOKEN_KEY")          # Fernet key for stored tokens; derived if empty
    shopify_history_days: int = int(_env("SHOPIFY_HISTORY_DAYS", "365"))

    def validate(self) -> list[str]:
        probs = []
        if self.auth_mode == "supabase":
            for k, v in [("SUPABASE_URL", self.supabase_url), ("SUPABASE_ANON_KEY", self.supabase_anon_key)]:
                if not v:
                    probs.append(f"{k} is not set")
        if self.storage_mode == "supabase" and not self.supabase_service_key:
            probs.append("SUPABASE_SERVICE_ROLE_KEY is not set (needed for file storage)")
        if self.auth_mode == "dev" and (self.storage_mode == "supabase" or self.supabase_service_key):
            probs.append("AUTH_MODE=dev is for local testing only; use AUTH_MODE=supabase with Supabase keys")
        return probs


def get_settings() -> Settings:
    return Settings()
