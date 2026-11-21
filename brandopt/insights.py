"""Returns analysis and cash-flow projection."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _wilson(k, n, z=1.64):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def returns_report(orders: pd.DataFrame, products: pd.DataFrame) -> dict:
    o = orders.copy()
    o["delivered"] = o.status.isin(["delivered", "size_exchange"])
    o["exchange"] = o.status.eq("size_exchange")
    o["refused"] = o.status.eq("refused")
    names = products.drop_duplicates("style_id").set_index("style_id").name
    base_ex = o.loc[o.delivered, "exchange"].mean()
    rows = []
    for sid, g in o[o.delivered].groupby("style_id"):
        k, n = int(g.exchange.sum()), len(g)
        lo, hi = _wilson(k, n)
        size_ex = g.groupby("size").exchange.mean()
        rows.append(dict(style_id=sid, name=names.get(sid, sid), delivered=n, exchange_rate=k / n,
                         low=lo, high=hi, flag=lo > 1.5 * base_ex,
                         worst_size=size_ex.idxmax(), worst_size_rate=size_ex.max()))
    ex = pd.DataFrame(rows).sort_values("exchange_rate", ascending=False)
    o["price_band"] = pd.cut(o.price, [0, 700, 1300, 10_000], labels=["< 700", "700-1300", "> 1300"])
    ref_zone = o.groupby("zone").refused.agg(["mean", "size"]).rename(columns={"mean": "refusal_rate", "size": "orders"})
    ref_price = o.groupby("price_band", observed=True).refused.mean().rename("refusal_rate").to_frame()
    return dict(exchanges=ex, base_exchange=base_ex, refusal_by_zone=ref_zone.sort_values("refusal_rate", ascending=False),
                refusal_by_price=ref_price, overall_refusal=o.refused.mean())


def cash_flow(q: np.ndarray, sku: pd.DataFrame, exp_sold: np.ndarray, start_cash: float, weeks_profile: np.ndarray,
              deposit_share: float = 0.5, lead_weeks: int = 4, remit_weeks: int = 1, ship_cost_per_order: float = 80,
              refusal_rate: float = 0.27, ads_share: float = 0.12, fixed_weekly: float = 15000) -> pd.DataFrame:
    """Weekly cash balance: deposit to factory now, balance on delivery, COD comes back after remittance."""
    c = sku.unit_cost.to_numpy(float)
    p = sku.price.to_numpy(float)
    prod_cost = float((q * c).sum())
    revenue = float((exp_sold * p).sum())
    units = float(exp_sold.sum())
    W = lead_weeks + len(weeks_profile) + remit_weeks + 1
    rows = []
    cash = start_cash
    for w in range(W):
        out = fixed_weekly
        note = ""
        if w == 0:
            out += deposit_share * prod_cost
            note = "factory deposit"
        if w == lead_weeks:
            out += (1 - deposit_share) * prod_cost
            note = "factory balance, stock arrives"
        sw = w - lead_weeks
        sales_units = units * weeks_profile[sw] if 0 <= sw < len(weeks_profile) else 0.0
        attempts = sales_units / (1 - refusal_rate) if sales_units else 0.0
        out += attempts * ship_cost_per_order + ads_share * revenue * (weeks_profile[sw] if 0 <= sw < len(weeks_profile) else 0)
        rw = w - lead_weeks - remit_weeks
        cash_in = revenue * weeks_profile[rw] if 0 <= rw < len(weeks_profile) else 0.0
        cash += cash_in - out
        rows.append(dict(week=w, cash_in=cash_in, cash_out=out, balance=cash, note=note))
    return pd.DataFrame(rows)
