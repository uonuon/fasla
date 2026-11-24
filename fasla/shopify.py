"""Fasla as a Shopify app (embedded in the Shopify admin).

How it fits together:
  * The merchant opens Fasla inside their Shopify admin. App Bridge gives our page a short-lived ID token
    (a JWT signed with our app's client secret) and adds it to every fetch to our backend.
  * `verify_id_token` checks it; the shop's domain becomes the Fasla "user" (a stable UUID per shop), so
    all the existing brand / plan endpoints work unchanged.
  * To read orders we need an Admin API access token: we get one with token exchange (ID token ->
    expiring offline token, 1 hour, plus a 90-day refresh token) and refresh it server-side when needed.
    Tokens are stored encrypted.
  * `fetch_orders` reads line items only: date, product, size/colour options, quantity, price, status.
    No customer names, emails, phones or addresses are requested (protected customer data level 1).
  * Webhooks: app/uninstalled drops the tokens; shop/redact deletes everything for the shop;
    customers/data_request and customers/redact are acknowledged (Fasla stores no customer data).
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken

SHOP_NS = uuid.UUID("7c4a1f1e-5b6e-4d0a-9a3e-0f5a5f1a5e01")
TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ID_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"
OFFLINE_TOKEN_TYPE = "urn:shopify:params:oauth:token-type:offline-access-token"


class ShopifyError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def is_shop_domain(shop: str) -> bool:
    shop = (shop or "").lower()
    return shop.endswith(".myshopify.com") and all(c.isalnum() or c in "-." for c in shop) and len(shop) < 120


def shop_user_id(shop: str) -> uuid.UUID:
    """The Fasla user id for a shop: stable, so reinstalling finds the same brands and plans."""
    return uuid.uuid5(SHOP_NS, shop.lower())


# ---------------------------------------------------------------- ID tokens (session tokens)
def peek_audience(token: str) -> str:
    try:
        aud = jwt.decode(token, options={"verify_signature": False}).get("aud")
    except jwt.PyJWTError:
        return ""
    return aud if isinstance(aud, str) else ""


def verify_id_token(token: str, api_key: str, api_secret: str, leeway: int = 10) -> dict:
    """Check an App Bridge ID token: HS256 with our client secret, aud = our client id, not expired, not
    before nbf, and iss/dest on the same *.myshopify.com host. Returns claims plus 'shop'."""
    if not api_key or not api_secret:
        raise ShopifyError("Shopify app is not configured", 503)
    try:
        claims = jwt.decode(token, api_secret, algorithms=["HS256"], audience=api_key, leeway=leeway,
                            options={"require": ["exp", "nbf", "iss", "dest", "aud"]})
    except jwt.ExpiredSignatureError as e:
        raise ShopifyError("Session expired, reload the app", 401) from e
    except jwt.PyJWTError as e:
        raise ShopifyError("Invalid Shopify session", 401) from e
    dest = urlparse(str(claims.get("dest", ""))).hostname or ""
    iss = urlparse(str(claims.get("iss", ""))).hostname or ""
    if not dest or dest != iss or not is_shop_domain(dest):
        raise ShopifyError("Invalid Shopify session", 401)
    claims["shop"] = dest.lower()
    return claims


# ---------------------------------------------------------------- webhooks
def verify_webhook(body: bytes, hmac_header: str, api_secret: str) -> bool:
    if not api_secret or not hmac_header:
        return False
    digest = base64.b64encode(hmac.new(api_secret.encode(), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(digest, hmac_header.strip())


# ---------------------------------------------------------------- token storage
def _fernet(token_key: str, api_secret: str) -> Fernet:
    if token_key:
        return Fernet(token_key.encode())
    # derived from the client secret when no separate key is set (rotating the secret invalidates tokens anyway)
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"fasla-shopify-tokens:" + api_secret.encode()).digest()))


def encrypt(value: str, token_key: str, api_secret: str) -> str:
    return _fernet(token_key, api_secret).encrypt(value.encode()).decode()


def decrypt(value: str, token_key: str, api_secret: str) -> str:
    try:
        return _fernet(token_key, api_secret).decrypt(value.encode()).decode()
    except InvalidToken as e:
        raise ShopifyError("Stored Shopify token can't be read; reopen the app", 401) from e


@dataclass
class Tokens:
    access_token: str
    expires_at: datetime | None
    refresh_token: str | None
    refresh_expires_at: datetime | None
    scope: str

    @staticmethod
    def from_response(j: dict) -> "Tokens":
        now = datetime.now(timezone.utc)
        exp = j.get("expires_in")
        rexp = j.get("refresh_token_expires_in")
        return Tokens(access_token=j["access_token"],
                      expires_at=now + timedelta(seconds=int(exp)) if exp else None,
                      refresh_token=j.get("refresh_token"),
                      refresh_expires_at=now + timedelta(seconds=int(rexp)) if rexp else None,
                      scope=j.get("scope", ""))

    def access_valid(self, margin: int = 120) -> bool:
        return self.expires_at is None or self.expires_at > datetime.now(timezone.utc) + timedelta(seconds=margin)

    def refresh_valid(self) -> bool:
        return bool(self.refresh_token) and (self.refresh_expires_at is None or
                                             self.refresh_expires_at > datetime.now(timezone.utc) + timedelta(minutes=5))


def exchange_token(shop: str, id_token: str, api_key: str, api_secret: str, client: httpx.Client | None = None) -> Tokens:
    """ID token -> expiring offline access token (+ refresh token)."""
    c = client or httpx.Client(timeout=20)
    r = c.post(f"https://{shop}/admin/oauth/access_token", json={
        "client_id": api_key, "client_secret": api_secret, "grant_type": TOKEN_EXCHANGE,
        "subject_token": id_token, "subject_token_type": ID_TOKEN_TYPE,
        "requested_token_type": OFFLINE_TOKEN_TYPE, "expiring": "1"})
    if r.status_code != 200:
        raise ShopifyError("Shopify didn't give us access. Reload the app and try again.", 401)
    return Tokens.from_response(r.json())


def refresh_token(shop: str, refresh: str, api_key: str, api_secret: str, client: httpx.Client | None = None) -> Tokens:
    c = client or httpx.Client(timeout=20)
    r = c.post(f"https://{shop}/admin/oauth/access_token", json={
        "client_id": api_key, "client_secret": api_secret, "grant_type": "refresh_token", "refresh_token": refresh})
    if r.status_code != 200:
        raise ShopifyError("Shopify access expired. Reload the app.", 401)
    return Tokens.from_response(r.json())


# ---------------------------------------------------------------- Admin GraphQL
class Admin:
    def __init__(self, shop: str, access_token: str, api_version: str, client: httpx.Client | None = None,
                 reauth=None):
        self.url = f"https://{shop}/admin/api/{api_version}/graphql.json"
        self.headers = {"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"}
        self.c = client or httpx.Client(timeout=40)
        # reauth() -> a fresh access token (or None). Used once when Shopify rejects the stored one,
        # e.g. after an uninstall + reinstall whose app/uninstalled webhook hasn't arrived yet.
        self.reauth = reauth

    def query(self, q: str, variables: dict | None = None, retries: int = 5) -> dict:
        for attempt in range(retries):
            r = self.c.post(self.url, json={"query": q, "variables": variables or {}}, headers=self.headers)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(2 ** attempt, 10))
                continue
            if r.status_code in (401, 403):
                fresh = self.reauth() if self.reauth else None
                self.reauth = None
                if fresh:
                    self.headers["X-Shopify-Access-Token"] = fresh
                    continue
                raise ShopifyError("Fasla lost access to this store. Reload the app.", 401)
            r.raise_for_status()
            j = r.json()
            errs = j.get("errors") or []
            if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errs):
                time.sleep(min(2 ** attempt, 10))
                continue
            if errs:
                msg = "; ".join(str(e.get("message", e))[:200] for e in errs[:3])
                if "access" in msg.lower() and "order" in msg.lower():
                    raise ShopifyError("Fasla needs permission to read orders. Approve the new permissions and retry.", 403)
                raise ShopifyError(f"Shopify said: {msg}", 502)
            self._throttle(j)
            return j["data"]
        raise ShopifyError("Shopify is busy right now. Try again in a minute.", 503)

    @staticmethod
    def _throttle(j: dict) -> None:
        cost = (j.get("extensions") or {}).get("cost") or {}
        st = cost.get("throttleStatus") or {}
        avail, rate = st.get("currentlyAvailable"), st.get("restoreRate") or 50
        if avail is not None and avail < 200:
            time.sleep(min((200 - avail) / rate, 5))


SHOP_QUERY = """{ shop { name currencyCode ianaTimezone } currentAppInstallation { activeSubscriptions { name status test } } }"""

ORDERS_QUERY = """
query Orders($q: String!, $after: String, $first: Int!, $lines: Int!) {
  orders(first: $first, after: $after, query: $q, sortKey: PROCESSED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      name createdAt processedAt cancelledAt displayFulfillmentStatus
      lineItems(first: $lines) {
        nodes {
          title quantity currentQuantity sku variantTitle
          originalUnitPriceSet { shopMoney { amount } }
          product { id title productType }
          variant { selectedOptions { name value } inventoryQuantity
                    inventoryItem { tracked unitCost { amount } } }
        }
      }
    }
  }
}"""

SIZE_NAMES = ("size", "sizes", "المقاس", "مقاس", "الحجم", "taille", "talla")
COLOR_NAMES = ("color", "colour", "اللون", "لون", "couleur")
# No financial status column: a partial refund would mark every line refused. Line-level returns are read
# from currentQuantity instead, and cancellations from cancelled_at.
CSV_COLUMNS = ["order_id", "date", "style", "size", "color", "qty", "price", "cost", "category", "status",
               "cancelled_at", "stock"]


ONE_SIZE = "One size"


def _is_size_option(n: str) -> bool:
    # "Size", "Shoe size", "Size (EU)", "Talla", "مقاس", "Größe", "Waist"...
    return n in SIZE_NAMES or any(w in n for w in ("size", "مقاس", "taille", "talla", "größe", "groesse", "waist"))


def _is_color_option(n: str) -> bool:
    return n in COLOR_NAMES or any(w in n for w in ("color", "colour", "لون", "couleur", "farbe"))


def _options(variant: dict | None, variant_title: str | None) -> tuple[str, str]:
    """Size and colour of a line. Products with no size option (bags, scarves, one-size pieces) get "One size"
    instead of being dropped: they are part of the store's sales and cash too."""
    size = color = other = ""
    opts = (variant or {}).get("selectedOptions") or []
    for o in opts:
        n = str(o.get("name", "")).strip().lower()
        if _is_size_option(n) and not size:
            size = str(o.get("value", ""))
        elif _is_color_option(n) and not color:
            color = str(o.get("value", ""))
        elif not other and n != "title" and str(o.get("value", "")) != "Default Title":
            other = str(o.get("value", ""))   # flavour, scent, material...: the variant when there is no colour
    color = color or other
    if not opts and not size and not color and variant_title and variant_title != "Default Title":
        # no option names (variant deleted since): "M / Black" style titles
        parts = [p.strip() for p in variant_title.split("/")]
        size = parts[0] if parts else ""
        color = parts[1] if len(parts) > 1 else ""
    return size or ONE_SIZE, color


