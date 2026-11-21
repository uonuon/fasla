"""File storage: Supabase Storage (private bucket) in production, a local folder for testing.
Paths always start with the owner's user id."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import httpx

from .config import Settings


class StorageError(Exception):
    pass


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, path: str) -> Path:
        p = (self.root / path).resolve()
        if self.root.resolve() not in p.parents:
            raise StorageError("Bad path")
        return p

    def put(self, path: str, data: bytes, content_type: str):
        p = self._p(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, path: str) -> bytes:
        p = self._p(path)
        if not p.exists():
            raise StorageError("File not found")
        return p.read_bytes()

    def delete(self, paths: list[str]):
        for path in paths:
            p = self._p(path)
            if p.exists():
                p.unlink()


class SupabaseStorage:
    def __init__(self, url: str, service_key: str, bucket: str, client: httpx.Client | None = None):
        self.base = f"{url}/storage/v1"
        self.bucket = bucket
        self.h = {"Authorization": f"Bearer {service_key}", "apikey": service_key}
        self.c = client or httpx.Client(timeout=60)

    def _u(self, path: str) -> str:
        return f"{self.base}/object/{self.bucket}/{quote(path)}"

    def put(self, path: str, data: bytes, content_type: str):
        r = self.c.post(self._u(path), content=data, headers={**self.h, "Content-Type": content_type, "x-upsert": "true"})
        if r.status_code >= 300:
            raise StorageError(f"Upload failed ({r.status_code}): {r.text[:200]}")

    def get(self, path: str) -> bytes:
        r = self.c.get(self._u(path), headers=self.h)
        if r.status_code == 404 or (r.status_code == 400 and "not found" in r.text.lower()):
            raise StorageError("File not found")
        if r.status_code >= 300:
            raise StorageError(f"Download failed ({r.status_code})")
        return r.content

    def delete(self, paths: list[str]):
        if not paths:
            return
        r = self.c.request("DELETE", f"{self.base}/object/{self.bucket}", json={"prefixes": paths}, headers=self.h)
        if r.status_code >= 300:
            raise StorageError(f"Delete failed ({r.status_code})")


def make_storage(s: Settings):
    if s.storage_mode == "local":
        return LocalStorage(s.local_storage_dir)
    return SupabaseStorage(s.supabase_url, s.supabase_service_key, s.supabase_bucket)
