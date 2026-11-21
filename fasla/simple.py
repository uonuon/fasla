"""Orders-only uploads: a Shopify orders export, or any sheet with one row per item sold.

We map common column names, read size and colour out of Shopify's "Product - Colour / Size"
line-item names, and build the Products / Production / Couriers tables the planner needs:
  * Products: one row per style + colour + size seen in the orders; price = typical selling price;
    unit_cost from a cost column if there is one, else typed in by the owner in the app.
  * Production: unknown. We add every sold size with produced = 0, which the planner reads as
    "no stock limit known", so no size is treated as sold out.
  * Couriers: one fee per courier (typed in the app), the same in every zone.
Seasons: one per year, July to June (the same rule the full template uses without season dates).
"""
from __future__ import annotations

import io
import re

import numpy as np
import pandas as pd

from brandopt.forecast import SIZE_ORDER

DEFAULT_FEE = 60.0
RETURN_FEE_SHARE = 0.5   # couriers usually charge about half the fee to bring a refused parcel back

ALIASES = {
    "date": ["date", "order_date", "created_at", "created", "order_created_at", "paid_at", "day"],
    "style": ["style_id", "style", "product", "product_name", "product_title", "item", "item_name", "lineitem_name",
              "line_item_name", "title", "model"],
    "size": ["size", "variant_size", "option_size"],
    "color": ["color", "colour", "variant_color", "option_color"],
    "qty": ["qty", "quantity", "lineitem_quantity", "line_item_quantity", "units", "pieces", "count"],
    "price": ["price", "unit_price", "lineitem_price", "line_item_price", "item_price", "selling_price", "sale_price"],
    "zone": ["zone", "governorate", "city", "shipping_province_name", "shipping_province", "shipping_city", "region",
             "area", "state", "province"],
    "status": ["status", "order_status", "delivery_status", "shipping_status", "fulfillment_status"],
    "courier": ["courier", "shipping_company", "courier_name", "carrier", "shipping_carrier", "shipping_method",
                "delivery_company"],
    "cost": ["unit_cost", "cost", "cost_price", "cost_per_item", "item_cost"],
    "category": ["category", "product_type", "type", "collection"],
    "cancelled": ["cancelled_at", "canceled_at"],
    "financial": ["financial_status", "payment_status"],
}
REQUIRED = ["date", "style", "price"]
NAMES = {"date": "order date", "style": "product / style", "price": "price", "size": "size"}


class SimpleError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _norm(c) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_")


def read_table(data: bytes, filename: str = "") -> tuple[pd.DataFrame, bool]:
    """Return (table, is_full_template). CSV or xlsx."""
    if data[:4] == b"PK\x03\x04":
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, engine="openpyxl")
        names = {str(k).strip(): v for k, v in sheets.items()}
        # any template-only sheet means the owner used the full template: check it as one
        if {"Products", "Production", "Couriers", "Settings"} & set(names):
            return pd.DataFrame(), True
        df = names.get("Orders")
        if df is None:   # first sheet that has some rows
            df = next((v for v in names.values() if len(v.dropna(how="all"))), next(iter(names.values())))
        return df, False
    for enc in ("utf-8-sig", "cp1256", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sep = ";" if text[:2000].count(";") > text[:2000].count(",") else ","
    return pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False), False


def _pick(cols: list[str], key: str) -> str | None:
    for a in ALIASES[key]:
        if a in cols:
            return a
    return None


def _is_size(tok: str) -> bool:
    t = tok.strip().upper()
    return t in SIZE_ORDER or bool(re.fullmatch(r"\d{1,3}(\.\d)?", t)) or t in {"XXXXL", "4XL", "OS"}


def _split_variant(name: str) -> tuple[str, str | None, str | None]:
    """'Classic Tee - Black / M' -> ('Classic Tee', 'Black', 'M')."""
    name = str(name).strip()
    if " - " not in name:
        return name, None, None
    base, var = name.rsplit(" - ", 1)
    toks = [t.strip() for t in var.split("/") if t.strip()]
    size = next((t for t in toks if _is_size(t)), None)
    rest = [t for t in toks if t != size]
    return base.strip(), (rest[0] if rest else None), size


