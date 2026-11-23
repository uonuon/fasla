"""How good is the rolling 'what to make for the next N months' forecast (the Shopify default)?

Synthetic stores where the TRUE expected demand is known: daily Poisson sales per style, with growth or decline,
a style-specific season (summer, winter, Eid, all year), Ramadan / Eid / White Friday peaks, and 6 to 24 months of
history. For each store and cut date we forecast the next `months` per style from the orders before the cut and
score against the true expected demand (not one noisy draw).

Run:  python -m brandopt.rolling_bench [n_stores]
"""
from __future__ import annotations

import io
import math
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

STYLES = [  # name, category, price, season
    ("Linen Wide-Leg Pants", "Bottoms", 890, "summer"), ("Oversized Cotton Tee", "Tops", 450, "summer"),
    ("Ribbed Tank Top", "Tops", 320, "summer"), ("Satin Midi Skirt", "Bottoms", 750, "eid"),
    ("Pleated Maxi Dress", "Dresses", 1450, "eid"), ("Embroidered Kaftan", "Dresses", 1650, "eid"),
    ("Everyday Abaya", "Abayas", 1250, "eid"), ("Modest Wrap Blouse", "Tops", 620, "all"),
    ("High-Waist Mom Jeans", "Bottoms", 980, "all"), ("Knit Cardigan", "Outerwear", 1150, "winter"),
    ("Puffer Jacket", "Outerwear", 1950, "winter"), ("Fleece Hoodie", "Tops", 850, "winter"),
]
SIZES, SIZE_W = ["S", "M", "L", "XL"], [0.18, 0.34, 0.31, 0.17]
COLORS = ["Black", "White", "Beige"]
RAMADAN = {2024: (date(2024, 3, 11), date(2024, 4, 9)), 2025: (date(2025, 3, 1), date(2025, 3, 30)),
           2026: (date(2026, 2, 18), date(2026, 3, 19)), 2027: (date(2027, 2, 8), date(2027, 3, 9))}
ADHA = {2024: date(2024, 6, 16), 2025: date(2025, 6, 6), 2026: date(2026, 5, 27), 2027: date(2027, 5, 16)}


def season_mult(season: str, d: date, amp: float) -> float:
    summer = 1.0 + amp * math.cos((d.month - 7) / 12 * 2 * math.pi)
    if season == "summer":
        return max(0.15, summer)
    if season == "winter":
        return max(0.1, 2.0 - summer)
    if season == "eid":
        r0, r1 = RAMADAN[d.year]
        return 2.8 if (r0 <= d <= r1 + timedelta(days=2) or timedelta(0) <= ADHA[d.year] - d <= timedelta(days=12)) else 0.6
    return 1.0


def store_mult(d: date) -> float:
    w = 1.25 if d.weekday() in (3, 4) else 1.0
    r0, r1 = RAMADAN[d.year]
    if r0 <= d <= r1:
        w *= 1.3 + 0.8 * (d - r0).days / (r1 - r0).days
    wf = date(d.year, 11, 28)
    if timedelta(0) <= d - wf <= timedelta(days=4):
        w *= 2.4
    return w


