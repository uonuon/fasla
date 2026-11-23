"""Fasla as a Shopify app: session tokens, token exchange/refresh, order import, webhooks, data deletion.
Shopify itself is faked with httpx.MockTransport."""
import base64
import hashlib
import hmac
import importlib
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

KEY, SECRET, SHOP = "test-client-id", "test-client-secret-0123456789-abcdefgh", "nile-thread.myshopify.com"


def id_token(shop=SHOP, aud=KEY, secret=SECRET, exp_in=60, dest=None):
    now = int(time.time())
    return jwt.encode({"iss": f"https://{shop}/admin", "dest": dest or f"https://{shop}", "aud": aud, "sub": "42",
                       "exp": now + exp_in, "nbf": now - 5, "iat": now, "jti": "x", "sid": "s"}, secret, algorithm="HS256")


def orders_page(n_orders=400, seed=1):
    import random
    rnd = random.Random(seed)
    styles = [("Linen Shirt", "Shirts", 650, 260), ("Wide Pants", "Pants", 780, 300), ("Basic Tee", "Tees", 350, 120)]
    start = date.today() - timedelta(days=360)
    nodes = []
    for i in range(n_orders):
        d = start + timedelta(days=int(i * 360 / n_orders))
        lis = []
        for _ in range(rnd.choice([1, 1, 2])):
            title, ptype, price, cost = rnd.choice(styles)
            size, color = rnd.choice(["S", "M", "L", "XL"]), rnd.choice(["Black", "White", "Beige"])
            q = rnd.choice([1, 1, 2])
            lis.append({"title": title, "quantity": q, "currentQuantity": 0 if rnd.random() < 0.08 else q, "sku": None,
                        "variantTitle": f"{size} / {color}", "originalUnitPriceSet": {"shopMoney": {"amount": str(price)}},
                        "product": {"id": f"gid://shopify/Product/{title}", "title": title, "productType": ptype},
                        "variant": {"selectedOptions": [{"name": "Size", "value": size}, {"name": "Color", "value": color}],
                                    "inventoryItem": {"unitCost": {"amount": str(cost)}}}})
        nodes.append({"name": f"#{1000 + i}", "createdAt": f"{d.isoformat()}T10:00:00Z",
                      "cancelledAt": f"{d.isoformat()}T12:00:00Z" if rnd.random() < 0.03 else None,
                      "displayFulfillmentStatus": "FULFILLED", "lineItems": {"nodes": lis}})
    return nodes


class FakeShopify:
    def __init__(self):
        self.calls = []
        self.nodes = orders_page()
        self.exchange_count = 0
        self.refresh_count = 0
        self.expires_in = 3600

    def handler(self, request: httpx.Request):
        self.calls.append(str(request.url))
        if request.url.path == "/admin/oauth/access_token":
            body = json.loads(request.content)
            assert body["client_id"] == KEY and body["client_secret"] == SECRET
            if body["grant_type"] == "refresh_token":
                self.refresh_count += 1
                assert body["refresh_token"].startswith("shprt_")
            else:
                self.exchange_count += 1
                assert body["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
                assert body["requested_token_type"].endswith("offline-access-token") and body["expiring"] == "1"
                jwt.decode(body["subject_token"], SECRET, algorithms=["HS256"], audience=KEY)
            return httpx.Response(200, json={"access_token": f"shpat_{self.exchange_count}_{self.refresh_count}",
                                             "scope": "read_orders,read_all_orders,read_products,read_inventory",
                                             "expires_in": self.expires_in, "refresh_token": f"shprt_{self.refresh_count}",
                                             "refresh_token_expires_in": 7776000})
        assert request.url.path.endswith("/graphql.json")
        assert request.headers["X-Shopify-Access-Token"].startswith("shpat_")
        q = json.loads(request.content)
        if "currentAppInstallation" in q["query"]:
            return httpx.Response(200, json={"data": {"shop": {"name": "Nile Thread", "currencyCode": "EGP", "ianaTimezone": "Africa/Cairo"},
                                                      "currentAppInstallation": {"activeSubscriptions": []}}})
        assert "customer" not in q["query"].lower() and "email" not in q["query"].lower() and "address" not in q["query"].lower()
        after = int(q["variables"].get("after") or 0)
        page = self.nodes[after:after + 100]
        nxt = after + 100 < len(self.nodes)
        return httpx.Response(200, json={"data": {"orders": {"nodes": page, "pageInfo": {"hasNextPage": nxt, "endCursor": str(after + 100)}}},
                                         "extensions": {"cost": {"throttleStatus": {"currentlyAvailable": 1000, "restoreRate": 100}}}})


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    fake = FakeShopify()
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: real_client(transport=httpx.MockTransport(fake.handler)))
    os.environ.update(AUTH_MODE="dev", STORAGE_MODE="local", DATABASE_URL=f"sqlite:///{tmp_path / 't.db'}",
                      LOCAL_STORAGE_DIR=str(tmp_path / "files"), SOLVER_SECONDS="6", SHOPIFY_API_KEY=KEY,
                      SHOPIFY_API_SECRET=SECRET, SHOPIFY_TOKEN_KEY="")
    for m in [m for m in sys.modules if m.startswith("fasla")]:
        del sys.modules[m]
    main = importlib.import_module("fasla.main")
    with TestClient(main.app) as c:
        yield c, fake, main
    for k in ("SHOPIFY_API_KEY", "SHOPIFY_API_SECRET", "SHOPIFY_TOKEN_KEY"):
        os.environ.pop(k, None)
    for m in [m for m in sys.modules if m.startswith("fasla")]:
        del sys.modules[m]