def _status(raw: str, cancelled: str, financial: str) -> str:
    s = f"{raw} {financial}".strip().lower()
    if str(cancelled).strip() and str(cancelled).strip().lower() not in ("nan", "none"):
        return "refused"
    if "exchang" in s:
        return "size_exchange"
    if any(w in s for w in ("refus", "cancel", "reject", "return", "rto", "fail", "void", "refund", "lost")):
        return "refused"
    return "delivered"


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(r"[^0-9.\-]", "", regex=True).replace("", np.nan), errors="coerce")


def parse_orders(df: pd.DataFrame, max_rows: int = 300_000) -> dict:
    """Map an orders table to the planner's tables. Raises SimpleError with fixable messages."""
    df = df.copy()
    df.columns = [_norm(c) for c in df.columns]
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]
    df = df.dropna(how="all")
    if len(df) > max_rows:
        raise SimpleError([f"The file has more than {max_rows:,} rows."])
    cols = list(df.columns)
    col = {k: _pick(cols, k) for k in ALIASES}
    # Shopify fills order-level fields (date, city, status…) only on an order's first line item
    order_col = next((c for c in ("name", "order_id", "order_number", "order", "order_no") if c in cols), None)
    if order_col:
        level = [col[k] for k in ("date", "zone", "status", "courier", "cancelled", "financial") if col[k]]
        if level:
            blanked = df[level].replace({"": np.nan, "nan": np.nan})
            df[level] = blanked.groupby(df[order_col].astype(str)).ffill().fillna("")
    # Shopify puts the order number in "name": never read it as the product
    errors = [f"We couldn't find a column for the {NAMES[k]}." for k in REQUIRED if not col[k]]
    if errors:
        errors.append("Columns we found: " + ", ".join(cols[:25]) + ("…" if len(cols) > 25 else "") + ".")
        raise SimpleError(errors)

    out = pd.DataFrame(index=df.index)
    out["date"] = pd.to_datetime(df[col["date"]].astype(str).str.slice(0, 19), errors="coerce")
    names = df[col["style"]].astype(str).str.strip()
    split = names.map(_split_variant)
    out["style_id"] = [b for b, _, _ in split]
    size_from_name = pd.Series([z for _, _, z in split], index=df.index)
    color_from_name = pd.Series([c for _, c, _ in split], index=df.index)
    out["size"] = (df[col["size"]].astype(str).str.strip() if col["size"] else size_from_name).replace({"": None, "nan": None})
    out["color"] = (df[col["color"]].astype(str).str.strip() if col["color"] else color_from_name).replace({"": None, "nan": None})
    out["qty"] = _num(df[col["qty"]]) if col["qty"] else 1.0
    out["price"] = _num(df[col["price"]])
    out["zone"] = df[col["zone"]].astype(str).str.strip().replace({"": "Unknown", "nan": "Unknown"}) if col["zone"] else "All zones"
    out["courier"] = (df[col["courier"]].astype(str).str.strip().replace({"": "Your courier", "nan": "Your courier"})
                      if col["courier"] else "Your courier")
    blank = pd.Series("", index=df.index)
    out["status"] = [_status(a, b, c) for a, b, c in zip(
        df[col["status"]].astype(str) if col["status"] else blank,
        df[col["cancelled"]].astype(str) if col["cancelled"] else blank,
        df[col["financial"]].astype(str) if col["financial"] else blank)]
    cost = _num(df[col["cost"]]) if col["cost"] else pd.Series(np.nan, index=df.index)
    cat = df[col["category"]].astype(str).str.strip().replace({"": "Other", "nan": "Other"}) if col["category"] else "Other"
    out["category"] = cat
    out["cost"] = cost

    warnings, errors = [], []
    # Shopify lists extra lines (shipping, gift cards) without a size: drop rows we can't use
    bad_date = out.date.isna()
    if bad_date.all():
        raise SimpleError(["None of the dates could be read. Use a date like 2025-10-03."])
    no_size = out["size"].isna()
    if no_size.all():
        raise SimpleError(["We couldn't find sizes. Add a 'size' column, or use names like 'Classic Tee - Black / M'."])
    bad = bad_date | no_size | out.price.isna() | (out.price <= 0) | out.qty.isna() | (out.qty <= 0) | (out.style_id == "")
    if bad.any():
        warnings.append(f"{int(bad.sum())} row(s) without a date, size, price or quantity were skipped.")
    out = out[~bad].copy()
    if len(out) < 1:
        raise SimpleError(["No usable order rows: each row needs a date, product, size, price and quantity."])
    out["color"] = out["color"].fillna("One colour")
    out["qty"] = out.qty.round().astype(int)
    out["style_id"] = out.style_id.str.slice(0, 80)

    p = (out.groupby(["style_id", "color", "size"], sort=False)
         .agg(price=("price", "median"), unit_cost=("cost", "median"), category=("category", "first")).reset_index())
    style_price = out.groupby("style_id").price.median()
    p["price"] = p.price.fillna(p.style_id.map(style_price)).round(2)
    style_cost = out.groupby("style_id").cost.median()
    p["unit_cost"] = p.unit_cost.fillna(p.style_id.map(style_cost))
    p.insert(1, "name", p.style_id)
    products = p[["style_id", "name", "category", "color", "size", "unit_cost", "price"]]

    yr = out.date.dt.year.where(out.date.dt.month >= 7, out.date.dt.year - 1)
    out["_season"] = yr.map(lambda y: f"Season {y}/{(y + 1) % 100:02d}")
    production = (out.groupby(["_season", "style_id", "color", "size"]).size().reset_index()
                  .rename(columns={"_season": "season"}).drop(columns=0))
    production["produced"] = 0
    orders = out.drop(columns=["cost", "category", "_season"])

    if not col["status"] and not col["cancelled"]:
        warnings.append("No order status column: we assumed every order was delivered, so refusals aren't counted.")
    if not col["zone"]:
        warnings.append("No city or zone column: delivery advice is for all zones together.")
    warnings.append("No production history: Fasla can't see which sizes sold out, so demand for your best sellers "
                    "may be a little low. The full template fixes this.")
    return dict(products=products, orders=orders, production=production, couriers=sorted(orders.courier.unique()),
                warnings=warnings, columns={k: v for k, v in col.items() if v})


