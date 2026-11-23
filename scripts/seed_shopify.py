"""Fill a Shopify *development* store with a year of believable clothing sales, so Fasla can be tested and demoed.

Runs from GitHub Actions (workflow "Seed Shopify dev store"), using a separate "Fasla Seeder" app that is installed
only on the dev store. Fasla itself stays read-only.

    SHOP=fasla-test.myshopify.com SEED_CLIENT_ID=... SEED_CLIENT_SECRET=... python scripts/seed_shopify.py --orders 400

Safe to run again: products are matched by handle, and orders already created (tag "fasla-seed") are skipped,
so a run that stopped half-way just continues. Development stores accept at most 5 new orders per minute,
so 400 orders take about 80 minutes.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
import time
from datetime import date, datetime, timedelta, timezone

import httpx

API_VERSION = "2026-10"
TAG = "fasla-seed"

# style, category, base price EGP, colours, popularity, season ("summer" | "winter" | "eid" | "all")
CATALOG = [
    ("Linen Wide-Leg Pants", "Bottoms", 890, ["Beige", "White", "Olive"], 1.6, "summer"),
    ("Oversized Cotton Tee", "Tops", 450, ["White", "Black", "Sage", "Baby Blue"], 2.4, "summer"),
    ("Ribbed Tank Top", "Tops", 320, ["White", "Black", "Brown"], 1.2, "summer"),
    ("Satin Midi Skirt", "Bottoms", 750, ["Champagne", "Black", "Emerald"], 1.0, "eid"),
    ("Pleated Maxi Dress", "Dresses", 1450, ["Dusty Pink", "Navy", "Mocha"], 1.1, "eid"),
    ("Embroidered Kaftan", "Dresses", 1650, ["Ivory", "Burgundy"], 0.8, "eid"),
    ("Everyday Abaya", "Abayas", 1250, ["Black", "Charcoal", "Taupe"], 1.3, "eid"),
    ("Modest Wrap Blouse", "Tops", 620, ["White", "Black", "Lilac"], 1.4, "all"),
    ("High-Waist Mom Jeans", "Bottoms", 980, ["Light Wash", "Dark Wash"], 1.7, "all"),
    ("Knit Cardigan", "Outerwear", 1150, ["Cream", "Camel", "Grey"], 0.9, "winter"),
    ("Puffer Jacket", "Outerwear", 1950, ["Black", "Olive"], 0.7, "winter"),
    ("Fleece Hoodie", "Tops", 850, ["Grey", "Black", "Navy", "Brown"], 1.5, "winter"),
]
SIZES = ["S", "M", "L", "XL"]
SIZE_W = [0.18, 0.34, 0.31, 0.17]

RAMADAN = (date(2026, 2, 18), date(2026, 3, 19))
EID_ADHA = date(2026, 5, 27)
WHITE_FRIDAY = date(2025, 11, 28)


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


class Shop:
    def __init__(self, shop: str, client_id: str, secret: str):
        self.shop = shop
        r = httpx.post(f"https://{shop}/admin/oauth/access_token", timeout=30,
                       data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret})
        if r.status_code != 200:
            sys.exit(f"Could not get a token ({r.status_code}): {r.text[:300]}")
        self.token = r.json()["access_token"]
        self.http = httpx.Client(timeout=60, headers={"X-Shopify-Access-Token": self.token})

    def q(self, query: str, variables: dict | None = None) -> dict:
        for attempt in range(8):
            r = self.http.post(f"https://{self.shop}/admin/api/{API_VERSION}/graphql.json",
                               json={"query": query, "variables": variables or {}})
            if r.status_code in (429, 502, 503):
                time.sleep(2 + attempt * 3)
                continue
            r.raise_for_status()
            body = r.json()
            errs = body.get("errors")
            if errs and any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errs):
                time.sleep(2 + attempt * 2)
                continue
            if errs:
                raise RuntimeError(errs)
            return body["data"]
        raise RuntimeError("Shopify kept throttling")


def handle(style: str) -> str:
    return "seed-" + style.lower().replace(" ", "-")


def ensure_products(s: Shop, currency_factor: float) -> list[dict]:
    """Returns one entry per variant: {id, style, size, color, price, season, pop}."""
    out = []
    for style, cat, price, colors, pop, season in CATALOG:
        h = handle(style)
        found = s.q("query($h:String!){ productByIdentifier(identifier:{handle:$h}){ id variants(first:100){ nodes{ id price "
                    "selectedOptions{ name value } } } } }", {"h": h})["productByIdentifier"]
        if not found:
            p = round(price * currency_factor, 2)
            cost = round(p * random.Random(h).uniform(0.34, 0.45), 2)
            variants = [{"optionValues": [{"optionName": "Size", "name": sz}, {"optionName": "Color", "name": c}],
                         "price": str(p), "inventoryItem": {"cost": str(cost), "tracked": False}}
                        for c in colors for sz in SIZES]
            res = s.q("""mutation($input: ProductSetInput!){ productSet(synchronous: true, input: $input){
                product { id variants(first:100){ nodes{ id price selectedOptions{ name value } } } }
                userErrors { field message } } }""", {"input": {
                "title": style, "handle": h, "productType": cat, "status": "ACTIVE", "tags": [TAG],
                "productOptions": [{"name": "Size", "values": [{"name": x} for x in SIZES]},
                                   {"name": "Color", "values": [{"name": x} for x in colors]}],
                "variants": variants}})["productSet"]
            if res["userErrors"]:
                sys.exit(f"productSet failed for {style}: {res['userErrors']}")
            found = res["product"]
            log("created product", style, len(variants), "variants")
        for v in found["variants"]["nodes"]:
            opt = {o["name"]: o["value"] for o in v["selectedOptions"]}
            out.append(dict(id=v["id"], style=style, size=opt.get("Size"), color=opt.get("Color"),
                            price=v["price"], season=season, pop=pop, colors=colors))
    return out


def day_weight(d: date, start: date) -> float:
    w = 1.0 + 0.5 * (d - start).days / 365          # the brand grows about 50% over the year
    if d.weekday() in (3, 4):                        # Thursday/Friday evenings
        w *= 1.25
    r0, r1 = RAMADAN
    if r0 <= d <= r1:
        w *= 1.6 + 1.6 * ((d - r0).days / (r1 - r0).days)  # builds up to Eid al-Fitr
    if timedelta(0) <= EID_ADHA - d <= timedelta(days=12):
        w *= 1.9
    if timedelta(0) <= d - WHITE_FRIDAY <= timedelta(days=4) or \
       timedelta(0) <= d - WHITE_FRIDAY.replace(year=2026) <= timedelta(days=4):
        w *= 2.6
    if date(2026, 8, 25) <= d <= date(2026, 9, 20):  # back to school/university
        w *= 1.3
    return w


def season_mult(season: str, d: date) -> float:
    m = d.month
    summer = 1.0 + 0.9 * math.cos((m - 7) / 12 * 2 * math.pi)   # peaks in July
    if season == "summer":
        return max(0.15, summer)
    if season == "winter":
        return max(0.1, 2.0 - summer)
    if season == "eid":
        r0, r1 = RAMADAN
        return 2.8 if (r0 <= d <= r1 + timedelta(days=2) or timedelta(0) <= EID_ADHA - d <= timedelta(days=12)) \
            else 0.6
    return 1.0


def plan_orders(variants: list[dict], n: int, seed: int = 7) -> list[dict]:
    rnd = random.Random(seed)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=364)
    days = [start + timedelta(days=i) for i in range(365)]
    dw = [day_weight(d, start) for d in days]
    styles = {}
    for v in variants:
        styles.setdefault(v["style"], []).append(v)
    color_pop = {}  # first colour of each style sells best
    for st, vs in styles.items():
        cols = vs[0]["colors"]
        color_pop[st] = {c: 1.0 / (1 + 0.6 * i) for i, c in enumerate(cols)}
    orders = []
    for i, d in enumerate(sorted(rnd.choices(days, weights=dw, k=n))):
        names = list(styles)
        sw = [styles[s][0]["pop"] * season_mult(styles[s][0]["season"], d) for s in names]
        lines = []
        for _ in range(rnd.choices([1, 2, 3], weights=[0.62, 0.29, 0.09])[0]):
            st = rnd.choices(names, weights=sw)[0]
            size = rnd.choices(SIZES, weights=SIZE_W)[0]
            cols = list(color_pop[st])
            color = rnd.choices(cols, weights=[color_pop[st][c] for c in cols])[0]
            v = next(x for x in styles[st] if x["size"] == size and x["color"] == color)
            if any(l["variant"]["id"] == v["id"] for l in lines):
                continue
            lines.append({"variant": v, "qty": rnd.choices([1, 2], weights=[0.88, 0.12])[0]})
        t = datetime(d.year, d.month, d.day, rnd.randint(10, 23), rnd.randint(0, 59), tzinfo=timezone.utc)
        fate = rnd.random()
        orders.append({"n": i, "at": t, "lines": lines,
                       "fate": "cancel" if fate < 0.07 else ("return" if fate < 0.13 else "ok")})
    return orders


def set_stock(s: Shop, variants: list[dict], orders: list[dict]) -> None:
    """Give every seeded variant a believable stock level: about 0.3-2.5 months of its recent sales, a few
    best sellers sold out (0), slow sizes a handful. Tracked, so Fasla reads it. Safe to repeat (absolute values)."""
    rnd = random.Random(11)
    recent_from = max(o["at"] for o in orders) - timedelta(days=60)
    rate = {}
    for o in orders:
        if o["at"] >= recent_from and o["fate"] == "ok":
            for l in o["lines"]:
                rate[l["variant"]["id"]] = rate.get(l["variant"]["id"], 0) + l["qty"] / 2   # per month
    top = sorted(rate, key=rate.get, reverse=True)[: max(1, len(rate) // 8)]
    loc = s.q("query{ locations(first: 1){ nodes{ id } } }")["locations"]["nodes"][0]["id"]
    ids = [v["id"] for v in variants]
    inv = {}
    for i in range(0, len(ids), 50):
        for n in s.q("query($ids:[ID!]!){ nodes(ids:$ids){ ... on ProductVariant { id inventoryItem { id tracked } } } }",
                     {"ids": ids[i:i + 50]})["nodes"]:
            inv[n["id"]] = n["inventoryItem"]
    for vid, item in inv.items():
        if not item["tracked"]:
            e = s.q("""mutation($id: ID!){ inventoryItemUpdate(id: $id, input: {tracked: true}){
                userErrors { message } } }""", {"id": item["id"]})["inventoryItemUpdate"]["userErrors"]
            if e:
                log("tracking failed:", e)
    qty = []
    for vid, item in inv.items():
        if vid in top and rnd.random() < 0.6:
            q = 0
        elif vid in rate:
            q = int(round(rate[vid] * rnd.uniform(0.3, 2.5)))
        else:
            q = rnd.randint(1, 6)
        qty.append({"inventoryItemId": item["id"], "locationId": loc, "quantity": q, "changeFromQuantity": None})
    for i in range(0, len(qty), 100):
        r = s.q("""mutation($input: InventorySetQuantitiesInput!, $key: String!){
            inventorySetQuantities(input: $input) @idempotent(key: $key){ userErrors { code message } } }""",
                {"input": {"name": "available", "reason": "correction", "quantities": qty[i:i + 100]},
                 "key": f"fasla-seed-stock-{datetime.now().strftime('%Y%m%d%H%M%S')}-{i}"})
        if r["inventorySetQuantities"]["userErrors"]:
            log("stock failed:", r["inventorySetQuantities"]["userErrors"])
    log(f"stock set on {len(qty)} variants, {sum(1 for x in qty if x['quantity'] == 0)} at zero,",
        f"{sum(x['quantity'] for x in qty)} pieces in all")


def count_seeded(s: Shop) -> int:
    return s.q('query{ ordersCount(query:"tag:%s", limit: null){ count } }' % TAG)["ordersCount"]["count"]


def apply_fate(s: Shop, order: dict, fate: str) -> None:
    """Cancel the order, or refund (return) its first line. Both mutations need an @idempotent key from API
    2026-04; keyed on the order so a repeat is harmless."""
    try:
        oid = order["id"].rsplit("/", 1)[-1]
        if fate == "cancel":
            r = s.q("""mutation($id: ID!, $key: String!){ orderCancel(orderId:$id, reason: CUSTOMER, restock: false,
                notifyCustomer: false, refundMethod: {originalPaymentMethodsRefund: false}) @idempotent(key: $key){
                userErrors: orderCancelUserErrors { message } } }""", {"id": order["id"], "key": f"seed-cancel-{oid}"})
            if r["orderCancel"]["userErrors"]:
                log("  cancel not recorded:", r["orderCancel"]["userErrors"])
        elif fate == "return":
            li = order["lineItems"]["nodes"][0]
            r = s.q("""mutation($input: RefundInput!, $key: String!){ refundCreate(input: $input) @idempotent(key: $key){
                userErrors { message } } }""", {"key": f"seed-return-{oid}", "input": {"orderId": order["id"],
                    "notify": False, "refundLineItems": [{"lineItemId": li["id"], "quantity": li["quantity"],
                                                          "restockType": "NO_RESTOCK"}]}})["refundCreate"]
            if r["userErrors"]:
                log("  return not recorded:", r["userErrors"])
    except Exception as e:  # a missing cancel/return must not stop the run
        log("  follow-up failed:", str(e)[:300])


def catch_up_fates(s: Shop, orders: list[dict]) -> None:
    """Apply cancellations and returns that earlier runs could not record, matching seeded orders to the plan by
    their processed time (minute precision, unique in practice)."""
    want = {o["at"].strftime("%Y-%m-%dT%H:%M"): o["fate"] for o in orders if o["fate"] != "ok"}
    after, fixed = None, 0
    while True:
        d = s.q("""query($after: String){ orders(first: 100, after: $after, query: "tag:fasla-seed"){
            pageInfo { hasNextPage endCursor }
            nodes { id processedAt cancelledAt lineItems(first: 5){ nodes { id quantity currentQuantity } } } } }""",
                {"after": after})["orders"]
        for n in d["nodes"]:
            fate = want.get(n["processedAt"][:16])
            if fate == "cancel" and not n["cancelledAt"]:
                apply_fate(s, n, "cancel"); fixed += 1
            elif fate == "return" and n["lineItems"]["nodes"] and n["lineItems"]["nodes"][0]["currentQuantity"] > 0:
                apply_fate(s, n, "return"); fixed += 1
        if not d["pageInfo"]["hasNextPage"]:
            break
        after = d["pageInfo"]["endCursor"]
    log(f"caught up {fixed} cancellations/returns")


def create_order(s: Shop, o: dict, currency: str) -> str | None:
    total = sum(float(l["variant"]["price"]) * l["qty"] for l in o["lines"])
    inp = {
        "processedAt": o["at"].isoformat(), "currency": currency, "tags": [TAG],
        "financialStatus": "PAID",
        "lineItems": [{"variantId": l["variant"]["id"], "quantity": l["qty"]} for l in o["lines"]],
        "transactions": [{"kind": "SALE", "status": "SUCCESS", "gateway": "Cash on Delivery",
                          "amountSet": {"shopMoney": {"amount": f"{total:.2f}", "currencyCode": currency}}}],
    }
    for attempt in range(30):
        d = s.q("""mutation($order: OrderCreateOrderInput!, $options: OrderCreateOptionsInput){
            orderCreate(order: $order, options: $options){ order { id lineItems(first:5){ nodes{ id quantity } } }
            userErrors { field message } } }""",
                {"order": inp, "options": {"sendReceipt": False, "sendFulfillmentReceipt": False,
                                           "inventoryBehaviour": "BYPASS"}})["orderCreate"]
        errs = d["userErrors"]
        if not errs:
            order = d["order"]
            apply_fate(s, order, o["fate"])
            return order["id"]
        msg = " ".join(e["message"] for e in errs)
        if "too many" in msg.lower() or "limit" in msg.lower() or "throttl" in msg.lower():
            time.sleep(15)
            continue
        log("order failed:", msg)
        return None
    log("gave up after repeated rate limits")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orders", type=int, default=400)
    args = ap.parse_args()
    shop = os.environ["SHOP"].strip()
    if not shop.endswith(".myshopify.com"):
        shop += ".myshopify.com"
    s = Shop(shop, os.environ["SEED_CLIENT_ID"].strip(), os.environ["SEED_CLIENT_SECRET"].strip())
    info = s.q("query{ shop{ name currencyCode plan{ partnerDevelopment } } }")["shop"]
    log("shop:", info["name"], info["currencyCode"], "dev store:", info["plan"]["partnerDevelopment"])
    if not info["plan"]["partnerDevelopment"]:
        sys.exit("Refusing to seed: this is not a development store.")
    currency = info["currencyCode"]
    factor = 1.0 if currency == "EGP" else 0.02   # EGP prices, or roughly USD-like numbers otherwise
    random.seed(1)
    variants = ensure_products(s, factor)
    orders = plan_orders(variants, args.orders)
    done = count_seeded(s)
    log(f"{done} seeded orders already there, {len(orders) - done} to go")
    t0 = time.time()
    for o in orders[done:]:
        # stay just under 5 orders a minute
        wait = 12.5 - (time.time() - t0)
        if wait > 0:
            time.sleep(wait)
        t0 = time.time()
        oid = create_order(s, o, currency)
        if oid and (o["n"] + 1) % 10 == 0:
            log(f"{o['n'] + 1}/{len(orders)} orders ({o['at'].date()})")
    log("done:", count_seeded(s), "seeded orders")
    catch_up_fates(s, orders)
    set_stock(s, variants, orders)


if __name__ == "__main__":
    main()
