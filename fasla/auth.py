"""Who is calling? Verifies Supabase access tokens (new asymmetric signing keys via JWKS, or the
legacy HS256 secret). AUTH_MODE=dev signs its own tokens for local testing."""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
import uuid

import httpx
import jwt
from fastapi import Depends, HTTPException, Request

from .config import Settings, get_settings


class AuthError(Exception):
    pass


_jwks_cache: dict = {"at": 0.0, "keys": None}
_lock = threading.Lock()


def _jwks(settings: Settings, client: httpx.Client | None = None):
    with _lock:
        if _jwks_cache["keys"] is not None and time.time() - _jwks_cache["at"] < 600:
            return _jwks_cache["keys"]
        c = client or httpx.Client(timeout=10)
        r = c.get(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json",
                  headers={"apikey": settings.supabase_anon_key})
        r.raise_for_status()
        _jwks_cache.update(at=time.time(), keys=r.json().get("keys", []))
        return _jwks_cache["keys"]


def verify_supabase_token(token: str, settings: Settings, client: httpx.Client | None = None) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise AuthError("Malformed token") from e
    alg = header.get("alg", "")
    opts = dict(audience="authenticated", issuer=f"{settings.supabase_url}/auth/v1", leeway=30)
    try:
        if alg == "HS256":
            if not settings.supabase_jwt_secret:
                raise AuthError("Project uses the legacy JWT secret; set SUPABASE_JWT_SECRET")
            claims = jwt.decode(token, settings.supabase_jwt_secret, algorithms=["HS256"], **opts)
        elif alg in ("ES256", "RS256", "EdDSA"):
            keys = _jwks(settings, client)
            key = next((k for k in keys if k.get("kid") == header.get("kid")), None)
            if key is None:
                _jwks_cache["keys"] = None                      # key rotated: refresh once
                keys = _jwks(settings, client)
                key = next((k for k in keys if k.get("kid") == header.get("kid")), None)
            if key is None:
                raise AuthError("Unknown signing key")
            claims = jwt.decode(token, jwt.PyJWK(key).key, algorithms=[alg], **opts)
        else:
            raise AuthError(f"Unsupported token algorithm {alg}")
    except jwt.ExpiredSignatureError as e:
        raise AuthError("Session expired, please sign in again") from e
    except jwt.PyJWTError as e:
        raise AuthError("Invalid token") from e
    if claims.get("role") != "authenticated":
        raise AuthError("Not a signed-in user")
    return claims


# ---------- dev mode ----------
def hash_pw(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return salt.hex() + "$" + dk.hex()


def check_pw(pw: str, stored: str) -> bool:
    salt, dk = stored.split("$")
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex(), dk)


def dev_token(user_id: str, email: str, settings: Settings, ttl: int = 3600 * 12) -> str:
    now = int(time.time())
    return jwt.encode({"sub": user_id, "email": email, "role": "authenticated", "aud": "authenticated",
                       "iss": "fasla-dev", "iat": now, "exp": now + ttl}, settings.dev_secret, algorithm="HS256")


def verify_dev_token(token: str, settings: Settings) -> dict:
    try:
        return jwt.decode(token, settings.dev_secret, algorithms=["HS256"], audience="authenticated", issuer="fasla-dev")
    except jwt.ExpiredSignatureError as e:
        raise AuthError("Session expired, please sign in again") from e
    except jwt.PyJWTError as e:
        raise AuthError("Invalid token") from e


class User:
    shop: str | None = None     # set for merchants using Fasla inside Shopify

    def __init__(self, id: uuid.UUID, email: str):
        self.id, self.email = id, email


def current_user(request: Request, settings: Settings = Depends(get_settings)) -> User:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        raise HTTPException(401, "Please sign in")
    token = h[7:].strip()
    if settings.shopify_api_key:
        from .shopify import ShopifyError, peek_audience, shop_user_id, verify_id_token
        if peek_audience(token) == settings.shopify_api_key:     # a Shopify admin session (embedded app)
            try:
                c = verify_id_token(token, settings.shopify_api_key, settings.shopify_api_secret)
            except ShopifyError as e:
                raise HTTPException(e.status, e.message)
            u = User(shop_user_id(c["shop"]), c["shop"])
            u.shop = c["shop"]
            return u
    try:
        claims = verify_dev_token(token, settings) if settings.auth_mode == "dev" else verify_supabase_token(token, settings)
        return User(uuid.UUID(claims["sub"]), claims.get("email", ""))
    except (AuthError, KeyError, ValueError) as e:
        raise HTTPException(401, str(e) or "Please sign in")