def order_rows(orders: list[dict]) -> list[dict]:
    rows = []
    for o in orders:
        for li in (o.get("lineItems") or {}).get("nodes") or []:
            qty = li.get("quantity") or 0
            if qty <= 0:
                continue
            prod = li.get("product") or {}
            if not prod and not li.get("variant") and not li.get("variantTitle"):
                continue   # custom line (gift wrap, tip, manual charge): not a product to make
            size, color = _options(li.get("variant"), li.get("variantTitle"))
            var = li.get("variant") or {}
            inv = var.get("inventoryItem") or {}
            cost = (inv.get("unitCost") or {}).get("amount")
            # stock on hand now; only when Shopify tracks it (untracked variants report 0 or less)
            stock = var.get("inventoryQuantity") if inv.get("tracked") and var.get("inventoryQuantity") is not None else ""
            if stock != "":
                stock = max(int(stock), 0)
            removed = qty - (li.get("currentQuantity") if li.get("currentQuantity") is not None else qty)
            status = o.get("displayFulfillmentStatus") or ""
            if removed >= qty and not o.get("cancelledAt"):
                status = "returned"
            rows.append(dict(
                order_id=o.get("name", ""), date=str(o.get("processedAt") or o.get("createdAt") or "")[:10],
                style=prod.get("title") or li.get("title") or "", size=size, color=color, qty=qty,
                price=((li.get("originalUnitPriceSet") or {}).get("shopMoney") or {}).get("amount", ""),
                cost=cost or "", category=prod.get("productType") or "", status=status,
                cancelled_at=o.get("cancelledAt") or "", stock=stock))
    return rows