def H(tok=None):
    return {"Authorization": f"Bearer {tok or id_token()}"}


def test_id_token_rules():
    from fasla.shopify import ShopifyError, verify_id_token
    assert verify_id_token(id_token(), KEY, SECRET)["shop"] == SHOP
    for bad in (id_token(secret="wrong-secret-xxxxxxxxxxxxxxxx"), id_token(aud="other"), id_token(exp_in=-60),
                id_token(dest="https://other.myshopify.com"), id_token(shop="evil.com")):
        with pytest.raises(ShopifyError):
            verify_id_token(bad, KEY, SECRET)


def test_order_rows_privacy_and_options():
    from fasla.shopify import CSV_COLUMNS, order_rows, rows_to_csv
    rows = order_rows(orders_page(20))
    assert rows and set(rows[0]) <= set(CSV_COLUMNS)
    assert {r["size"] for r in rows} <= {"S", "M", "L", "XL"} and {r["color"] for r in rows} <= {"Black", "White", "Beige"}
    csv_text = rows_to_csv(rows).decode()
    assert "email" not in csv_text.lower() and "phone" not in csv_text.lower()
    # a fully returned line is marked returned
    o = orders_page(1)[0]
    o["lineItems"]["nodes"] = o["lineItems"]["nodes"][:1]
    o["lineItems"]["nodes"][0]["currentQuantity"] = 0
    o["cancelledAt"] = None
    assert order_rows([o])[0]["status"] == "returned"


def test_full_flow_install_import_plan(app_env):
    c, fake, main = app_env
    assert c.get("/shopify/?shop=" + SHOP).status_code == 200
    assert "frame-ancestors https://admin.shopify.com https://" + SHOP in c.get("/shopify/?shop=" + SHOP).headers["content-security-policy"]
    assert KEY in c.get("/shopify/").text
    r = c.get("/api/shopify/session", headers=H())
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["name"] == "Nile Thread" and s["plan"] is None and "pricing_plans" in s["pricing_url"]
    assert fake.exchange_count == 1
    # stored token is encrypted
    with main.engine.connect() as con:
        row = con.execute(main.select(main.shopify_shops)).mappings().first()
    assert row["shop"] == SHOP and not row["access_token"].startswith("shpat_")
    # second call reuses the stored token (no new exchange)
    assert c.get("/api/shopify/session", headers=H()).status_code == 200 and fake.exchange_count == 1
    r = c.post("/api/shopify/import", json={"days": 365}, headers=H())
    assert r.status_code == 201, r.text
    body = r.json()
    st = body["checks"]["stats"]
    assert st["orders"] > 300 and st["styles"] == 3 and all(x["cost"] for x in st["style_list"])
    pid = body["plan_id"]
    r = c.post(f"/api/plans/{pid}/run", json={"settings": {}}, headers=H())
    assert r.status_code == 200, r.text
    for _ in range(120):
        p = c.get(f"/api/plans/{pid}", headers=H()).json()
        if p["status"] in ("done", "failed"):
            break
        time.sleep(1)
    assert p["status"] == "done", p.get("error")
    assert p["view"]["variants"][p["view"]["base"]]["units"] > 0
    assert c.get(f"/api/plans/{pid}/report", headers=H()).content[:2] == b"PK"
    # another shop can't see this plan
    assert c.get(f"/api/plans/{pid}", headers=H(id_token(shop="other-shop.myshopify.com"))).status_code == 404


