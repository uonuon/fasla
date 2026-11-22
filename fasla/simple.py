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
    "price": ["price", "unit_price", "lineitem_price", "line_item_price", "item_price", "selling_price", "sale_price",
              "order_item_price", "product_price"],
    "zone": ["zone", "governorate", "city", "shipping_province_name", "shipping_province", "shipping_city", "region",
             "area", "state", "province", "city_shipping", "state_shipping", "shipping_state", "billing_city",
             "city_billing", "state_billing"],
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
    def __init__(self, errors: list[str], columns_problem: bool = False):
        super().__init__("; ".join(errors))
        self.errors = errors
        self.columns_problem = columns_problem   # True when a smarter column mapping could fix it


def _norm(c) -> str:
    # keep letters in any script (Arabic headers must survive for the AI to read them)
    return re.sub(r"[^\w]+", "_", str(c).strip().lower()).strip("_")


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


AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫", "01234567890123456789.")
SIZE_WORDS = {
    "SMALL": "S", "MEDIUM": "M", "MED": "M", "LARGE": "L", "XLARGE": "XL", "X-LARGE": "XL", "X LARGE": "XL",
    "XXLARGE": "XXL", "XX-LARGE": "XXL", "2X": "XXL", "3X": "3XL", "EXTRA LARGE": "XL", "FREE": "FREE SIZE",
    "ONE-SIZE": "ONE SIZE", "ONESIZE": "ONE SIZE", "سمول": "S", "سمال": "S", "صغير": "S", "ميديم": "M", "ميديوم": "M",
    "متوسط": "M", "وسط": "M", "لارج": "L", "كبير": "L", "اكس لارج": "XL", "إكس لارج": "XL", "اكس اكس لارج": "XXL",
    "٢ اكس لارج": "XXL", "2 اكس لارج": "XXL", "3 اكس لارج": "3XL", "فري سايز": "FREE SIZE", "مقاس واحد": "ONE SIZE",
}


def clean_size(v) -> str | None:
    t = str(v).translate(AR_DIGITS).strip()
    if not t or t.lower() in ("nan", "none"):
        return None
    t = re.sub(r"^(size|مقاس)\s*[:\-]?\s*", "", t, flags=re.I).strip()
    u = t.upper()
    return SIZE_WORDS.get(u, SIZE_WORDS.get(t, u))


def parse_dates(s: pd.Series) -> pd.Series:
    """Dates as brands write them: ISO, Shopify's '+0200', Excel dates, and Egypt's day-first 05/11/2025."""
    t = s.astype(str).str.translate(AR_DIGITS).str.strip().str.slice(0, 19)
    if pd.api.types.is_datetime64_any_dtype(s):
        return pd.to_datetime(s, errors="coerce")
    num = pd.to_numeric(t, errors="coerce")
    if num.notna().mean() > 0.8 and num.between(30000, 60000).mean() > 0.8:   # Excel serial days
        return pd.to_datetime(num, unit="D", origin="1899-12-30", errors="coerce")
    slash = t.str.match(r"^\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}")
    if slash.mean() > 0.5:
        parts = t[slash].str.extract(r"^(\d{1,2})[/.\-](\d{1,2})")
        a, b = parts[0].astype(int), parts[1].astype(int)
        dayfirst = not ((b > 12).any() and not (a > 12).any())   # Egypt writes day/month unless the data says otherwise
        return pd.to_datetime(t.str.replace(r"[.]", "/", regex=True), dayfirst=dayfirst, errors="coerce", format="mixed")
    return pd.to_datetime(t, errors="coerce", format="mixed")