def couriers_table(orders: pd.DataFrame, fees: dict | None) -> pd.DataFrame:
    fees = fees or {}
    rows = []
    for k in sorted(orders.courier.unique()):
        fee = float(fees.get(k, DEFAULT_FEE))
        for z in sorted(orders.zone.unique()):
            rows.append(dict(courier=k, zone=z, fee=fee, return_fee=round(fee * RETURN_FEE_SHARE, 2), remit_days=7))
    return pd.DataFrame(rows)


def style_summary(products: pd.DataFrame, orders: pd.DataFrame) -> list[dict]:
    """Per style: price, known cost, pieces sold last season (for the app's cost table)."""
    o = orders[orders.status.isin(["delivered", "size_exchange"])]
    last_start = o.date.max() - pd.Timedelta(days=365) if len(o) else None
    last = o[o.date > last_start].groupby("style_id").qty.sum() if last_start is not None else pd.Series(dtype=float)
    out = []
    for sid, g in products.groupby("style_id", sort=False):
        c = g.unit_cost.dropna()
        out.append(dict(id=sid, name=g.name.iloc[0], price=float(g.price.median()),
                        cost=None if c.empty else float(c.median()), sold_last_year=int(last.get(sid, 0))))
    out.sort(key=lambda s: -s["sold_last_year"])
    return out