def test_token_refresh_when_expired(app_env):
    c, fake, main = app_env
    fake.expires_in = 60          # access token that is already inside the 2-minute safety margin
    assert c.get("/api/shopify/session", headers=H()).status_code == 200
    assert fake.exchange_count == 1 and fake.refresh_count == 0
    assert c.get("/api/shopify/session", headers=H()).status_code == 200
    assert fake.refresh_count == 1 and fake.exchange_count == 1


def test_regular_users_and_bad_tokens(app_env):
    c, fake, main = app_env
    assert c.get("/api/shopify/session", headers=H(id_token(secret="wrong-secret-xxxxxxxxxxxxxxxx"))).status_code == 401
    r = c.post("/api/dev/signup", json={"email": "web@x.com", "password": "correct horse 1"})
    web = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert c.get("/api/brands", headers=web).status_code == 200
    assert c.get("/api/shopify/session", headers=web).status_code == 403


def _hook(c, topic, payload, secret=SECRET, shop=SHOP):
    body = json.dumps(payload).encode()
    sig = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return c.post("/webhooks/shopify", content=body, headers={"X-Shopify-Hmac-Sha256": sig, "X-Shopify-Topic": topic,
                                                              "X-Shopify-Shop-Domain": shop, "Content-Type": "application/json"})


def test_webhooks(app_env):
    c, fake, main = app_env
    assert c.get("/api/shopify/session", headers=H()).status_code == 200
    assert c.post("/api/shopify/import", json={"days": 365}, headers=H()).status_code == 201
    assert _hook(c, "app/uninstalled", {}, secret="wrong").status_code == 401
    for t in ("customers/data_request", "customers/redact"):
        assert _hook(c, t, {"shop_domain": SHOP, "customer": {"id": 1}}).status_code == 200
    assert _hook(c, "app/uninstalled", {}).status_code == 200
    with main.engine.connect() as con:
        row = con.execute(main.select(main.shopify_shops)).mappings().first()
        assert row["access_token"] is None and row["uninstalled_at"] is not None
    assert _hook(c, "shop/redact", {"shop_domain": SHOP}).status_code == 200
    uid = main.shp.shop_user_id(SHOP)
    with main.engine.connect() as con:
        assert con.execute(main.select(main.brands).where(main.brands.c.user_id == uid)).first() is None
        assert con.execute(main.select(main.plans).where(main.plans.c.user_id == uid)).first() is None
        assert con.execute(main.select(main.shopify_shops)).first() is None


def test_shopify_off_without_keys(tmp_path):
    os.environ.update(AUTH_MODE="dev", STORAGE_MODE="local", DATABASE_URL=f"sqlite:///{tmp_path / 't.db'}",
                      LOCAL_STORAGE_DIR=str(tmp_path / "files"))
    for k in ("SHOPIFY_API_KEY", "SHOPIFY_API_SECRET"):
        os.environ.pop(k, None)
    for m in [m for m in sys.modules if m.startswith("fasla")]:
        del sys.modules[m]
    main = importlib.import_module("fasla.main")
    with TestClient(main.app) as c:
        assert c.get("/shopify/").status_code == 404
        assert c.post("/webhooks/shopify", content=b"{}").status_code == 404
    for m in [m for m in sys.modules if m.startswith("fasla")]:
        del sys.modules[m]
