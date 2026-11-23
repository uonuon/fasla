"""Demand forecast for the next season from a brand's own order history.

Key ideas (all standard inventory analytics):
  1. Censoring: when a size sold out, recorded sales understate real demand.
     We estimate how much of the season's demand normally arrives by each week
     (from sizes that never sold out) and scale sold-out sizes up accordingly.
  2. Shrinkage: size and colour shares mix the style's own history with the
     category's average curve, so a style with few sales is not over-trusted.
  3. Uncertainty: a style-level lognormal "surprise" plus Dirichlet noise on
     the size/colour mix gives demand scenarios for the optimizer.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SIZES = ["S", "M", "L", "XL", "XXL"]
SOLD_STATUSES = ("delivered", "size_exchange")
SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "XXXL", "3XL", "4XL", "5XL", "ONE SIZE", "FREE SIZE"]


def size_sort_key(s):
    t = str(s).strip().upper()
    if t in SIZE_ORDER:
        return (0, SIZE_ORDER.index(t), "")
    try:
        return (1, float(t), "")
    except ValueError:
        return (2, 0, t)


def sizes_in(products: pd.DataFrame) -> list:
    return sorted(dict.fromkeys(products["size"].astype(str)), key=size_sort_key)


def assign_seasons(orders: pd.DataFrame, production: pd.DataFrame):
    """Give every order and production row a season index 0..k-1 (oldest first).
    Uses Production.season_start dates when given, else the 4-digit year in the season name
    (orders from July onward count toward that year's season)."""
    o = orders.copy()
    prod = production.copy()
    prod["season"] = prod["season"].astype(str)
    if "season_start" in prod.columns and prod["season_start"].notna().all():
        st = prod.groupby("season")["season_start"].min().map(pd.to_datetime).sort_values()
        labels = list(st.index)
        idx = np.searchsorted(st.values.astype("datetime64[ns]"), o.date.values.astype("datetime64[ns]"), side="right") - 1
        o["season"] = np.where(idx >= 0, idx, -1)
        starts = {i: st.iloc[i] for i in range(len(labels))}
    else:
        yr = prod["season"].str.extract(r"(\d{4})")[0]
        if yr.isna().any():
            raise ValueError("Each Production season needs a year in its name (e.g. 'Winter 2025/26') or a season_start date.")
        prod["_y"] = yr.astype(int)
        ys = sorted(prod["_y"].unique())
        labels = [prod.loc[prod._y == y, "season"].iloc[0] for y in ys]
        oy = o.date.dt.year.where(o.date.dt.month >= 7, o.date.dt.year - 1)
        o["season"] = oy.map({y: i for i, y in enumerate(ys)}).fillna(-1).astype(int)
        starts = {}
    prod["season"] = prod["season"].map({l: i for i, l in enumerate(labels)})
    o = o[o.season >= 0]
    for i in range(len(labels)):
        if i not in starts:
            d = o.loc[o.season == i, "date"]
            starts[i] = d.min() if len(d) else pd.Timestamp("1970-01-01")
    o["week"] = ((o.date - o.season.map(starts)).dt.days // 7).clip(lower=0)
    return o, prod.drop(columns=[c for c in ("_y",) if c in prod.columns]), labels


def season_coverage(orders: pd.DataFrame, production: pd.DataFrame) -> dict:
    """Share of each season's normal demand that falls inside the dates we have orders for.

    Order exports rarely line up with seasons: the history starts mid-season and the current season is
    still running. Read as-is, a five-month-old season looks like a 50% drop and the next plan comes out
    far too small. Coverage = (expected demand in the observed part of the season) / (expected demand in
    the whole season), from a month-of-year profile of the brand's own sales. With one year of history the
    profile also carries that year's growth, which makes coverage a little high (forecast a little low):
    the safe direction for production."""
    if orders.empty:
        return {}
    d0, d1 = orders.date.min().normalize(), orders.date.max().normalize()
    sold = orders[orders.status.isin(SOLD_STATUSES)]
    o, prod, labels = assign_seasons(sold, production)
    if o.empty:
        return {}
    # season bounds
    bounds = {}
    for i in range(len(labels)):
        di = o.loc[o.season == i, "date"]
        if not len(di):
            continue
        if "season_start" in production.columns and production["season_start"].notna().all():
            st = production.groupby(production["season"].astype(str))["season_start"].min().map(pd.to_datetime)
            st = st.sort_values()
            start = st.iloc[i]
            end = st.iloc[i + 1] if i + 1 < len(st) else start + pd.DateOffset(years=1)
        else:
            f = di.min()
            y = f.year if f.month >= 7 else f.year - 1
            start, end = pd.Timestamp(y, 7, 1), pd.Timestamp(y + 1, 7, 1)
        bounds[i] = (start, end)
    days = pd.date_range(min(b[0] for b in bounds.values()), max(b[1] for b in bounds.values()) - pd.Timedelta(days=1))
    seen = (days >= d0) & (days <= d1)
    # month-of-year profile: average daily sales per calendar month over the days we observed
    daily = sold.groupby(sold.date.dt.normalize()).qty.sum().reindex(days[seen], fill_value=0)
    by_m = daily.groupby(daily.index.month).mean()
    rate = by_m.reindex(range(1, 13))
    rate = rate.fillna(rate.mean() if rate.notna().any() else 1.0).clip(lower=1e-6)
    out = {}
    for i, (start, end) in bounds.items():
        m = (days >= start) & (days < end)
        r = rate.reindex(days[m].month).to_numpy()
        c = float(np.clip(r[seen[m]].sum() / r.sum(), 0.0, 1.0))
        # the last days of an export sit in a partly-seen month whose rate is an average, so a finished season
        # still reads ~0.97; small gaps are noise, not missing demand
        out[i] = 1.0 if c >= 0.93 else c
    return out


def weekly_profile(orders: pd.DataFrame, production: pd.DataFrame, weeks: int = 16) -> np.ndarray:
    """Share of a season's demand that arrives in each week, from SKUs that never sold out."""
    o, prod, _ = assign_seasons(orders[orders.status.isin(SOLD_STATUSES)], production)
    o["week"] = o.week.clip(0, weeks - 1)
    sold = o.groupby(["season", "style_id", "color", "size"]).qty.sum()
    prod = prod.set_index(["season", "style_id", "color", "size"]).produced
    # produced == 0 means "unknown" (orders-only upload): treat as never sold out
    keys = [k for k in sold.index if k in prod.index and (prod[k] <= 0 or sold[k] < 0.9 * prod[k])]
    if not keys:
        return np.full(weeks, 1 / weeks)
    m = o.set_index(["season", "style_id", "color", "size"]).index.isin(keys)
    w = o[m].groupby("week").qty.sum().reindex(range(weeks), fill_value=0).to_numpy(float)
    w = w + 0.5
    return w / w.sum()


def corrected_sales(orders: pd.DataFrame, production: pd.DataFrame, weeks: int = 16) -> pd.DataFrame:
    """Per season x SKU: recorded sales, whether it sold out, and estimated true demand."""
    prof = weekly_profile(orders, production, weeks)
    cum = np.cumsum(prof)
    o, prod, labels = assign_seasons(orders[orders.status.isin(SOLD_STATUSES)], production)
    o["week"] = o.week.clip(0, weeks - 1)
    g = o.groupby(["season", "style_id", "color", "size"]).agg(sold=("qty", "sum"), last_week=("week", "max"))
    df = prod.merge(g.reset_index(), on=["season", "style_id", "color", "size"], how="left")
    df["sold"] = df.sold.fillna(0)
    df["last_week"] = df.last_week.fillna(0).astype(int)
    df["sold_out"] = (df.produced > 0) & (df.sold >= 0.97 * df.produced)
    frac = cum[df.last_week.clip(0, weeks - 1)]
    # Production unknown (orders-only upload): infer sell-outs from timing. If the size had stayed in stock,
    # sales after its last sale week would have continued at the season's normal pace; when that expected
    # remainder is large (>= 3 pieces, i.e. <5% chance of seeing none by luck), the size most likely sold out.
    unknown = df.produced <= 0
    if unknown.any():
        expected_after = df.sold * (1 - frac) / np.maximum(frac, 0.05)
        inferred = unknown & (df.sold >= 4) & (expected_after >= 3) & (df.last_week < weeks - 2)
        df["sold_out"] = df.sold_out | inferred
        df["sold_out_inferred"] = inferred
    df["demand_est"] = np.where(df.sold_out, df.sold / np.maximum(frac, 0.15), df.sold)
    df["sellout_week"] = np.where(df.sold_out, df.last_week + 1, np.nan)
    return df


def build_forecast(orders: pd.DataFrame, production: pd.DataFrame, products: pd.DataFrame,
                   growth: float | None = None, cv_existing: float | None = None, cv_new: float = 0.45,
                   size_prior_strength: float = 40, color_prior_strength: float = 10,
                   new_style_units: dict | None = None, owner_estimates: dict | None = None,
                   owner_cv: float = 0.30) -> dict:
    """Mean demand per SKU for next season + parameters to sample scenarios."""
    cs_raw = corrected_sales(orders, production)
    # put partly-observed seasons on a full-season footing (see season_coverage); a season we saw less
    # than 15% of says too little to keep
    cov = season_coverage(orders, production)
    cs = cs_raw.copy()
    keep = [s for s in sorted(cs.season.unique()) if cov.get(s, 1.0) >= 0.15] or [max(cs.season.unique())]
    cs = cs[cs.season.isin(keep)]
    scale = cs.season.map(lambda s: 1.0 / max(cov.get(s, 1.0), 0.15))
    cs["sold"] = cs.sold * scale
    cs["demand_est"] = cs.demand_est * scale
    seasons = sorted(cs.season.unique())
    last = seasons[-1]
    st_tot = cs.groupby(["season", "style_id"]).demand_est.sum().unstack(0)

    # brand growth, shrunk 20% toward "no growth". Measured on sizes that stayed in stock in both of the last
    # two seasons: their sales are real demand, while corrected sell-outs carry estimation noise that
    # inflated growth (simulated brands: 1.19 estimated vs 1.15 true; with this rule 1.15).
    growth_measured = growth is None
    if growth is None and len(seasons) >= 2:
        a = cs[cs.season == seasons[-2]].set_index(["style_id", "color", "size"])
        b = cs[cs.season == last].set_index(["style_id", "color", "size"])
        k = a.index.intersection(b.index)
        stable = k[(~a.loc[k, "sold_out"].to_numpy(bool)) & (~b.loc[k, "sold_out"].to_numpy(bool))]
        if a.loc[stable, "sold"].sum() >= 50:
            growth = float(np.clip(0.8 * b.loc[stable, "sold"].sum() / a.loc[stable, "sold"].sum() + 0.2, 0.7, 1.6))
    # fallback: styles present in both of the last two seasons (pooled ratio of corrected demand, shrunk to 1)
    if growth is None:
        if len(seasons) >= 2:
            both = st_tot[[seasons[-2], last]].dropna()
            ratio = both[last].sum() / max(both[seasons[-2]].sum(), 1)
            growth = float(np.clip(0.8 * ratio + 0.2 * 1.0, 0.7, 1.6))
        else:
            growth = 1.0
    # growth read off a young season is mostly extrapolation: pull it toward "no growth" in proportion
    if growth_measured and cov.get(last, 1.0) < 0.9:
        growth = float(1 + (growth - 1) * cov.get(last, 1.0) / 0.9)
    # forecast error cv from season-to-season log changes across styles
    if cv_existing is None:
        if len(seasons) >= 2:
            both = st_tot[[seasons[-2], last]].dropna()
            lr = np.log(both[last] / both[seasons[-2]]) - np.log(growth)
            cv_existing = float(np.clip(np.sqrt(np.mean(lr ** 2) + 0.15 ** 2), 0.18, 0.6))
        else:
            cv_existing = 0.35

    # and widen the uncertainty, so the optimizer hedges instead of betting on a young season's pace
    if cov.get(last, 1.0) < 0.9:
        cv_existing = float(min(0.6, np.sqrt(cv_existing ** 2 + (0.35 * (1 - cov.get(last, 1.0))) ** 2)))

    cat = products.drop_duplicates("style_id").set_index("style_id").category
    all_sizes = sizes_in(products)
    # category size curve from corrected demand (all seasons, with light smoothing)
    sz = cs.groupby(["style_id", "size"]).demand_est.sum().unstack().reindex(columns=all_sizes).fillna(0)

    def size_prior(c, sizes):
        ids = [s for s in sz.index if cat.get(s) == c]
        v = sz.loc[ids, sizes].sum().to_numpy(float) if ids else np.zeros(len(sizes))
        if v.sum() <= 0:
            v = sz[sizes].sum().to_numpy(float) if len(sz) else np.zeros(len(sizes))
        v = v + 0.5
        return v / v.sum()

    rows = []
    style_info = {}
    for sid, grp in products.groupby("style_id", sort=False):
        c = grp.category.iloc[0]
        colors = list(dict.fromkeys(grp.color))
        sizes = sorted(dict.fromkeys(grp["size"].astype(str)), key=size_sort_key)
        prior = size_prior(c, sizes)
        hist = cs[(cs.style_id == sid)]
        if len(hist):
            w = np.array([0.35, 0.65]) if len(seasons) >= 2 and seasons[-2] in hist.season.values else np.array([0, 1.0])
            if w[0] > 0 and cov.get(last, 1.0) < 0.6:
                # a young current season is a noisy estimate even after scaling: lean on the previous one more
                w = np.array([0.35, 0.65 * cov.get(last, 1.0) / 0.6])
                w = w / w.sum()
            tot_last = hist[hist.season == last].demand_est.sum()
            tot_prev = hist[hist.season == seasons[-2]].demand_est.sum() if len(seasons) >= 2 else 0
            if tot_prev > 0:
                mean_tot = w[0] * tot_prev * growth ** 2 + w[1] * tot_last * growth
            else:
                mean_tot = tot_last * growth
            cv = cv_existing
            sizes_obs = hist.groupby("size").demand_est.sum().reindex(sizes).fillna(0).to_numpy()
            size_post = sizes_obs + size_prior_strength * prior
            col_obs = hist.groupby("color").demand_est.sum().reindex(colors).fillna(0).to_numpy()
            col_post = col_obs + color_prior_strength * np.full(len(colors), 1 / len(colors))
            source = "own history"
        else:
            # new style: analog = average corrected season total of the same category, times growth
            same = [s for s in st_tot.index if cat.get(s) == c and not np.isnan(st_tot.loc[s, last])]
            base = st_tot.loc[same, last].mean() if same else st_tot[last].mean()
            mean_tot = float(base * growth)
            if new_style_units and sid in new_style_units:
                mean_tot = float(new_style_units[sid])
            cv = cv_new
            size_post = size_prior_strength * prior
            col_post = color_prior_strength * np.full(len(colors), 1 / len(colors))
            source = "similar styles (new)"
        if owner_estimates and sid in owner_estimates and owner_estimates[sid] > 0:
            # combine data forecast and owner's own estimate (inverse-variance weights on the log scale)
            v_d, v_o = np.log(1 + cv ** 2), np.log(1 + owner_cv ** 2)
            w_d = (1 / v_d) / (1 / v_d + 1 / v_o)
            mean_tot = float(np.exp(w_d * np.log(max(mean_tot, 1)) + (1 - w_d) * np.log(owner_estimates[sid])))
            cv = float(np.sqrt(np.exp(1 / (1 / v_d + 1 / v_o)) - 1))
            source += " + owner estimate"
        size_sh = size_post / size_post.sum()
        col_sh = col_post / col_post.sum()
        present = set(zip(grp.color, grp["size"].astype(str)))
        cells = [ci * len(sizes) + si for ci, col in enumerate(colors) for si, z in enumerate(sizes) if (col, z) in present]
        style_info[sid] = dict(mean=mean_tot, cv=cv, size_conc=size_post.sum(), color_conc=col_post.sum(),
                               size_sh=size_sh, col_sh=col_sh, colors=colors, sizes=sizes, cells=cells, source=source)
        tot_share = sum(col_sh[i // len(sizes)] * size_sh[i % len(sizes)] for i in cells)
        for i in cells:
            ci, si = divmod(i, len(sizes))
            rows.append(dict(style_id=sid, color=colors[ci], size=sizes[si],
                             mean=mean_tot * col_sh[ci] * size_sh[si] / max(tot_share, 1e-9)))
    fc = pd.DataFrame(rows)
    return dict(sku=fc, styles=style_info, growth=growth, cv_existing=cv_existing, corrected=cs_raw, coverage=cov)


def sample_scenarios(fc: dict, n: int = 300, seed: int = 1) -> np.ndarray:
    """n x SKU matrix of demand scenarios, columns in the order of fc['sku']."""
    rng = np.random.default_rng(seed)
    sku = fc["sku"]
    out = np.zeros((n, len(sku)))
    for sid, info in fc["styles"].items():
        m = (sku.style_id == sid).to_numpy()
        sig = np.sqrt(np.log(1 + info["cv"] ** 2))
        shock = rng.lognormal(-sig ** 2 / 2, sig, size=n)
        size_mix = rng.dirichlet(info["size_sh"] * min(info["size_conc"], 400), size=n)
        col_mix = rng.dirichlet(info["col_sh"] * min(info["color_conc"], 200), size=n)
        mix = (col_mix[:, :, None] * size_mix[:, None, :]).reshape(n, -1)[:, info["cells"]]
        mix = mix / mix.sum(1, keepdims=True)
        out[:, m] = rng.poisson(shock[:, None] * info["mean"] * mix)
    return out


def window_shares(orders: pd.DataFrame, products: pd.DataFrame, start: pd.Timestamp, months: float,
                  prior_units: float = 60) -> dict:
    """Share of a year's demand that falls in [start, start + months), per category.

    From the store's own sales: average daily sales in each calendar month over the observed days, per
    category, pulled toward the whole store's pattern when a category has few sales (a winter jacket and a
    linen trouser peak in different months). Returns {category: share}, plus "*" for the whole store."""
    sold = orders[orders.status.isin(SOLD_STATUSES)].copy()
    if sold.empty or months <= 0:
        return {"*": min(1.0, months / 12)}
    cat = products.drop_duplicates("style_id").set_index("style_id").category
    sold["cat"] = sold.style_id.map(cat).fillna("Other")
    d0, d1 = sold.date.min().normalize(), sold.date.max().normalize()
    seen = pd.date_range(d0, d1)
    year = pd.date_range("2001-01-01", "2001-12-31")          # any non-leap year: day weights per month
    end = start + pd.Timedelta(days=int(round(months * 30.44)))
    win = pd.date_range(start.normalize(), end - pd.Timedelta(days=1))

    def shares(g: pd.DataFrame) -> np.ndarray:
        daily = g.groupby(g.date.dt.normalize()).qty.sum().reindex(seen, fill_value=0)
        r = daily.groupby(daily.index.month).mean().reindex(range(1, 13))
        return r.fillna(r.mean() if r.notna().any() else 1.0).clip(lower=0).to_numpy()

    def share(rate: np.ndarray) -> float:
        tot = rate[year.month - 1].sum()
        if tot <= 0:
            return min(1.0, months / 12)
        # windows longer than a year repeat the pattern
        return float(rate[win.month - 1].sum() / tot)

    brand = shares(sold)
    out = {"*": share(brand)}
    b = brand / max(brand.mean(), 1e-9)
    for c, g in sold.groupby("cat"):
        own = shares(g)
        n = float(g.qty.sum())
        own_n = own / max(own.mean(), 1e-9)
        out[c] = share((n * own_n + prior_units * b) / (n + prior_units))
    return out


def apply_window(fc: dict, orders: pd.DataFrame, products: pd.DataFrame, start: pd.Timestamp, months: float) -> dict:
    """Turn a next-season (yearly) forecast into demand for the next `months` only: the rolling 'what to
    make now' question of stores that restock all year. Scales each style's mean by its category's share of
    the year falling in the window; the size and colour mix and the uncertainty shape stay as they were."""
    sh = window_shares(orders, products, start, months)
    cat = products.drop_duplicates("style_id").set_index("style_id").category
    f = {sid: sh.get(cat.get(sid), sh["*"]) for sid in fc["styles"]}
    styles = {sid: {**info, "mean": info["mean"] * f[sid]} for sid, info in fc["styles"].items()}
    sku = fc["sku"].copy()
    sku["mean"] = sku["mean"] * sku.style_id.map(f).fillna(sh["*"])
    return {**fc, "sku": sku, "styles": styles, "window": dict(start=str(start.date()), months=months, shares=sh)}
