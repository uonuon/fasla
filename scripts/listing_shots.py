"""App Store screenshots (1600x900) of the real Shopify page, driven against the real backend.

Shopify is faked (like the tests): a clothing store built from the seeder's catalogue and a small food brand.
Everything on screen (plans, numbers, notes) is computed by Fasla's own engine.

    python scripts/listing_shots.py out_dir
"""
from __future__ import annotations

import importlib
import json
import os
import random
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import seed_shopify as seed  # noqa: E402

KEY, SECRET = "shots-client-id", "shots-client-secret-0123456789-abcdef"
HOST = "https://fasla.test"
REAL_CLIENT = httpx.Client


def clothing_nodes() -> list[dict]:
    variants, costs = [], {}
    for style, cat, price, colors, pop, season in seed.CATALOG:
        costs[style] = round(price * random.Random(seed.handle(style)).uniform(0.34, 0.45), 2)
        for c in colors:
            for sz in seed.SIZES:
                variants.append(dict(id=f"{style}/{sz}/{c}", style=style, size=sz, color=c, price=str(price),
                                     season=season, pop=pop, colors=colors, cat=cat))
    rnd = random.Random(11)
    stock = {v["id"]: rnd.choice([0, 0, 2, 4, 6, 9, 12]) for v in variants}
    nodes = []
    for o in seed.plan_orders(variants, 1150, seed=7):
        lis = []
        for line in o["lines"]:
            v, q = line["variant"], line["qty"]
            lis.append({"title": v["style"], "quantity": q, "currentQuantity": 0 if o["fate"] == "return" else q,
                        "sku": None, "variantTitle": f"{v['size']} / {v['color']}",
                        "originalUnitPriceSet": {"shopMoney": {"amount": v["price"]}},
                        "product": {"id": f"gid://shopify/Product/{v['style']}", "title": v["style"], "productType": v["cat"]},
                        "variant": {"selectedOptions": [{"name": "Size", "value": v["size"]}, {"name": "Color", "value": v["color"]}],
                                    "inventoryQuantity": stock[v["id"]],
                                    "inventoryItem": {"tracked": True, "unitCost": {"amount": str(costs[v["style"]])}}}})
        at = o["at"].strftime("%Y-%m-%dT%H:%M:%SZ")
        nodes.append({"name": f"#{1001 + o['n']}", "createdAt": at, "processedAt": at,
                      "cancelledAt": at if o["fate"] == "cancel" else None,
                      "displayFulfillmentStatus": "FULFILLED", "lineItems": {"nodes": lis}})
    return nodes


FOOD = [  # product, type, price, cost, variants, packs, weight
    ("Bone Broth", "Broths", 320, 140, ["Plain", "Veggie"], ["500 ml"], 2.2),
    ("Raw Sidr Honey", "Honey", 450, 230, ["-"], ["250 g", "500 g"], 1.8),
    ("Fig & Olive Jam", "Jams", 210, 80, ["-"], ["300 g"], 1.4),
    ("Garlic Honey", "Ferments", 280, 120, ["-"], ["250 g"], 0.9),
    ("Cold-pressed Olive Oil", "Oils", 390, 210, ["-"], ["500 ml", "1 L"], 1.5),
    ("Date Paste", "Pantry", 150, 55, ["-"], ["400 g"], 1.1),
]


def food_nodes() -> list[dict]:
    rnd = random.Random(5)
    end = date.today() - timedelta(days=1)
    nodes = []
    for i in range(900):
        d = end - timedelta(days=int(rnd.triangular(0, 365, 0)))
        bump = 1.8 if seed.RAMADAN[0] <= d <= seed.RAMADAN[1] else 1.0
        lis = []
        for _ in range(rnd.choice([1, 1, 2])):
            w = [x[6] * (bump if x[0] in ("Date Paste", "Raw Sidr Honey") else 1) for x in FOOD]
            title, ptype, price, cost, flavours, packs, _ = rnd.choices(FOOD, weights=w)[0]
            fl, pack = rnd.choice(flavours), rnd.choice(packs)
            p = price * (1.8 if pack in ("500 g", "1 L") else 1)
            opts = [{"name": "Size", "value": pack}] + ([{"name": "Flavour", "value": fl}] if fl != "-" else [])
            lis.append({"title": title, "quantity": rnd.choice([1, 1, 2, 3]), "currentQuantity": None, "sku": None,
                        "variantTitle": f"{pack} / {fl}", "originalUnitPriceSet": {"shopMoney": {"amount": str(p)}},
                        "product": {"id": f"gid://shopify/Product/{title}", "title": title, "productType": ptype},
                        "variant": {"selectedOptions": opts, "inventoryQuantity": rnd.randint(0, 25),
                                    "inventoryItem": {"tracked": True, "unitCost": {"amount": str(round(cost * p / price))}}}})
        at = f"{d.isoformat()}T{rnd.randint(9, 22):02d}:00:00Z"
        nodes.append({"name": f"#{3001 + i}", "createdAt": at, "processedAt": at, "cancelledAt": None,
                      "displayFulfillmentStatus": "FULFILLED", "lineItems": {"nodes": lis}})
    return nodes