def make_store(seed: int, months: int, orders_per_year: float, growth: float, amp: float, end: date):
    """Returns (csv bytes, truth(d0, d1) -> expected units per style in [d0, d1))."""
    rnd = np.random.default_rng(seed)
    styles = [s for s in STYLES if rnd.random() < 0.85] or STYLES[:4]
    pop = {s[0]: rnd.uniform(0.5, 2.0) for s in styles}
    start = end - timedelta(days=int(months * 30.44))
    t0 = end  # growth measured relative to the end of history

    def rate(s, d):  # expected units per day
        return (orders_per_year / 365 * 1.25 / len(styles) * pop[s[0]] * growth ** ((d - t0).days / 365)
                * season_mult(s[3], d, amp) * store_mult(d))

    rows, n = [], 0
    d = start
    while d < end:
        for s in styles:
            k = rnd.poisson(rate(s, d))
            for _ in range(k):
                n += 1
                gone = rnd.random()
                rows.append((f"#{n}", d.isoformat(), s[0], rnd.choice(SIZES, p=SIZE_W), rnd.choice(COLORS),
                             1, s[2], round(s[2] * 0.4), s[1], "returned" if 0.06 < gone < 0.12 else "FULFILLED",
                             d.isoformat() if gone < 0.06 else "", ""))
        d += timedelta(days=1)
    df = pd.DataFrame(rows, columns=["order_id", "date", "style", "size", "color", "qty", "price", "cost", "category",
                                     "status", "cancelled_at", "stock"])

    def truth(a: date, b: date) -> pd.Series:
        out = {}
        for s in styles:
            tot, x = 0.0, a
            while x < b:
                tot += rate(s, x)
                x += timedelta(days=1)
            out[s[0]] = tot * 0.88   # kept share (cancelled and returned lines are not demand)
        return pd.Series(out)

    return df.to_csv(index=False).encode(), truth


def stores(n: int):
    rnd = np.random.default_rng(1)
    end = date(2026, 11, 22)
    for i in range(n):
        yield dict(seed=100 + i, months=int(rnd.choice([10, 12, 15, 18, 24])),
                   orders_per_year=float(rnd.choice([250, 800, 2500, 8000])),
                   growth=float(rnd.choice([0.8, 1.0, 1.2, 1.5])), amp=float(rnd.uniform(0.3, 0.9)),
                   end=end - timedelta(days=int(rnd.integers(0, 330))))


def engine_window(inp: dict, cut: pd.Timestamp, months: float) -> pd.Series:
    from .forecast import apply_window, build_forecast
    o = inp["orders"]
    tr = o[o.date < cut]
    f = build_forecast(tr, inp["production"], inp["products"])
    f = apply_window(f, tr, inp["products"], cut, months)
    return f["sku"].groupby("style_id")["mean"].sum()


def naive_window(inp: dict, cut: pd.Timestamp, months: float) -> pd.Series:
    o = inp["orders"]
    sold = o[o.status.isin(("delivered", "size_exchange"))]
    span = pd.Timedelta(days=int(months * 30.44))
    return sold[(sold.date < cut) & (sold.date >= cut - span)].groupby("style_id").qty.sum()


def run(n: int = 24, months: float = 3, methods: dict | None = None) -> pd.DataFrame:
    from fasla.planner import read_workbook
    methods = methods or {"engine": engine_window, "repeat last": naive_window}
    out = []
    for cfg in stores(n):
        csv, truth = make_store(**cfg)
        inp = read_workbook(csv)["inp"]
        end = inp["orders"].date.max().normalize() + pd.Timedelta(days=1)
        for back in (0, 1):   # two cuts: the window just before the end, and one a month earlier
            cut = end - pd.Timedelta(days=int((months + back) * 30.44))
            if (cut - inp["orders"].date.min()).days < 240:
                continue
            t = truth(cut.date(), (cut + pd.Timedelta(days=int(months * 30.44))).date())
            for name, fn in methods.items():
                p = fn(inp, cut, months).reindex(t.index).fillna(0)
                out.append(dict(store=cfg["seed"], hist=cfg["months"], vol=cfg["orders_per_year"], growth=cfg["growth"],
                                cut=str(cut.date()), method=name, wape=float((p - t).abs().sum() / t.sum()),
                                bias=float(p.sum() / t.sum() - 1)))
    return pd.DataFrame(out)


if __name__ == "__main__":
    r = run(int(sys.argv[1]) if len(sys.argv) > 1 else 24)
    print(r.groupby("method")[["wape", "bias"]].mean().round(3))
    print(r.pivot_table(index=["growth"], columns="method", values="wape").round(3))
    print(r.pivot_table(index=["hist"], columns="method", values="wape").round(3))
    print(r.pivot_table(index=["vol"], columns="method", values="wape").round(3))
