"""A made-up Egyptian hair care brand selling on Instagram, for demos and tests of the batch planner.

14 months of orders, growing about 3% a month, with a Ramadan/Eid bump, cash-on-delivery refusals and
cancellations. Products are made by a toll manufacturer with a minimum batch per formula.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .batch import ramadan_share

PRODUCTS = [
    # product, variant, pack, price, unit_cost, share of orders, batch_min_amount (ml/g), multiple, shelf life
    ("Rosemary Hair Oil", "-", "100 ml", 320, 95, 0.26, 20000, 12, 24),
    ("Rosemary Hair Oil", "-", "250 ml", 650, 180, 0.12, 20000, 12, 24),
    ("Onion & Castor Oil", "-", "100 ml", 280, 80, 0.18, 15000, 12, 24),
    ("Argan Serum", "-", "50 ml", 390, 120, 0.14, 5000, 24, 18),
    ("Hair Mask", "Coconut", "250 g", 450, 140, 0.11, 25000, 12, 12),
    ("Hair Mask", "Vanilla", "250 g", 450, 140, 0.07, 25000, 12, 12),
    ("Hair Mask", "Coconut", "500 g", 790, 240, 0.05, 25000, 6, 12),
    ("Scalp Scrub", "Mint", "200 g", 380, 115, 0.07, 10000, 12, 9),
]


# A made-up food-in-jars brand (bone broth, fermented garlic honey, tallow, figs & olives). Prices are invented.
# product, variant, pack, price, unit_cost, share, batch_min_amount, multiple, shelf life (months), lead days,
# make every (days)
FOOD = [
    ("Bone Broth", "Beef", "500 ml", 260, 110, 0.20, 10000, 1, 3, 2, 7),
    ("Bone Broth", "Beef", "1 L", 480, 205, 0.10, 10000, 1, 3, 2, 7),
    ("Bone Broth", "Chicken", "500 ml", 220, 85, 0.14, 10000, 1, 3, 2, 7),
    ("Fermented Garlic Honey", "-", "250 g", 350, 150, 0.18, 5000, 1, 12, 30, 30),
    ("Fermented Garlic Honey", "-", "500 g", 650, 280, 0.09, 5000, 1, 12, 30, 30),
    ("Beef Tallow", "Plain", "200 g", 240, 90, 0.10, 4000, 1, 6, 5, 30),
    ("Beef Tallow", "Whipped", "200 g", 320, 110, 0.07, 4000, 1, 6, 5, 30),
    ("Figs & Olive", "-", "400 g", 300, 130, 0.12, 4000, 1, 9, 7, 30),
]


def sample_food_brand(seed: int = 5, end: str = "2026-11-21", months: int = 12):
    return sample_brand(seed, end, months, catalog=FOOD, base_orders=4.0, new_item=None)


def sample_brand(seed: int = 3, end: str = "2026-11-21", months: int = 14, catalog=None, base_orders: float = 9.0,
                 new_item="serum") -> tuple[pd.DataFrame, pd.DataFrame]:
    PRODUCTS = catalog or globals()["PRODUCTS"]
    rng = np.random.default_rng(seed)
    end_ts = pd.Timestamp(end)
    start = (end_ts - pd.DateOffset(months=months - 1)).replace(day=1)
    days = pd.date_range(start, end_ts, freq="D")
    shares = np.array([r[5] for r in PRODUCTS])
    shares = shares / shares.sum()
    rows = []
    oid = 1000
    for i, d in enumerate(days):
        months_in = (d.year - start.year) * 12 + d.month - start.month
        base = base_orders * 1.03 ** months_in
        base *= 1 + 0.45 * ramadan_share(d.replace(day=1)) * 2.2 * (1 if _in_ramadan(d) else 0.15)
        base *= 1.25 if d.dayofweek in (3, 4) else 1.0          # Thursday/Friday payday browsing
        if d.month == 11 and d.day >= 20:                       # White Friday
            base *= 1.6
        n = rng.poisson(base)
        for _ in range(n):
            oid += 1
            status = rng.choice(["delivered", "refused", "cancelled"], p=[0.8, 0.15, 0.05])
            lines = rng.choice([1, 2, 3], p=[0.62, 0.28, 0.10])
            picks = rng.choice(len(PRODUCTS), size=lines, replace=False, p=shares)
            for k in picks:
                pr = PRODUCTS[k]
                rows.append(dict(order_id=f"#{oid}", date=d.date(), product=pr[0], variant=pr[1], pack=pr[2],
                                 qty=int(rng.choice([1, 2], p=[0.85, 0.15])), status=status))
    orders = pd.DataFrame(rows)
    sold_last = orders[pd.to_datetime(orders.date) >= end_ts - pd.Timedelta(days=30)]
    last_month = sold_last.groupby(["product", "variant", "pack"]).qty.sum()
    prods = []
    for pr in PRODUCTS:
        lm = float(last_month.get((pr[0], pr[1], pr[2]), 0))
        prods.append(dict(product=pr[0], variant=pr[1], pack=pr[2], price=pr[3], unit_cost=pr[4],
                          on_hand=int(lm * rng.uniform(0.2, 0.9) * (0.25 if len(pr) > 10 and pr[10] <= 7 else 1)),
                          batch_min_amount=pr[6], batch_multiple=pr[7], shelf_life_months=pr[8],
                          lead_time_days=pr[9] if len(pr) > 9 else 21,
                          make_every_days=pr[10] if len(pr) > 10 else 0))
    if new_item:
        prods.append(dict(product="Hair Growth Serum", variant="-", pack="30 ml", price=420, unit_cost=130, on_hand=0,
                      batch_min_amount=3000, batch_multiple=24, shelf_life_months=18, lead_time_days=21,
                      expected_monthly_units=60))
    return orders, pd.DataFrame(prods)


def _in_ramadan(d: pd.Timestamp) -> bool:
    from .batch import RAMADAN_START
    s = RAMADAN_START.get(d.year)
    if not s:
        return False
    r0 = pd.Timestamp(s)
    return r0 - pd.Timedelta(days=7) <= d <= r0 + pd.Timedelta(days=32)


def sample_materials() -> tuple[pd.DataFrame, pd.DataFrame]:
    bom = pd.DataFrame([
        ("*", "*", "100 ml", "Amber bottle 100 ml", 1), ("*", "*", "250 ml", "Amber bottle 250 ml", 1),
        ("*", "*", "50 ml", "Dropper bottle 50 ml", 1), ("*", "*", "30 ml", "Dropper bottle 30 ml", 1),
        ("Hair Mask", "*", "250 g", "Jar 250 g", 1), ("Hair Mask", "*", "500 g", "Jar 500 g", 1),
        ("Scalp Scrub", "*", "200 g", "Jar 200 g", 1),
        ("*", "*", "*", "Label", 1), ("*", "*", "*", "Printed box", 1),
    ], columns=["product", "variant", "pack", "material", "qty_per_unit"])
    mats = pd.DataFrame([
        ("Amber bottle 100 ml", 400, 30, 100, 9), ("Amber bottle 250 ml", 150, 30, 100, 14),
        ("Dropper bottle 50 ml", 200, 45, 100, 16), ("Dropper bottle 30 ml", 0, 45, 100, 13),
        ("Jar 250 g", 120, 30, 50, 11), ("Jar 500 g", 40, 30, 50, 17), ("Jar 200 g", 60, 30, 50, 10),
        ("Label", 1500, 10, 500, 1.5), ("Printed box", 600, 21, 250, 6),
    ], columns=["material", "on_hand", "lead_time_days", "order_multiple", "unit_cost"])
    return bom, mats
