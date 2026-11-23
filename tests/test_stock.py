"""Stock on hand: read from uploads and Shopify, subtracted from what to make, and used to spot sell-outs."""
import io

import numpy as np
import pandas as pd

from brandopt.excel import run_plan
from fasla.planner import read_workbook
from fasla.shopify import order_rows


def _orders_csv(stock: dict | None = None, seed: int = 3) -> bytes:
    rng = np.random.default_rng(seed)
    rows = []
    days = pd.date_range("2025-07-01", "2026-06-30")
    for i in range(900):
        d = days[rng.integers(len(days))]
        style = rng.choice(["Tee", "Hoodie", "Jeans"], p=[0.5, 0.3, 0.2])
        size = rng.choice(["S", "M", "L", "XL"], p=[0.2, 0.35, 0.3, 0.15])
        color = rng.choice(["Black", "White"])
        price = {"Tee": 450, "Hoodie": 900, "Jeans": 1000}[style]
        r = dict(order_id=f"#{i}", date=d.date().isoformat(), style=style, size=size, color=color, qty=1,
                 price=price, cost=price * 0.4, status="FULFILLED")
        if stock is not None:
            r["stock"] = stock.get((style, color, size), 0)
        rows.append(r)
    buf = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, index=False)
    return buf.getvalue().encode()


def _plan(data: bytes):
    inp = read_workbook(data)["inp"]
    inp["settings"].update(budget=5_000_000, factory_moq=0, min_per_color=0)
    return inp, run_plan(inp, n_scen=60, time_limit_s=10)


def test_stock_column_is_read_and_reduces_what_to_make():
    _, base = _plan(_orders_csv())
    big = {(s, c, z): 60 for s in ("Tee", "Hoodie", "Jeans") for c in ("Black", "White") for z in ("S", "M", "L", "XL")}
    inp, with_stock = _plan(_orders_csv(big))
    assert "on_hand" in inp["products"].columns
    assert with_stock["stock"].sum() == 60 * 24
    # the stock already covers a good part of demand: make clearly less, and the plan knows it holds stock
    assert with_stock["Q"].sum() < base["Q"].sum() - 500
    assert (with_stock["explain"].on_hand > 0).all()


def test_zero_stock_marks_sell_out_and_raises_demand():
    sold_out = {("Tee", "Black", "M"): 0}
    plenty = {(s, c, z): 50 for s in ("Tee", "Hoodie", "Jeans") for c in ("Black", "White") for z in ("S", "M", "L", "XL")}
    plenty.update(sold_out)
    inp = read_workbook(_orders_csv(plenty))["inp"]
    prod = inp["production"]
    hit = prod[(prod.style_id == "Tee") & (prod.color == "Black") & (prod["size"] == "M")]
    assert hit.produced.max() > 0          # recorded as a sell-out in the latest season
    others = prod[~prod.index.isin(hit.index)]
    assert (others.produced == 0).all()    # in stock: still "unknown production"


def test_shopify_rows_carry_tracked_stock_only():
    li = {"title": "Tee", "quantity": 1, "currentQuantity": 1, "variantTitle": "M / Black",
          "originalUnitPriceSet": {"shopMoney": {"amount": "450"}},
          "variant": {"selectedOptions": [{"name": "Size", "value": "M"}, {"name": "Color", "value": "Black"}],
                      "inventoryQuantity": 7, "inventoryItem": {"tracked": True, "unitCost": {"amount": "180"}}}}
    untracked = {**li, "variant": {**li["variant"], "inventoryQuantity": -3,
                                   "inventoryItem": {"tracked": False, "unitCost": None}}}
    o = {"name": "#1", "processedAt": "2026-03-01T10:00:00Z", "lineItems": {"nodes": [li, untracked]}}
    rows = order_rows([o])
    assert rows[0]["stock"] == 7 and rows[1]["stock"] == ""
