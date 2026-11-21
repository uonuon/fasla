"""Supabase token verification and Storage REST calls, against mocked Supabase endpoints."""
import json
import time

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import ec

from fasla import auth
from fasla.config import Settings
from fasla.storage import StorageError, SupabaseStorage

URL = "https://abc.supabase.co"


def settings(**kw):
    base = dict(auth_mode="supabase", supabase_url=URL, supabase_anon_key="anon", supabase_jwt_secret="")
    base.update(kw)
    return Settings(**base)


def claims(**kw):
    now = int(time.time())
    c = dict(sub="2b1f0c0e-1111-4c4c-9999-123456789abc", email="a@x.com", role="authenticated",
             aud="authenticated", iss=f"{URL}/auth/v1", iat=now, exp=now + 3600)
    c.update(kw)
    return c


@pytest.fixture
def es_key():
    k = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(k.public_key()))
    jwk.update(kid="key-1", alg="ES256", use="sig")
    auth._jwks_cache.update(at=0.0, keys=None)
    return k, jwk


@respx.mock
def test_es256_token_via_jwks(es_key):
    k, jwk = es_key
    respx.get(f"{URL}/auth/v1/.well-known/jwks.json").mock(return_value=httpx.Response(200, json={"keys": [jwk]}))
    tok = jwt.encode(claims(), k, algorithm="ES256", headers={"kid": "key-1"})
    c = auth.verify_supabase_token(tok, settings())
    assert c["sub"].startswith("2b1f0c0e")


@respx.mock
def test_key_rotation_refreshes_jwks(es_key):
    k, jwk = es_key
    k2 = ec.generate_private_key(ec.SECP256R1())
    jwk2 = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(k2.public_key()))
    jwk2.update(kid="key-2", alg="ES256")
    route = respx.get(f"{URL}/auth/v1/.well-known/jwks.json")
    route.side_effect = [httpx.Response(200, json={"keys": [jwk]}), httpx.Response(200, json={"keys": [jwk, jwk2]})]
    auth.verify_supabase_token(jwt.encode(claims(), k, algorithm="ES256", headers={"kid": "key-1"}), settings())
    c = auth.verify_supabase_token(jwt.encode(claims(), k2, algorithm="ES256", headers={"kid": "key-2"}), settings())
    assert c["role"] == "authenticated" and route.call_count == 2


def test_hs256_legacy_secret():
    s = settings(supabase_jwt_secret="super-secret-jwt-token-with-at-least-32-characters")
    tok = jwt.encode(claims(), s.supabase_jwt_secret, algorithm="HS256")
    assert auth.verify_supabase_token(tok, s)["email"] == "a@x.com"
    with pytest.raises(auth.AuthError):
        auth.verify_supabase_token(jwt.encode(claims(), "wrong-secret-wrong-secret-wrong-secret!", algorithm="HS256"), s)


@pytest.mark.parametrize("bad,msg", [
    (dict(exp=int(time.time()) - 100), "expired"),
    (dict(aud="something-else"), "Invalid"),
    (dict(iss="https://evil.supabase.co/auth/v1"), "Invalid"),
    (dict(role="anon"), "signed-in"),
])
def test_rejects_bad_tokens(bad, msg):
    s = settings(supabase_jwt_secret="super-secret-jwt-token-with-at-least-32-characters")
    tok = jwt.encode(claims(**bad), s.supabase_jwt_secret, algorithm="HS256")
    with pytest.raises(auth.AuthError, match=msg):
        auth.verify_supabase_token(tok, s)


def test_rejects_unsigned_token():
    tok = jwt.encode(claims(), None, algorithm="none")
    with pytest.raises(auth.AuthError):
        auth.verify_supabase_token(tok, settings())


@respx.mock
def test_storage_put_get_delete():
    st = SupabaseStorage(URL, "service", "fasla-files")
    put = respx.post(f"{URL}/storage/v1/object/fasla-files/u1/b1/p1/input.xlsx").mock(return_value=httpx.Response(200, json={"Key": "x"}))
    get = respx.get(f"{URL}/storage/v1/object/fasla-files/u1/b1/p1/input.xlsx").mock(return_value=httpx.Response(200, content=b"PKdata"))
    dele = respx.delete(f"{URL}/storage/v1/object/fasla-files").mock(return_value=httpx.Response(200, json=[]))
    st.put("u1/b1/p1/input.xlsx", b"PKdata", "application/x")
    assert put.calls[0].request.headers["authorization"] == "Bearer service"
    assert put.calls[0].request.headers["x-upsert"] == "true"
    assert st.get("u1/b1/p1/input.xlsx") == b"PKdata"
    st.delete(["u1/b1/p1/input.xlsx"])
    assert json.loads(dele.calls[0].request.content) == {"prefixes": ["u1/b1/p1/input.xlsx"]}


@respx.mock
def test_storage_errors():
    st = SupabaseStorage(URL, "service", "fasla-files")
    respx.get(f"{URL}/storage/v1/object/fasla-files/nope.xlsx").mock(return_value=httpx.Response(400, json={"error": "not_found", "message": "Object not found"}))
    respx.post(f"{URL}/storage/v1/object/fasla-files/big.xlsx").mock(return_value=httpx.Response(413, text="Payload too large"))
    with pytest.raises(StorageError, match="not found"):
        st.get("nope.xlsx")
    with pytest.raises(StorageError, match="Upload failed"):
        st.put("big.xlsx", b"x", "a")
