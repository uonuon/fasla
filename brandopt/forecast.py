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
    df["demand_est"] = np.where(df.sold_out, df.sold / np.maximum(frac, 0.15), df.sold)
    df["sellout_week"] = np.where(df.sold_out, df.last_week + 1, np.nan)
    return df


def build_forecast(orders: pd.DataFrame, production: pd.DataFrame, products: pd.DataFrame,
                   growth: float | None = None, cv_existing: float | None = None, cv_new: float = 0.45,
                   size_prior_strength: float = 40, color_prior_strength: float = 10,
                   new_style_units: dict | None = None, owner_estimates: dict | None = None,
                   owner_cv: float = 0.30) -> dict:
    """Mean demand per SKU for next season + parameters to sample scenarios."""
    cs = corrected_sales(orders, production)
    seasons = sorted(cs.season.unique())
    last = seasons[-1]
    st_tot = cs.groupby(["season", "style_id"]).demand_est.sum().unstack(0)

    # brand growth from styles present in both of the last two seasons (pooled ratio, shrunk to 1)
    if growth is None:
        if len(seasons) >= 2:
            both = st_tot[[seasons[-2], last]].dropna()
            ratio = both[last].sum() / max(both[seasons[-2]].sum(), 1)
            growth = float(np.clip(0.8 * ratio + 0.2 * 1.0, 0.7, 1.6))
        else:
            growth = 1.0
    # forecast error cv from season-to-season log changes across styles
    if cv_existing is None:
        if len(seasons) >= 2:
            both = st_tot[[seasons[-2], last]].dropna()
            lr = np.log(both[last] / both[seasons[-2]]) - np.log(growth)
            cv_existing = float(np.clip(np.sqrt(np.mean(lr ** 2) + 0.15 ** 2), 0.18, 0.6))
        else:
            cv_existing = 0.35

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
    return dict(sku=fc, styles=style_info, growth=growth, cv_existing=cv_existing, corrected=cs)


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