WEBSITE_ONLY_WARNINGS = ("No city or zone column", "No production numbers")

WINDOW_WARNING = ("Shopify showed Fasla only the last 60 days of orders (new apps get more after Shopify approves "
                  "them). If your store is older, upload your full orders export below for a much better plan.")


def only_recent_window(orders: list[dict], days: int = 61) -> bool:
    """True when every order Shopify returned was created in the last ~60 days: the default window an app sees
    without the read_all_orders scope (a store younger than that looks the same)."""
    created = [o.get("createdAt") for o in orders if o.get("createdAt")]
    if not created:
        return False
    oldest = min(datetime.fromisoformat(c.replace("Z", "+00:00")) for c in created)
    return (datetime.now(timezone.utc) - oldest).days <= days


def fetch_orders(admin: Admin, since: date, max_orders: int = 20000) -> list[dict]:
    # processed_at, not created_at: orders imported from another platform keep their real date there.
    q = f"processed_at:>={since.isoformat()}"
    out, after = [], None
    # Shopify caps what one request may cost; big pages of orders x line items can exceed it. Start large and
    # step down if Shopify refuses (the cap and its arithmetic have changed between API versions).
    sizes = [(100, 50), (40, 30), (15, 25)]
    while True:
        for i, (first, lines) in enumerate(sizes):
            try:
                d = admin.query(ORDERS_QUERY, {"q": q, "after": after, "first": first, "lines": lines})["orders"]
                sizes = sizes[i:]
                break
            except ShopifyError as e:
                if "cost" not in e.message.lower() or i == len(sizes) - 1:
                    raise
        out.extend(d["nodes"])
        if not d["pageInfo"]["hasNextPage"] or len(out) >= max_orders:
            return out[:max_orders]
        after = d["pageInfo"]["endCursor"]


