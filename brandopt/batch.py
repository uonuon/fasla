"""Monthly production plan for brands that make products in batches: hair oils, creams, perfumes, food in jars.

Clothing brands plan once per season (style x colour x size). Batch brands make the same products again
and again, so the question is "how much of each product do we make this month?". The plan here:

  1. Forecast monthly demand per SKU (product x variant x pack) from the brand's order history:
     brand total per month with a damped trend and a Ramadan effect, shared out to SKUs with shares that
     mix each SKU's recent sales with its long-run share (so a SKU with few orders is not over-trusted).
  2. Turn the forecast into demand scenarios (brand-level surprise + Poisson noise per SKU).
  3. Target stock = the demand quantile that balances a lost sale (margin) against holding a unit for a
     month (holding cost), over the months until the next batch can arrive. Make = target - stock on hand.
  4. Batch rules per product x variant: a minimum batch (units, or ml / g of product), whole multiples,
     and shelf life: never make more than is likely to sell before it expires.
  5. Cash: if the plan costs more than the budget, drop the units that earn least per pound spent.
  6. Materials (optional): bottles, labels, oils per unit -> what to order and the last day to order it.

Leftover units are not wasted (they are next month's stock), which is why the target uses a holding cost,
not a clearance price as in the seasonal clothing model.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

# First day of Ramadan (Egypt) by year. Ramadan lasts 29-30 days; Eid al-Fitr follows.
RAMADAN_START = {
    2019: "2019-05-06", 2020: "2020-04-24", 2021: "2021-04-13", 2022: "2022-04-02", 2023: "2023-03-23",
    2024: "2024-03-11", 2025: "2025-03-01", 2026: "2026-02-18", 2027: "2027-02-08", 2028: "2028-01-28",
    2029: "2029-01-16", 2030: "2030-01-06",
}
NOT_DEMAND = ("cancelled", "canceled", "duplicate", "test", "ملغي", "ملغى")


def ramadan_share(month_start: pd.Timestamp) -> float:
    """Share of the days of this month that fall in Ramadan or the 3 days of Eid al-Fitr."""
    m0 = pd.Timestamp(month_start).normalize().replace(day=1)
    m1 = m0 + pd.offsets.MonthBegin(1)
    days = 0
    for y in (m0.year - 1, m0.year, m0.year + 1):
        s = RAMADAN_START.get(y)
        if not s:
            continue
        r0 = pd.Timestamp(s)
        r1 = r0 + pd.Timedelta(days=33)
        lo, hi = max(m0, r0), min(m1, r1)
        days += max((hi - lo).days, 0)
    return days / (m1 - m0).days


def pack_amount(pack) -> tuple[float | None, str]:
    """'250 ml' -> (250, 'ml'); '1 L' -> (1000, 'ml'); '500g' -> (500, 'g'); '١٠٠ مل' -> (100, 'ml')."""
    t = str(pack).strip().lower()
    t = t.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789."))
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(ml|مل|l|لتر|ltr|g|gm|gr|جم|جرام|kg|كجم|كيلو|oz)?", t)
    if not m:
        return None, ""
    v = float(m.group(1).replace(",", "."))
    u = m.group(2) or ""
    if u in ("l", "لتر", "ltr"):
        return v * 1000, "ml"
    if u in ("kg", "كجم", "كيلو"):
        return v * 1000, "g"
    if u in ("ml", "مل"):
        return v, "ml"
    if u in ("g", "gm", "gr", "جم", "جرام"):
        return v, "g"
    if u == "oz":
        return v * 29.57, "ml"
    return v, ""


@dataclass
class BatchSettings:
    plan_start: date | None = None        # day the batch is made; default: 7 days after the last order
    months_ahead: int = 4                 # months to forecast and show
    review_months: float = 1.0            # how often you produce (1 = every month)
    budget: float = math.inf              # cash for this batch (EGP)
    holding_cost_month: float = 0.03      # cost of keeping a unit one more month, share of unit cost
    max_service: float = 0.95             # never aim higher than this chance of not running out
    expiry_margin_months: float = 1.0     # stop selling this long before expiry
    n_scenarios: int = 400
    seed: int = 1
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------- reading input
_COLS = {
    "date": ("date", "order date", "created", "created at", "تاريخ", "التاريخ", "تاريخ الطلب"),
    "product": ("product", "product name", "item", "منتج", "المنتج", "اسم المنتج", "الصنف"),
    "variant": ("variant", "scent", "flavour", "flavor", "type", "النوع", "الريحة", "الرائحة", "النكهة"),
    "pack": ("pack", "size", "pack size", "volume", "weight", "الحجم", "العبوة", "الوزن", "المقاس"),
    "qty": ("qty", "quantity", "pieces", "units", "الكمية", "عدد", "العدد"),
    "status": ("status", "order status", "الحالة", "حالة الطلب"),
}


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    ren = {}
    for c in df.columns:
        k = str(c).strip().lower()
        for std, names in _COLS.items():
            if k == std or k in names:
                ren[c] = std
                break
    return df.rename(columns=ren)


def clean_orders(orders: pd.DataFrame) -> pd.DataFrame:
    o = normalize_columns(orders.copy())
    missing = [c for c in ("date", "product", "qty") if c not in o.columns]
    if missing:
        raise ValueError(f"Orders sheet needs columns: {', '.join(missing)}")
    for c in ("variant", "pack"):
        if c not in o.columns:
            o[c] = "-"
    o["date"] = pd.to_datetime(o["date"], errors="coerce", dayfirst=True)
    o["qty"] = pd.to_numeric(o["qty"], errors="coerce")
    o = o.dropna(subset=["date", "qty"])
    o = o[o.qty > 0]
    if "status" in o.columns:
        st = o["status"].astype(str).str.strip().str.lower()
        o = o[~st.isin(NOT_DEMAND)]
    for c in ("product", "variant", "pack"):
        o[c] = o[c].fillna("-").astype(str).str.strip()
    return o[["date", "product", "variant", "pack", "qty"]]


def clean_products(products: pd.DataFrame) -> pd.DataFrame:
    p = normalize_columns(products.copy())
    for c in ("variant", "pack"):
        if c not in p.columns:
            p[c] = "-"
    need = [c for c in ("product", "price", "unit_cost") if c not in p.columns]
    if need:
        raise ValueError(f"Products sheet needs columns: {', '.join(need)}")
    for c in ("product", "variant", "pack"):
        p[c] = p[c].fillna("-").astype(str).str.strip()
    defaults = dict(on_hand=0, batch_min_units=0, batch_min_amount=0, batch_multiple=1, shelf_life_months=0,
                    lead_time_days=14, make_every_days=0, expected_monthly_units=0)
    for c, d in defaults.items():
        p[c] = pd.to_numeric(p[c], errors="coerce").fillna(d) if c in p.columns else d
    p["price"] = pd.to_numeric(p.price, errors="coerce")
    p["unit_cost"] = pd.to_numeric(p.unit_cost, errors="coerce")
    if p[["price", "unit_cost"]].isna().any().any():
        raise ValueError("Every product row needs a price and a unit_cost.")
    amt = p["pack"].map(pack_amount)
    p["amount"] = [a for a, _ in amt]
    p["amount_unit"] = [u for _, u in amt]
    p["batch_multiple"] = p.batch_multiple.clip(lower=1).round().astype(int)
    return p.drop_duplicates(["product", "variant", "pack"]).reset_index(drop=True)


# ---------------------------------------------------------------- forecast
def monthly_table(orders: pd.DataFrame, sku: pd.DataFrame) -> pd.DataFrame:
    """Months x SKU units (SKU order = sku rows), months with no orders filled with 0."""
    o = orders.copy()
    o["month"] = o.date.dt.to_period("M")
    key = list(zip(sku["product"], sku["variant"], sku["pack"]))
    t = o.groupby(["month", "product", "variant", "pack"]).qty.sum().unstack(["product", "variant", "pack"])
    months = pd.period_range(o.month.min(), o.month.max(), freq="M")
    t = t.reindex(index=months).reindex(columns=pd.MultiIndex.from_tuples(key)).fillna(0)
    t.columns = range(len(key))
    return t


def forecast_monthly(orders: pd.DataFrame, sku: pd.DataFrame, months_ahead: int = 4,
                     first_month: pd.Period | None = None) -> dict:
    mt = monthly_table(orders, sku)
    # the last month is partial if orders stop before its end: scale it up by the share of days covered
    last_day = orders.date.max()
    last_m = mt.index[-1]
    covered = (last_day.day) / last_m.days_in_month
    tot = mt.sum(axis=1).astype(float)
    if covered < 0.9:
        if covered < 0.35 and len(mt) > 1:
            mt, tot = mt.iloc[:-1], tot.iloc[:-1]
        else:
            tot.iloc[-1] = tot.iloc[-1] / covered
    n = len(tot)
    rs = np.array([ramadan_share(m.to_timestamp()) for m in tot.index])
    y = np.log(np.maximum(tot.to_numpy(), 1.0))
    t = np.arange(n, dtype=float)
    w = 0.5 ** ((n - 1 - t) / 6.0)          # recent months count more (half-life 6 months)
    use_ram = n >= 9 and (rs > 0.25).sum() >= 1 and (rs < 0.05).sum() >= 4
    use_trend = n >= 6
    X = [np.ones(n)]
    if use_trend:
        X.append(t - t[-1])
    if use_ram:
        X.append(rs)
    X = np.column_stack(X)
    # weighted ridge so short histories stay sensible
    lam = np.diag([0.0] + ([2.0] if use_trend else []) + ([0.3] if use_ram else []))
    W = np.diag(w)
    beta = np.linalg.solve(X.T @ W @ X + lam, X.T @ W @ y)
    level = beta[0]
    trend = float(np.clip(beta[1], -0.06, 0.06)) * 0.7 if use_trend else 0.0   # damped
    ram = float(np.clip(beta[-1], -0.5, 1.2)) if use_ram else 0.0
    resid = y - X @ beta
    cv = float(np.clip(np.sqrt(np.average(resid ** 2, weights=w) * n / max(n - X.shape[1], 1)), 0.15, 0.6)) if n >= 4 else 0.35

    start = first_month or (tot.index[-1] + 1)
    fut = pd.period_range(start, periods=months_ahead, freq="M")
    steps = np.array([(m - tot.index[-1]).n for m in fut], float)
    rs_f = np.array([ramadan_share(m.to_timestamp()) for m in fut])
    brand = np.exp(level + trend * steps + ram * rs_f)

    # SKU shares: recent (half-life 3 months) vs long-run, mixed like a Dirichlet prior of strength 30 units
    wm = 0.5 ** ((len(mt) - 1 - np.arange(len(mt))) / 3.0)
    recent = (mt.to_numpy() * wm[:, None]).sum(0)
    longrun = mt.to_numpy().sum(0)
    lr_share = (longrun + 0.5) / (longrun + 0.5).sum()
    share = recent + 30 * lr_share
    new = longrun == 0
    exp_units = sku.expected_monthly_units.to_numpy(float)
    share = share / share.sum()
    mean = brand[:, None] * share[None, :]
    # SKUs with no sales yet: owner's estimate if given, else half the average SKU of the same product
    for k in np.where(new)[0]:
        if exp_units[k] > 0:
            mean[:, k] = exp_units[k]
        else:
            same = (sku["product"] == sku["product"].iloc[k]).to_numpy() & ~new
            base = mean[:, same].mean(1) if same.any() else mean[:, ~new].mean(1)
            mean[:, k] = 0.5 * base
    return dict(months=list(fut), mean=mean, brand=brand, cv=cv, trend=trend, ramadan_effect=ram,
                used_ramadan=use_ram, history_months=n, new=new, history=mt, history_total=tot)


def scenarios(fc: dict, n: int, seed: int = 1) -> np.ndarray:
    """n x months x SKU demand draws: a shared brand surprise per scenario (persisting over the months)
    plus Poisson noise per SKU and month."""
    rng = np.random.default_rng(seed)
    sig = math.sqrt(math.log(1 + fc["cv"] ** 2))
    shock = rng.lognormal(-sig ** 2 / 2, sig, size=(n, 1, 1))
    mix = rng.lognormal(-0.02, 0.2, size=(n, 1, fc["mean"].shape[1]))   # SKU mix drift
    mix = mix / mix.mean(axis=2, keepdims=True)
    return rng.poisson(shock * mix * fc["mean"][None, :, :])


# ---------------------------------------------------------------- plan
def _cover_months(lead_days: float, review: float) -> float:
    return review + lead_days / 30.4


def plan_batch(orders: pd.DataFrame, products: pd.DataFrame, settings: BatchSettings | None = None) -> dict:
    s = settings or BatchSettings()
    notes = list(s.notes)
    o = clean_orders(orders)
    p = clean_products(products)
    # order lines for SKUs not in the product list still count toward the brand total, but can't be planned
    known = set(zip(p["product"], p["variant"], p["pack"]))
    unknown = o[[k not in known for k in zip(o["product"], o["variant"], o["pack"])]]
    if len(unknown):
        top = unknown.groupby(["product", "variant", "pack"]).qty.sum().sort_values(ascending=False).head(5)
        notes.append("Orders for items missing from the Products sheet were ignored: "
                     + "; ".join(f"{a} / {b} / {c} ({int(q)})" for (a, b, c), q in top.items()))
        o = o[[k in known for k in zip(o["product"], o["variant"], o["pack"])]]
    if o.empty:
        raise ValueError("No orders match the products list. Check product, variant and pack names.")
    start = pd.Timestamp(s.plan_start) if s.plan_start else (o.date.max() + pd.Timedelta(days=7)).normalize()
    fc = forecast_monthly(o, p, s.months_ahead, first_month=start.to_period("M"))
    if fc["history_months"] < 6:
        notes.append(f"Only {fc['history_months']} months of orders: the forecast is rough. 6-12 months is better.")
    scen = scenarios(fc, s.n_scenarios, s.seed)              # n x M x K
    M, K = fc["mean"].shape
    c = p.unit_cost.to_numpy(float)
    price = p.price.to_numpy(float)
    on_hand = p.on_hand.to_numpy(float)
    # day-of-month the batch starts: the first month counts only from that day
    frac0 = 1 - (start.day - 1) / start.days_in_month
    month_w = np.ones(M)
    month_w[0] = frac0

    def demand_over(months: float) -> np.ndarray:
        """n x K demand over the next `months` months (fractional months prorated)."""
        out = np.zeros((scen.shape[0], K))
        left = months
        for m in range(M):
            if left <= 0:
                break
            take = min(month_w[m], left)
            out += scen[:, m, :] * take          # scen holds whole-month demand
            left -= take
        if left > 0:  # beyond the forecast window: repeat the last month
            out += scen[:, -1, :] * left
        return out

    margin = np.maximum(price - c, 0)
    hold = s.holding_cost_month * c
    rows = []
    q = np.zeros(K)
    target = np.zeros(K)
    cover = np.zeros(K)
    for k in range(K):
        review = p.make_every_days.iloc[k] / 30.4 if p.make_every_days.iloc[k] > 0 else s.review_months
        cover[k] = _cover_months(p.lead_time_days.iloc[k], review)
        d = demand_over(cover[k])[:, k]
        cr = margin[k] / max(margin[k] + hold[k] * cover[k], 1e-9)
        sl = float(np.clip(cr, 0.5, s.max_service))
        target[k] = np.quantile(d, sl)
        q[k] = max(target[k] - on_hand[k], 0)
    # shelf life: on hand + made must sell (median) before expiry minus a margin
    cap = np.full(K, np.inf)
    for k in range(K):
        sl_m = p.shelf_life_months.iloc[k]
        if sl_m > 0:
            sell_window = max(sl_m - s.expiry_margin_months, 0.5)
            cap[k] = max(np.median(demand_over(sell_window)[:, k]) - on_hand[k], 0)
    q = np.minimum(q, cap)

    # batch rules per product x variant (one batch of the formula, filled into several pack sizes)
    groups = p.groupby(["product", "variant"], sort=False).indices
    why = {}
    for (prod, var), idx in groups.items():
        idx = np.asarray(idx)
        g = p.iloc[idx]
        mult = g.batch_multiple.to_numpy(int)
        q[idx] = np.ceil(q[idx] / mult - 1e-9) * mult
        min_units = g.batch_min_units.max()
        min_amt = g.batch_min_amount.max()
        amt = g.amount.fillna(0).to_numpy(float)
        made_units = q[idx].sum()
        made_amt = float((q[idx] * amt).sum())
        short = made_units > 0 and ((min_units > 0 and made_units < min_units) or
                                    (min_amt > 0 and amt.sum() > 0 and made_amt < min_amt))
        if short:
            # either skip this batch (if stock lasts until the next one) or scale up to the minimum
            need_now = (on_hand[idx] < np.quantile(demand_over(cover[idx].min())[:, idx], 0.5, axis=0)).any()
            room = cap[idx]
            if need_now:
                if min_amt > 0 and made_amt > 0:
                    f = min_amt / made_amt
                else:
                    f = min_units / made_units
                q2 = np.ceil(q[idx] * f / mult - 1e-9) * mult
                if np.all(q2 <= np.maximum(room, q[idx])):
                    q[idx] = q2
                    why[(prod, var)] = "raised to the minimum batch"
                else:
                    q[idx] = q2
                    why[(prod, var)] = "minimum batch is more than sells before expiry: check with the factory"
            else:
                q[idx] = 0
                why[(prod, var)] = "skipped: stock lasts until next batch and the need is below the minimum batch"

    # cash: drop the least valuable units first (expected margin per pound, by multiple)
    spend = float(q @ c)
    if spend > s.budget:
        notes.append(f"Plan needed EGP {spend:,.0f}; cut to the budget of EGP {s.budget:,.0f}.")
        d_cover = np.stack([demand_over(cover[k])[:, k] for k in range(K)], 1)
        while float(q @ c) > s.budget:
            best, best_val = None, np.inf
            for k in range(K):
                if q[k] <= 0:
                    continue
                m = p.batch_multiple.iloc[k]
                top = on_hand[k] + q[k]
                p_sell = np.mean(d_cover[:, k] >= top - m / 2)
                val = p_sell * margin[k] / max(c[k], 1e-9)
                if val < best_val:
                    best, best_val = k, val
            if best is None:
                break
            q[best] = max(q[best] - p.batch_multiple.iloc[best], 0)

    # results
    for k in range(K):
        d1 = demand_over(cover[k])[:, k]
        avail = on_hand[k] + q[k]
        rows.append(dict(
            product=p["product"].iloc[k], variant=p["variant"].iloc[k], pack=p["pack"].iloc[k],
            on_hand=int(on_hand[k]), make=int(q[k]),
            next_30_days=float(fc["mean"][0, k] * month_w[0] + (fc["mean"][1, k] * (1 - month_w[0]) if M > 1 else 0)),
            cover_months=round(cover[k], 1),
            make_every_days=int(round((p.make_every_days.iloc[k] or s.review_months * 30.4))),
            then_per_batch=int(round(float(fc["mean"][1 if M > 1 else 0, k]) * ((p.make_every_days.iloc[k] / 30.4) if p.make_every_days.iloc[k] > 0 else s.review_months))),
            chance_no_stockout=float(np.mean(d1 <= avail)),
            months_of_stock=float(avail / max(fc["mean"][:, k].mean(), 1e-9)),
            cost=float(q[k] * c[k]),
            note=why.get((p["product"].iloc[k], p["variant"].iloc[k]), "new item: estimate" if fc["new"][k] else ""),
        ))
    plan = pd.DataFrame(rows)

    amt = p.amount.fillna(0).to_numpy(float)
    batches = (plan.assign(amount=q * amt, unit=p.amount_unit)
               .groupby(["product", "variant"], sort=False)
               .agg(units=("make", "sum"), amount=("amount", "sum"), unit=("unit", "first"), cost=("cost", "sum"))
               .reset_index())
    batches = batches[batches.units > 0]

    fc_tab = pd.DataFrame(fc["mean"].T, columns=[str(m) for m in fc["months"]])
    fc_tab.insert(0, "pack", p["pack"])
    fc_tab.insert(0, "variant", p["variant"])
    fc_tab.insert(0, "product", p["product"])

    if fc["used_ramadan"]:
        notes.append(f"Ramadan/Eid months sell about {math.exp(fc['ramadan_effect']) - 1:+.0%} vs a normal month in your history.")
    else:
        notes.append("Not enough history around Ramadan to measure its effect; Ramadan is treated like a normal month.")
    notes.append(f"Monthly sales trend used: {fc['trend']:+.1%} per month (damped).")

    return dict(plan=plan, batches=batches, forecast=fc_tab, fc=fc, start=start.date(), notes=notes,
                total_cost=float(q @ c), q=q, products=p)


def materials_plan(result: dict, bom: pd.DataFrame, materials: pd.DataFrame, today: date | None = None) -> pd.DataFrame:
    """bom: product, variant ('*' = all), pack ('*' = all), material, qty_per_unit.
    materials: material, on_hand, lead_time_days, order_multiple, unit_cost (optional)."""
    p = result["products"]
    q = result["q"]
    b = bom.copy()
    for c in ("variant", "pack"):
        if c not in b.columns:
            b[c] = "*"
        b[c] = b[c].fillna("*").astype(str).str.strip()
    b["product"] = b["product"].astype(str).str.strip()
    need = {}
    for k in range(len(p)):
        if q[k] <= 0:
            continue
        m = ((b["product"] == p["product"].iloc[k]) | (b["product"] == "*")) & \
            ((b["variant"] == p["variant"].iloc[k]) | (b["variant"] == "*")) & \
            ((b["pack"] == p["pack"].iloc[k]) | (b["pack"] == "*"))
        for _, r in b[m].iterrows():
            need[r.material] = need.get(r.material, 0.0) + float(r.qty_per_unit) * q[k]
    mt = materials.copy()
    mt["material"] = mt["material"].astype(str).str.strip()
    mt = mt.set_index("material")
    start = pd.Timestamp(result["start"])
    rows = []
    for name, qty in need.items():
        r = mt.loc[name] if name in mt.index else pd.Series(dtype=float)
        oh = float(r.get("on_hand", 0) or 0)
        mult = float(r.get("order_multiple", 1) or 1)
        lead = float(r.get("lead_time_days", 14) or 14)
        buy = max(qty - oh, 0)
        buy = math.ceil(buy / mult - 1e-9) * mult if buy > 0 else 0
        by = (start - pd.Timedelta(days=lead)).date() if buy > 0 else None
        late = by is not None and by < (today or date.today())
        rows.append(dict(material=name, needed=round(qty, 1), on_hand=oh, to_order=buy, order_by=by,
                         status=("late: order today, batch may slip" if late else ("order" if buy > 0 else "enough")),
                         cost=buy * float(r.get("unit_cost", 0) or 0)))
    return pd.DataFrame(rows, columns=["material", "needed", "on_hand", "to_order", "order_by", "status", "cost"])