def find_header(raw: pd.DataFrame) -> pd.DataFrame:
    """Hand-made sheets often have a title and blank rows above the real headers: find the header row."""
    cols = [str(c) for c in raw.columns]
    unnamed = sum(c.startswith("Unnamed") or c.strip() == "" or re.fullmatch(r"\d+", c) is not None for c in cols)
    if unnamed < max(2, len(cols) // 2):
        return raw
    head = raw.head(15)
    for i, row in enumerate(head.itertuples(index=False)):
        vals = [v for v in row if str(v).strip() not in ("", "nan", "None")]
        texts = [v for v in vals if isinstance(v, str) and not re.fullmatch(r"[\d.,\s]+", v)]
        if len(texts) >= max(3, int(0.6 * len(cols))):
            body = raw.iloc[i + 1:].copy()
            body.columns = [str(v).strip() if str(v).strip() not in ("", "nan") else f"column_{j + 1}" for j, v in enumerate(row)]
            return body.reset_index(drop=True)
    return raw


def melt_sizes(df: pd.DataFrame) -> pd.DataFrame:
    """One column per size (S, M, L…) holding quantities -> one row per size."""
    size_cols = [c for c in df.columns if clean_size(c) and _is_size(clean_size(c))]
    if len(size_cols) < 2 or _pick(list(df.columns), "size"):
        return df
    rest = [c for c in df.columns if c not in size_cols]
    long = df.melt(id_vars=rest, value_vars=size_cols, var_name="size", value_name="qty")
    long["qty"] = pd.to_numeric(long["qty"].astype(str).str.translate(AR_DIGITS), errors="coerce")
    return long[long["qty"] > 0].reset_index(drop=True)


ITEM_RE = re.compile(r"(\d+)\s*[×xX]\s*(.+?)(?=,\s*\d+\s*[×xX]\s|$)")


def explode_items(df: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """WooCommerce Analytics puts every item of an order in one cell: '2× Tee - Black, M, 1× Hoodie - Grey, L'."""
    for c in df.columns:
        vals = df[c].astype(str)
        if vals.str.match(r"^\s*\d+\s*[×xX]\s").mean() < 0.6:
            continue
        total = next((x for x in ("net_sales", "total_sales", "order_total", "total", "sales") if x in df.columns), None)
        pieces = next((x for x in ("items_sold", "items", "item_count") if x in df.columns), None)
        rows = []
        for idx, cell in vals.items():
            items = ITEM_RE.findall(cell)
            n = sum(int(q) for q, _ in items) or 1
            amount = _num(pd.Series([df.at[idx, total]])).iloc[0] if total else np.nan
            if pieces:
                n = _num(pd.Series([df.at[idx, pieces]])).iloc[0] or n
            for q, name in items:
                r = df.loc[idx].to_dict()
                r.update({"_item": name.strip(), "_qty": int(q), "_price": amount / n if n else np.nan})
                rows.append(r)
        out = pd.DataFrame(rows).drop(columns=[c] + [x for x in (total, pieces) if x])
        return out.rename(columns={"_item": "item_name", "_qty": "quantity", "_price": "price"}), True
    return df, False


def _pick(cols: list[str], key: str) -> str | None:
    for a in ALIASES[key]:
        if a in cols:
            return a
    return None


def _is_size(tok: str) -> bool:
    t = str(tok).strip().upper()
    return t in SIZE_ORDER or bool(re.fullmatch(r"\d{1,3}(\.\d)?", t)) or t in {"XXXXL", "4XL", "OS"}


def _split_variant(name: str) -> tuple[str, str | None, str | None]:
    """'Classic Tee - Black / M' -> ('Classic Tee', 'Black', 'M')."""
    name = str(name).strip()
    if " - " not in name:
        return name, None, None
    base, var = name.rsplit(" - ", 1)
    toks = [t.strip() for t in re.split(r"[/,]", var) if t.strip()]   # Shopify 'Black / M', WooCommerce 'Black, M'
    size = next((clean_size(t) for t in toks if _is_size(clean_size(t) or "")), None)
    toks = [t for t in toks if clean_size(t) != size]
    return base.strip(), (toks[0] if toks else None), size


REFUSED_WORDS = ("refus", "cancel", "reject", "return", "rto", "fail", "void", "refund", "lost")
DELIVERED_WORDS = ("deliver", "fulfil", "complete", "paid", "success", "done", "closed", "shipped", "received",
                   "authorized", "pending", "unfulfilled", "partial")
STATUSES = ("delivered", "refused", "size_exchange")


def _known(s) -> bool:
    s = "" if not isinstance(s, str) else s
    return not s or "exchang" in s or any(w in s for w in REFUSED_WORDS + DELIVERED_WORDS)


def _status(raw: str, cancelled: str, financial: str, status_map: dict | None = None) -> str:
    if str(cancelled).strip() and str(cancelled).strip().lower() not in ("nan", "none"):
        return "refused"
    key = str(raw).strip().lower()
    if status_map and key in status_map and status_map[key] in STATUSES:
        return status_map[key]
    s = f"{raw} {financial}".strip().lower()
    if "exchang" in s:
        return "size_exchange"
    if any(w in s for w in REFUSED_WORDS):
        return "refused"
    return "delivered"


def _num(s: pd.Series) -> pd.Series:
    t = s.astype(str).str.translate(AR_DIGITS).str.replace(r"(?<=\d),(?=\d{3}\b)", "", regex=True)
    return pd.to_numeric(t.str.replace(r"[^0-9.\-]", "", regex=True).replace("", np.nan), errors="coerce")


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = find_header(df.copy())
    df.columns = [_norm(c) or f"column_{i + 1}" for i, c in enumerate(df.columns)]
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]
    df = df.dropna(how="all")
    df, _ = explode_items(df)
    return melt_sizes(df)


def ai_sample(table: pd.DataFrame, rows: int = 8) -> dict:
    """What the AI needs to suggest a column mapping: headers, a few rows, and status-like values."""
    df = normalize(table)
    sample = df.head(rows).astype(str).map(lambda v: v[:60]).values.tolist()
    return dict(headers=list(df.columns)[:80], original=[str(c)[:60] for c in table.columns][:80], sample=sample)


def unknown_statuses(table: pd.DataFrame, columns: dict | None = None, limit: int = 40) -> list[str]:
    """Status values our keyword rules don't recognise (e.g. Arabic), most frequent first."""
    df = normalize(table)
    c = (columns or {}).get("status") or _pick(list(df.columns), "status")
    if not c or c not in df.columns:
        return []
    vals = df[c].fillna("").astype(str).str.strip().str.lower()
    vals = vals[(vals != "") & (vals != "nan")]
    unk = vals[~vals.map(_known)]
    return unk.value_counts().head(limit).index.tolist()


def parse_orders(df: pd.DataFrame, max_rows: int = 300_000, columns: dict | None = None,
                 status_map: dict | None = None) -> dict:
    """Map an orders table to the planner's tables. Raises SimpleError with fixable messages.
    `columns` (field -> column name, e.g. from the AI) wins over the built-in name rules."""
    df = normalize(df)
    if len(df) > max_rows:
        raise SimpleError([f"The file has more than {max_rows:,} rows."])
    cols = list(df.columns)
    col = {k: _pick(cols, k) for k in ALIASES}
    for k, v in (columns or {}).items():
        if k in ALIASES and v in cols:
            col[k] = v
    status_map = {str(k).strip().lower(): v for k, v in (status_map or {}).items()}
    # Shopify fills order-level fields (date, city, status…) only on an order's first line item
    order_col = next((c for c in ("name", "order_id", "order_number", "order", "order_no") if c in cols), None)
    if order_col:
        level = [col[k] for k in ("date", "zone", "status", "courier", "cancelled", "financial") if col[k]]
        if level:
            blanked = df[level].replace({"": np.nan, "nan": np.nan})
            df[level] = blanked.groupby(df[order_col].astype(str)).ffill().fillna("")
    # WooCommerce "Item Cost" is the selling price per piece; only a price column wins over it
    if not col["price"] and "item_cost" in cols:
        col["price"] = "item_cost"
        if col["cost"] == "item_cost":
            col["cost"] = None
    if not col["price"]:
        line_total = next((c for c in ("order_line_total", "line_total", "lineitem_total", "item_total", "total_price",
                                       "subtotal", "line_subtotal") if c in cols), None)
        if line_total:
            qty = _num(df[col["qty"]]) if col["qty"] else 1
            df["_unit_price"] = _num(df[line_total]) / qty
            col["price"] = "_unit_price"
    courier_file = any(c in cols for c in ("tracking_number", "awb", "tracking_no", "رقم_الشحنة"))
    names_have_sizes = bool(col["style"]) and df[col["style"]].astype(str).map(lambda v: _split_variant(v)[2]).notna().any()
    if courier_file and not col["size"] and not names_have_sizes:
        raise SimpleError([
            "This looks like a courier export (tracking numbers and cash on delivery). It doesn't say which "
            "products and sizes were sold. Upload your store's orders export (Shopify, WooCommerce, EasyOrders "
            "or your own sheet) instead."])
    # Shopify puts the order number in "name": never read it as the product
    errors = [f"We couldn't find a column for the {NAMES[k]}." for k in REQUIRED if not col[k]]
    if errors:
        errors.append("Columns we found: " + ", ".join(cols[:25]) + ("…" if len(cols) > 25 else "") + ".")
        raise SimpleError(errors, columns_problem=True)

    out = pd.DataFrame(index=df.index)
    out["date"] = parse_dates(df[col["date"]])
    names = df[col["style"]].astype(str).str.strip()
    split = names.map(_split_variant)
    out["style_id"] = [b for b, _, _ in split]
    size_from_name = pd.Series([z for _, _, z in split], index=df.index)
    color_from_name = pd.Series([c for _, c, _ in split], index=df.index)
    out["size"] = (df[col["size"]].map(clean_size) if col["size"] else size_from_name).replace({"": None, "nan": None})
    out["color"] = (df[col["color"]].astype(str).str.strip() if col["color"] else color_from_name).replace({"": None, "nan": None})
    out["qty"] = _num(df[col["qty"]]) if col["qty"] else 1.0
    out["price"] = _num(df[col["price"]])
    out["zone"] = df[col["zone"]].astype(str).str.strip().replace({"": "Unknown", "nan": "Unknown"}) if col["zone"] else "All zones"
    out["courier"] = (df[col["courier"]].astype(str).str.strip().replace({"": "Your courier", "nan": "Your courier"})
                      if col["courier"] else "Your courier")
    blank = pd.Series("", index=df.index)
    out["status"] = [_status(a, b, c, status_map) for a, b, c in zip(
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
        raise SimpleError(["None of the dates could be read. Use a date like 2025-10-03."], columns_problem=True)
    no_size = out["size"].isna()
    if no_size.all():
        raise SimpleError(["We couldn't find sizes. Add a 'size' column, or use names like 'Classic Tee - Black / M'."],
                          columns_problem=True)
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
    warnings.append("No production numbers: Fasla estimates which sizes sold out from when they stopped selling. "
                    "Adding how many you produced (full template) makes demand for best sellers more accurate.")
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


def minimal_csv(table: pd.DataFrame, used: dict) -> bytes:
    """Only the columns Fasla reads (plus the order number, which links an order's lines). Everything else,
    such as customer names, phones, emails and addresses, is dropped before the file is stored."""
    norm = [_norm(c) or f"column_{i + 1}" for i, c in enumerate(table.columns)]
    keep_norm = set(used.values()) | {c for c in ("name", "order_id", "order_number", "order", "order_no") if c in norm}
    keep = [orig for orig, n in zip(table.columns, norm) if n in keep_norm]
    return table[keep].to_csv(index=False).encode("utf-8")


PERSONAL_HINTS = ("email", "phone", "mobile", "billing", "address", "customer", "first_name", "last_name", "zip",
                  "postal", "company", "note", "tax", "national_id", "ip_address", "الهاتف", "الاسم", "العنوان",
                  "تليفون", "موبايل")


def personal_columns(table: pd.DataFrame, used: dict | None = None) -> list[str]:
    """Columns that look like customer personal data and that Fasla does not use (they are dropped)."""
    keep = set((used or {}).values())
    return [str(c) for c in table.columns
            if _norm(c) not in keep and any(h in _norm(c) for h in PERSONAL_HINTS)]


_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", re.I)
_PHONE = re.compile(r"\+?[\d\s\-()]{10,20}")


def _is_phone(s: str) -> bool:
    s = s.strip().translate(AR_DIGITS)
    if s.endswith(".0"):
        s = s[:-2]
    return bool(_PHONE.fullmatch(s)) and 10 <= len(re.sub(r"\D", "", s)) <= 13


def _safe_value(v) -> str:
    s = str(v).strip().translate(AR_DIGITS)[:40]
    if _EMAIL.search(s):
        return "<email>"
    if _is_phone(s):
        return "<phone>"
    return s


def _namey(c) -> bool:
    """A 'name' column that isn't about the product is probably a person's name."""
    n = _norm(c)
    return (("name" in n or "اسم" in n or "عميل" in n) and n != "name"
            and not any(w in n for w in ("product", "item", "style", "variant", "sku", "lineitem", "province", "city", "zone", "courier",
                                     "منتج", "صنف", "موديل")))


def file_shape(data: bytes, max_cols: int = 60) -> dict:
    """What a file looks like, for fixing the reader when it fails: sheet names, headers, header row,
    row count, and a few example values per column. Never includes customer details: columns that look
    personal show their header only, and any email or phone-like value is masked. Never raises."""
    out: dict = {"bytes": len(data), "kind": "xlsx" if data[:4] == b"PK\x03\x04" else "csv"}
    try:
        if out["kind"] == "xlsx":
            sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, engine="openpyxl", nrows=200)
            out["sheets"] = {str(k): int(len(v)) for k, v in list(sheets.items())[:10]}
            raw = max(sheets.values(), key=len) if sheets else pd.DataFrame()
        else:
            head = data[:4096]
            out["encoding"] = "utf-8-sig" if head.startswith(b"\xef\xbb\xbf") else "utf-8"
            out["delimiter"] = max(",;\t|", key=lambda d: head.count(d.encode()))
            raw, _ = read_table(data)
            raw = raw.head(200)
        out["raw_headers"] = [str(c)[:60] for c in raw.columns[:max_cols]]
        body = find_header(raw)
        out["header_row_found"] = body is not raw
        out["rows_sampled"] = int(len(body))
        personal = set(personal_columns(body)) | {str(c) for c in body.columns if _namey(c)}
        cols = []
        for c in list(body.columns)[:max_cols]:
            col = body[c]
            ent = {"name": str(c)[:60], "norm": _norm(c)[:60], "filled": round(float(col.notna().mean()), 2) if len(col) else 0}
            if str(c) in personal:
                ent["personal"] = True
            else:
                vals = [x for x in col.dropna().astype(str).unique()[:4] if x.strip() not in ("", "nan")]
                ent["examples"] = [_safe_value(x) for x in vals[:3]]
            cols.append(ent)
        out["columns"] = cols
    except Exception as e:  # noqa: BLE001
        out["shape_error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return out


def _personal_header(v) -> bool:
    n = _norm(v)
    return bool(n) and (any(h in n for h in PERSONAL_HINTS) or _namey(v))


def scrub_file(data: bytes) -> tuple[bytes, str]:
    """A copy of an uploaded file that an owner chose to share so we can fix the reader: same layout
    (title rows, sheets, headers), but columns under a personal header are blanked and any email- or
    phone-like value anywhere is masked. Returns (bytes, ext)."""
    is_xlsx = data[:4] == b"PK\x03\x04"
    if is_xlsx:
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, engine="openpyxl", dtype=object)
    else:
        t, _ = read_table(data)
        sheets = {"data": pd.concat([pd.DataFrame([list(t.columns)]), pd.DataFrame(t.values)], ignore_index=True)}
    out = {}
    for name, df in sheets.items():
        df = df.copy()
        for j in df.columns:
            col = df[j]
            hdr = next((i for i, v in enumerate(col.head(15)) if isinstance(v, str) and _personal_header(v)), None)
            if hdr is not None:
                df.loc[df.index > hdr, j] = "<removed>"
            df[j] = [v if (v is None or (isinstance(v, float) and np.isnan(v))) else
                     (_safe_value(v) if _EMAIL.search(str(v)) or _is_phone(str(v)) else v)
                     for v in df[j]]
        out[str(name)[:31]] = df
    buf = io.BytesIO()
    if is_xlsx:
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            for n, df in out.items():
                df.to_excel(w, sheet_name=n, header=False, index=False)
        return buf.getvalue(), "xlsx"
    df = next(iter(out.values()))
    return df.to_csv(index=False, header=False).encode("utf-8"), "csv"