def rows_to_csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in CSV_COLUMNS})
    return buf.getvalue().encode("utf-8")


# Plans sold through Shopify (managed pricing). Names must match the plan names set on the listing.
PAID_TIERS = ("starter", "growth", "scale")
FREE_PLANS_PER_MONTH = 1
# Plans are sized by the store: products (each size and colour counts) and orders a month. None = no limit.
TIER_LIMITS = {"free": (300, 1000), "starter": (300, 1000), "growth": (2000, 5000), "scale": (None, None)}
NEXT_TIER = {"free": "Starter", "starter": "Growth", "growth": "Scale"}
ALERT_TIERS = ("growth", "scale")


def store_size(stats: dict) -> dict | None:
    """Products (style x colour x size) and average orders a month, from an import's stats."""
    try:
        d0, d1 = date.fromisoformat(str(stats["date_from"])[:10]), date.fromisoformat(str(stats["date_to"])[:10])
        months = max((d1 - d0).days / 30.44, 1.0)
        return {"products": int(stats.get("products") or 0), "orders_per_month": round(float(stats.get("orders") or 0) / months)}
    except (KeyError, TypeError, ValueError):
        return None


def size_limit_message(tier: str, stats: dict) -> str | None:
    """None if the store fits its plan, else what to upgrade to and why."""
    size = store_size(stats)
    lim = TIER_LIMITS.get(tier, TIER_LIMITS["free"])
    if size is None or lim[0] is None:
        return None
    over = []
    if size["products"] > lim[0]:
        over.append(f"{size['products']:,} products (sizes and colours count; your plan covers {lim[0]:,})")
    if size["orders_per_month"] > lim[1]:
        over.append(f"about {size['orders_per_month']:,} orders a month (your plan covers {lim[1]:,})")
    if not over:
        return None
    up = NEXT_TIER.get(tier, "Scale")
    if tier in ("free", "starter") and size["products"] <= TIER_LIMITS["growth"][0] \
            and size["orders_per_month"] <= TIER_LIMITS["growth"][1]:
        up = "Growth"
    elif tier in ("free", "starter", "growth"):
        up = "Scale" if tier == "growth" or size["products"] > TIER_LIMITS["growth"][0] \
            or size["orders_per_month"] > TIER_LIMITS["growth"][1] else up
    return f"Your store has {' and '.join(over)}. Upgrade to {up} to plan it."


def tier(subscriptions: list[dict]) -> str:
    """'free' or the paid tier name from the store's active app subscriptions (test charges on dev stores count)."""
    for s in subscriptions or []:
        if str(s.get("status", "ACTIVE")).upper() != "ACTIVE":
            continue
        name = str(s.get("name", "")).lower()
        for t in PAID_TIERS:
            if t in name:
                return t
    return "free"


def pricing_url(shop: str, app_handle: str) -> str:
    """Shopify-hosted plan page (managed pricing): the merchant picks a plan there."""
    return f"https://admin.shopify.com/store/{shop.split('.')[0]}/charges/{app_handle}/pricing_plans"