class Fake:
    def __init__(self, name: str, nodes: list[dict]):
        self.name, self.nodes = name, nodes

    def handler(self, request: httpx.Request):
        if request.url.path == "/admin/oauth/access_token":
            return httpx.Response(200, json={"access_token": "shpat_x", "scope": "read_orders,read_all_orders",
                                             "expires_in": 3600, "refresh_token": "shprt_x",
                                             "refresh_token_expires_in": 7776000})
        q = json.loads(request.content)
        if "currentAppInstallation" in q["query"]:
            return httpx.Response(200, json={"data": {
                "shop": {"name": self.name, "currencyCode": "EGP", "ianaTimezone": "Africa/Cairo"},
                "currentAppInstallation": {"activeSubscriptions": [{"name": "Growth", "status": "ACTIVE", "test": True}]}}})
        after, first = int(q["variables"].get("after") or 0), int(q["variables"].get("first") or 100)
        page = self.nodes[after:after + first]
        return httpx.Response(200, json={"data": {"orders": {"nodes": page, "pageInfo": {
            "hasNextPage": after + first < len(self.nodes), "endCursor": str(after + first)}}},
            "extensions": {"cost": {"throttleStatus": {"currentlyAvailable": 1000, "restoreRate": 100}}}})


def token(shop: str) -> str:
    now = int(time.time())
    return jwt.encode({"iss": f"https://{shop}/admin", "dest": f"https://{shop}", "aud": KEY, "sub": "42",
                       "exp": now + 3600, "nbf": now - 5, "iat": now, "jti": "x", "sid": "s"}, SECRET, algorithm="HS256")


def app_for(fake: Fake, tmp: Path):
    httpx.Client = lambda *a, **k: REAL_CLIENT(transport=httpx.MockTransport(fake.handler))
    os.environ.update(AUTH_MODE="dev", STORAGE_MODE="local", DATABASE_URL=f"sqlite:///{tmp / 't.db'}",
                      LOCAL_STORAGE_DIR=str(tmp / "files"), SOLVER_SECONDS="8", SHOPIFY_API_KEY=KEY,
                      SHOPIFY_API_SECRET=SECRET, SHOPIFY_TOKEN_KEY="")
    for m in [m for m in sys.modules if m.startswith("fasla")]:
        del sys.modules[m]
    from fastapi.testclient import TestClient
    return TestClient(importlib.import_module("fasla.main").app)


def drive(fake: Fake, shop: str, steps, out: Path):
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory() as td, app_for(fake, Path(td)) as client:
        tok = token(shop)

        def route(r):
            url = httpx.URL(r.request.url)
            if url.host == "cdn.shopify.com":
                return r.fulfill(content_type="text/javascript",
                                 body=f"window.shopify={{idToken: async () => {json.dumps(tok)}}};")
            if url.host != "fasla.test":
                return r.abort()
            path = url.raw_path.decode()
            resp = client.request(r.request.method, path, content=r.request.post_data_buffer,
                                  headers={k: v for k, v in r.request.headers.items() if k.lower() != "host"})
            r.fulfill(status=resp.status_code, body=resp.content,
                      headers={k: v for k, v in resp.headers.items() if k.lower() not in ("content-length", "content-encoding")})

        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1067, "height": 600}, device_scale_factor=1.5)
            pg.route("**/*", route)
            pg.goto(f"{HOST}/shopify/")
            pg.wait_for_selector("#title:has-text('·')")
            steps(pg, out)
            b.close()


def shot(pg, out: Path, name: str, top_selector: str, offset: int = 12):
    pg.evaluate("([s, o]) => window.scrollTo(0, document.querySelector(s).getBoundingClientRect().top + scrollY - o)",
                [top_selector, offset])
    pg.wait_for_timeout(300)
    pg.screenshot(path=str(out / name))
    print("saved", name)


def clothing_steps(pg, out):
    pg.click("#import")
    pg.wait_for_selector("#step2:not(.hidden)", timeout=120000)
    shot(pg, out, "02-orders-and-costs.png", "#modeCard")
    pg.click("#run")
    pg.wait_for_selector("#step3:not(.hidden)", timeout=240000)
    shot(pg, out, "01-plan.png", "#step3")


def food_steps(pg, out):
    pg.click("#import")
    pg.wait_for_selector("#importMsg .ok, #importMsg .err", timeout=120000)
    if pg.query_selector("#importMsg .err"):
        sys.exit("import failed: " + pg.inner_text("#importMsg"))
    pg.wait_for_selector("#step2b:not(.hidden)", timeout=120000)
    fill = {"Bone Broth": dict(batch_multiple=30, shelf_life_months=4, lead_time_days=2, make_every_days=14),
            "Raw Sidr Honey": dict(shelf_life_months=24, lead_time_days=10, make_every_days=60),
            "Fig & Olive Jam": dict(batch_multiple=24, shelf_life_months=12, lead_time_days=3)}
    for prod, vals in fill.items():
        for k, v in vals.items():
            pg.fill(f'#bprods tr[data-product="{prod}"] input[data-k="{k}"]', str(v))
    pg.click("h2")   # no focus ring in the picture
    shot(pg, out, "03-batch-inputs.png", "#modeCard")
    pg.click("#brun")
    pg.wait_for_selector("#step3b:not(.hidden)", timeout=120000)
    shot(pg, out, "04-batch-plan.png", "#step3b")


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "shots")
    out.mkdir(parents=True, exist_ok=True)
    drive(Fake("Nile Thread", clothing_nodes()), "nile-thread.myshopify.com", clothing_steps, out)
    drive(Fake("Sidr & Fig Pantry", food_nodes()), "sidr-fig.myshopify.com", food_steps, out)
