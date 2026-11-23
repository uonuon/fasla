"""Restock alerts (Growth and Scale): which sizes and colours will run out before new stock could arrive.

For every product with sales in the last 90 days and stock that Shopify tracks: expected sales over the factory
lead time plus one week (the time until the next check), from the same seasonal forecast the plan uses. If what
is on the shelf won't cover that, it's an alert: how many days the stock lasts, and the last day to start
production so new stock lands before it runs out.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from brandopt.forecast import SOLD_STATUSES, apply_window, build_forecast

from .planner import read_workbook

REVIEW_DAYS = 7          # alerts are checked weekly: cover until the next check too
RECENT_DAYS = 90         # products that haven't sold in 3 months aren't worth an alert


def restock_alerts(csv_bytes: bytes, lead_weeks: float = 4, today: date | None = None, limit: int = 50) -> dict:
    inp = read_workbook(csv_bytes)["inp"]
    o, p = inp["orders"], inp["products"]
    if "on_hand" not in p.columns:
        return {"tracked": False, "alerts": [], "checked": 0}
    end = o.date.max().normalize()
    today = pd.Timestamp(today or end.date())
    lead_days = max(int(round(lead_weeks * 7)), 1)
    horizon = lead_days + REVIEW_DAYS
    fc = build_forecast(o, inp["production"], p)
    fc = apply_window(fc, o, p, end + pd.Timedelta(days=1), horizon / 30.44)
    sku = fc["sku"].merge(p[["style_id", "name", "color", "size", "on_hand"]], on=["style_id", "color", "size"])
    sold = o[o.status.isin(SOLD_STATUSES)]
    recent = sold[sold.date > end - pd.Timedelta(days=RECENT_DAYS)].groupby(["style_id", "color", "size"]).qty.sum()
    last30 = sold[sold.date > end - pd.Timedelta(days=30)].groupby(["style_id", "color", "size"]).qty.sum()
    keys = list(zip(sku.style_id, sku.color, sku["size"]))
    sku["recent"] = [float(recent.get(k, 0)) for k in keys]
    sku["last30"] = [int(last30.get(k, 0)) for k in keys]
    sku = sku[sku.recent > 0]
    rate = (sku["mean"] / horizon).clip(lower=1e-9)
    sku = sku.assign(rate=rate, days_left=(sku.on_hand.astype(float) / rate))
    # worth an alert: at least ~2 sales expected before new stock could land, and the shelf won't cover them.
    # Biggest shortfall first (out-of-stock bestsellers before a slow size that's one piece short)
    sku = sku.assign(short=sku["mean"] - sku.on_hand.astype(float))
    low = sku[(sku["mean"] >= 1.5) & (sku.short > 0.5)].sort_values(["short", "days_left"], ascending=[False, True])
    out = []
    for r in low.head(limit).itertuples():
        days = int(np.floor(r.days_left))
        start_by = today + pd.Timedelta(days=days - lead_days)
        out.append(dict(style=str(r.name), color=str(r.color), size=str(r.size), stock=int(r.on_hand),
                        days_left=days, sold_last_30=int(r.last30),
                        need_next=int(np.ceil(r.mean)), short=int(np.ceil(r.short)),
                        start_by="now" if start_by <= today else str(start_by.date())))
    return {"tracked": True, "alerts": out, "checked": int(len(sku)), "lead_days": lead_days,
            "total": int(len(low)), "as_of": str(end.date())}
