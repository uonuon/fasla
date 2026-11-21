"""How accurate is the forecast? Scored on simulated brands where the TRUE next-season demand is known.

Run:  python -m brandopt.accuracy [n_brands]

For each simulated brand we forecast season 3 from seasons 1-2 and compare with the true expected
demand, for existing styles (orders-only uploads can't see styles that never sold):
  * style WAPE  - weighted absolute % error of each style's season total (what drives cash)
  * SKU WAPE    - same per style x colour x size (what drives leftovers)
  * bias        - total forecast / total truth - 1 (negative = we under-forecast)
Variants: the full template (production known) and an orders-only upload (production unknown).
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from .forecast import build_forecast
from .sim import BrandWorld, default_styles


def orders_only(hist: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """What an orders-only upload gives the planner: products seen in orders, production unknown (0)."""
    o = hist["orders"]
    sold = o[["style_id", "color", "size"]].drop_duplicates()
    products = hist["products"].merge(sold, on=["style_id", "color", "size"])
    yr = o.date.dt.year.where(o.date.dt.month >= 7, o.date.dt.year - 1)
    prod = (o.assign(season=yr.map(lambda y: f"Season {y}/{(y + 1) % 100:02d}"))
            .groupby(["season", "style_id", "color", "size"]).size().reset_index().drop(columns=0))
    prod["produced"] = 0
    return products, prod


def score(fc: dict, truth: pd.DataFrame) -> dict:
    f = fc["sku"][["style_id", "color", "size", "mean"]].rename(columns={"mean": "f"})
    t = truth.rename(columns={"mean": "t"})
    m = t.merge(f, on=["style_id", "color", "size"], how="left").fillna({"f": 0.0})
    st = m.groupby("style_id")[["t", "f"]].sum()
    return dict(style_wape=float((st.f - st.t).abs().sum() / st.t.sum()),
                sku_wape=float((m.f - m.t).abs().sum() / m.t.sum()),
                bias=float(m.f.sum() / m.t.sum() - 1),
                sold_out_found=int(fc["corrected"].sold_out.sum()))


def run(n_brands: int = 8) -> pd.DataFrame:
    rows = []
    for seed in range(1, n_brands + 1):
        w = BrandWorld(default_styles(), seed=seed)
        hist = w.simulate_history()
        existing = {s.style_id for s in w.styles if not s.new}
        truth = w.expected_sku_demand(2)
        truth = truth[truth.style_id.isin(existing)]
        full = build_forecast(hist["orders"], hist["production"], hist["products"])
        rows.append(dict(brand=seed, variant="template (production known)", **score(full, truth)))
        p, prod = orders_only(hist)
        oo = build_forecast(hist["orders"], prod, p)
        rows.append(dict(brand=seed, variant="orders only", **score(oo, truth)))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    df = run(n)
    pd.set_option("display.width", 140)
    print(df.groupby("variant")[["style_wape", "sku_wape", "bias", "sold_out_found"]].mean().round(3))
