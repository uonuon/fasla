"""Nightly end-to-end check against the real Shopify test store and the live site.

Reads fasla-test's orders and stock through the Admin API (the seeder app's client credentials, same org),
runs them through exactly the code Fasla uses (order_rows -> rows_to_csv -> planner) and checks the plan makes
sense. Then checks the live site answers. Any failed check exits non-zero, so GitHub emails the repo owner.

    SHOP=fasla-test.myshopify.com SEED_CLIENT_ID=... SEED_CLIENT_SECRET=... python scripts/live_check.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fasla import shopify as shp  # noqa: E402
from fasla.planner import read_workbook, run_plan_on  # noqa: E402

SITE = os.environ.get("SITE", "https://fasla.dotcraft.app")
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok:
        failures.append(what)


def store_checks() -> None:
    shop = os.environ["SHOP"].strip()
    r = httpx.post(f"https://{shop}/admin/oauth/access_token", timeout=30, data={
        "grant_type": "client_credentials", "client_id": os.environ["SEED_CLIENT_ID"].strip(),
        "client_secret": os.environ["SEED_CLIENT_SECRET"].strip()})
    check(r.status_code == 200, f"token for {shop} ({r.status_code})")
    if r.status_code != 200:
        return
    admin = shp.Admin(shop, r.json()["access_token"], shp_api_version())
    orders = shp.fetch_orders(admin, date.today() - timedelta(days=365))
    check(len(orders) >= 300, f"orders read: {len(orders)} (want >= 300)")
    rows = shp.order_rows(orders)
    check(len(rows) >= len(orders), f"order lines: {len(rows)}")
    stocked = [x for x in rows if x["stock"] not in ("", None)]
    check(len(stocked) > 0.5 * len(rows), f"lines with tracked stock: {len(stocked)}/{len(rows)}")
    data = shp.rows_to_csv(rows)
    if os.environ.get("EXPORT_CSV"):   # order lines only (no customer data), for engine checks offline
        open(os.environ["EXPORT_CSV"], "wb").write(data)

    parsed = read_workbook(data)
    st = parsed["settings"]
    check(100 <= st["budget"] <= 1e6, f"budget default sized to the store: {st['budget']:.0f}")
    check(st["factory_moq"] <= 100, f"factory minimum default: {st['factory_moq']:.0f}")
    check("on_hand" in parsed["inp"]["products"].columns, "stock read into products")

    sold_year = sum(int(x["qty"]) for x in rows if not x["cancelled_at"] and x["status"] != "returned")
    v3, _, s3 = run_plan_on(data, {"plan_months": 3, "lead_weeks": 4, "budget": 1e6}, "live", 15)
    v0, _, s0 = run_plan_on(data, {"plan_months": 0, "budget": 1e6}, "live", 15)
    d3 = sum(s["f_mean"] for s in v3["styles"])
    d0 = sum(s["f_mean"] for s in v0["styles"])
    print(f"     sold last 12 months {sold_year}, demand next season {d0:.0f}, next ~4 months {d3:.0f}; "
          f"make {s0['units']} vs {s3['units']}")
    check(0.5 * sold_year <= d0 <= 2.5 * sold_year, "season demand within 0.5x-2.5x of last year's sales")
    check(0.15 * d0 <= d3 <= 0.6 * d0, "3-month window is a fraction of the season")
    check(s3["units"] < s0["units"], "3-month plan makes less than a season plan")
    stock = sum(r.get("stock", 0) for r in v3["variants"][v3["base"]]["style"])
    check(stock > 0, f"plan counts stock on hand ({stock})")
    check(v3.get("check") is not None, "accuracy check produced")
    from fasla import shopify_batch as shb
    mode = shb.detect_mode(rows)
    check(mode["mode"] == "clothing", f"test store detected as clothing ({mode['sized_share']:.0%} sized)")
    bres = shb.plan_from_csv(data, {}, months_ahead=4)
    check(len(bres["plan"]) > 0 and bres["plan"].make.ge(0).all(), f"batch mode runs on the same data ({len(bres['plan'])} rows)")
    if v3.get("check"):
        c = v3["check"]
        print(f"     accuracy: {c['style_error']:.0%} off per style vs {c['repeat_last_error']:.0%} repeating")
        check(c["style_error"] < 0.6, "forecast error per style under 60%")


def shp_api_version() -> str:
    from fasla.config import get_settings
    return get_settings().shopify_api_version


def site_checks() -> None:
    with httpx.Client(timeout=30, follow_redirects=True) as c:
        for path, want in [("/healthz", 200), ("/", 200), ("/shopify/", 200), ("/privacy", 200)]:
            try:
                r = c.get(SITE + path)
                check(r.status_code == want, f"{SITE}{path} -> {r.status_code}")
            except httpx.HTTPError as e:
                check(False, f"{SITE}{path} -> {e}")
        r = c.get(SITE + "/shopify/")
        check("frame-ancestors" in r.headers.get("content-security-policy", ""), "Shopify page sets frame-ancestors")
        r = c.post(SITE + "/webhooks/shopify", content=b"{}", headers={"x-shopify-hmac-sha256": "bad"})
        check(r.status_code == 401, f"webhook rejects a bad signature ({r.status_code})")


if __name__ == "__main__":
    if os.environ.get("SEED_CLIENT_SECRET"):
        store_checks()
    else:
        print("SKIP store checks (no SEED_CLIENT_SECRET)")
    site_checks()
    print(f"\n{len(failures)} failed" if failures else "\nall checks passed")
    sys.exit(1 if failures else 0)
