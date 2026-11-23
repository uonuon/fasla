"""Batch mode for Shopify stores that make food, oils, cosmetics: products made in batches, in packs (500 ml,
250 g), with flavours or scents, often with a shelf life. Separate from the clothing path on purpose: it reads
the same order lines Fasla already imports, and hands them to brandopt.batch.

Detection: a store whose order lines mostly carry real sizes (S, M, 38, XL...) is clothing; one whose lines are
mostly one-size or pack sizes (500 ml, 250 g) is batch. The owner can switch either way in the app.
"""
from __future__ import annotations

import io
import re

import pandas as pd

from brandopt.batch import BatchSettings, pack_amount, plan_batch

ONE_SIZE = ("one size", "onesize", "free size", "default title", "-", "")
CLOTHING_SIZES = re.compile(r"^(xxs|xs|s|m|l|xl|xxl|xxxl|[2-5]xl|\d{2}(/\d{2})?|\d{1,2}y|\d{1,2}-\d{1,2}y?)$", re.I)

# What the owner can set per product, with the defaults used when they leave it blank.
DEFAULTS = dict(batch_multiple=1, batch_min_units=0, shelf_life_months=0, lead_time_days=14, make_every_days=30)
LIMITS = dict(batch_multiple=(1, 10000), batch_min_units=(0, 100000), shelf_life_months=(0, 120),
              lead_time_days=(0, 365), make_every_days=(0, 365))


def detect_mode(rows: list[dict]) -> dict:
    """'clothing' or 'batch', with the share of units that carry a clothing size (for the app to explain)."""
    total = sized = packed = 0
    for r in rows:
        q = float(r.get("qty") or 0)
        z = str(r.get("size") or "").strip().lower()
        total += q
        if CLOTHING_SIZES.match(z):
            sized += q
        elif pack_amount(z)[0] is not None:
            packed += q
    share = sized / total if total else 0.0
    return {"mode": "clothing" if share >= 0.5 else "batch", "sized_share": round(share, 3),
            "packed_share": round(packed / total, 3) if total else 0.0}


def _frame(csv_bytes: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(csv_bytes), dtype=str, keep_default_na=False)
    for c in ("style", "size", "color", "qty", "price", "cost", "status", "cancelled_at", "stock", "date"):
        if c not in df.columns:
            df[c] = ""
    return df


def _pack(size: str) -> str:
    return "-" if size.strip().lower() in ONE_SIZE else size.strip()


def products_for_app(csv_bytes: bytes) -> list[dict]:
    """One row per product + variant + pack: price, known cost, stock and units sold, for the app's input table."""
    df = _frame(csv_bytes)
    df["product"], df["variant"], df["pack"] = df["style"], df["color"].replace("", "-"), df["size"].map(_pack)
    df["q"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
    out = []
    for (p, v, k), g in df.groupby(["product", "variant", "pack"], sort=False):
        cost = pd.to_numeric(g["cost"], errors="coerce").dropna()
        stock = pd.to_numeric(g["stock"], errors="coerce").dropna()
        out.append(dict(product=p, variant=v, pack=k, sold=int(g.q.sum()),
                        price=round(float(pd.to_numeric(g["price"], errors="coerce").median()), 2),
                        cost=round(float(cost.median()), 2) if len(cost) else None,
                        stock=int(stock.iloc[-1]) if len(stock) else None))
    return sorted(out, key=lambda r: -r["sold"])


def clean_inputs(per_product: dict) -> dict:
    """Owner's per-product inputs (keyed by product name), clipped to sane ranges; unknown keys dropped."""
    out = {}
    for name, vals in (per_product or {}).items():
        if not isinstance(vals, dict):
            continue
        row = {}
        for k, (lo, hi) in LIMITS.items():
            v = vals.get(k)
            if v in (None, ""):
                continue
            try:
                row[k] = min(max(float(v), lo), hi)
            except (TypeError, ValueError):
                raise ValueError(f"'{k}' for {str(name)[:40]} must be a number.")
        if vals.get("cost") not in (None, ""):
            try:
                c = float(vals["cost"])
            except (TypeError, ValueError):
                raise ValueError(f"The cost of {str(name)[:40]} must be a number.")
            if c <= 0:
                raise ValueError(f"The cost of {str(name)[:40]} must be above 0.")
            row["cost"] = c
        out[str(name)[:200]] = row
    return out


def plan_from_csv(csv_bytes: bytes, per_product: dict, budget: float | None = None, months_ahead: int = 4) -> dict:
    """Run the batch planner on Shopify order lines. Cancelled and returned lines are not demand.
    Stock is used only when the store keeps it (same rule as clothing: mostly zeros = untracked)."""
    df = _frame(csv_bytes)
    inputs = clean_inputs(per_product)
    gone = (df["cancelled_at"].str.strip() != "") | df["status"].str.lower().isin(("returned", "refused"))
    orders = pd.DataFrame({"date": df["date"], "product": df["style"], "variant": df["color"].replace("", "-"),
                           "pack": df["size"].map(_pack), "qty": df["qty"], "status": "delivered"})[~gone]
    rows = products_for_app(csv_bytes)
    stock = [r["stock"] for r in rows if r["stock"] is not None]
    use_stock = bool(stock) and sum(1 for s in stock if s > 0) / len(stock) >= 0.25
    prods, missing = [], []
    for r in rows:
        i = {**DEFAULTS, **inputs.get(r["product"], {})}
        cost = i.get("cost", r["cost"])
        if cost is None:
            missing.append(r["product"])
            continue
        prods.append(dict(product=r["product"], variant=r["variant"], pack=r["pack"], price=r["price"],
                          unit_cost=cost, on_hand=(r["stock"] or 0) if use_stock else 0,
                          batch_min_units=i["batch_min_units"], batch_multiple=int(i["batch_multiple"]),
                          shelf_life_months=i["shelf_life_months"], lead_time_days=i["lead_time_days"],
                          make_every_days=i["make_every_days"]))
    if missing:
        names = sorted(set(missing))
        raise ValueError("Add the cost to make each product. Missing: " + ", ".join(names[:8])
                         + (f" and {len(names) - 8} more." if len(names) > 8 else "."))
    st = BatchSettings(months_ahead=int(months_ahead))
    if budget:
        st.budget = float(budget)
    res = plan_batch(orders, pd.DataFrame(prods), st)
    if not use_stock and stock:
        res["notes"].append("Stock is 0 for almost every product, so it looks untracked: Fasla ignored it.")
    return res
