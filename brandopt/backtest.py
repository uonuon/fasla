"""Score the optimizer against how owners usually decide, on demand it never saw."""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from .couriers import choose_couriers
from .forecast import build_forecast, sample_scenarios
from .production import evaluate_plan, explain_styles, optimize_production
from .sim import COURIERS, RETURN_FEE_SHARE, ZONE_SHARE, ZONES, BrandWorld, default_styles


def scale_to_budget(q, cost, budget, sku, moq):
    q = q.astype(float)
    tot = (q * cost).sum()
    if tot > budget:
        q = np.floor(q * budget / tot)
    return q.astype(int)


def owner_plans(world: BrandWorld, hist: dict, sku: pd.DataFrame, year: int, rng) -> dict:
    """Two realistic owner habits for next season."""
    cost = sku.unit_cost.to_numpy(float)
    gut = world.owner_production(year, rng)
    gut = sku[["style_id", "color", "size"]].merge(gut, how="left").produced.fillna(0).to_numpy()
    # habit B: repeat last season's recorded sales per SKU + 15% (new styles: gut feel)
    o = hist["orders"]
    last = o[o.status.isin(["delivered", "size_exchange"]) & (o.date >= "2025-07-01")]
    s = last.groupby(["style_id", "color", "size"]).qty.sum().rename("s").reset_index()
    rep = sku[["style_id", "color", "size"]].merge(s, how="left").s.to_numpy()
    rep = np.where(np.isnan(rep), gut, np.ceil(np.nan_to_num(rep) * 1.15))
    budget = float((gut * cost).sum())
    return dict(budget=budget,
                plans={"Owner A: gut feel, usual size split": gut.astype(int),
                       "Owner B: repeat last season's sales +15%": scale_to_budget(rep, cost, budget, sku, 100)})


def run_production_backtest(seed_world=7, seed_eval=99, n_scen=200, n_eval=2000, time_limit_s=60,
                            demand_shift: float = 1.0, world: BrandWorld | None = None,
                            use_owner_estimates: bool = True, with_oracle: bool = False) -> dict:
    world = world or BrandWorld(default_styles(), seed=seed_world)
    hist = world.simulate_history()
    fc0 = build_forecast(hist["orders"], hist["production"], hist["products"])
    sku0 = fc0["sku"].merge(hist["products"][["style_id", "color", "size", "unit_cost", "price"]],
                            on=["style_id", "color", "size"])
    rng = np.random.default_rng(seed_world + 1)
    own = owner_plans(world, hist, sku0, 2, rng)
    gut_tot = pd.Series(own["plans"]["Owner A: gut feel, usual size split"], index=sku0.style_id).groupby(level=0).sum()
    fc = build_forecast(hist["orders"], hist["production"], hist["products"],
                        owner_estimates=gut_tot.to_dict() if use_owner_estimates else None)
    sku = fc["sku"].merge(hist["products"][["style_id", "color", "size", "name", "unit_cost", "price"]],
                          on=["style_id", "color", "size"])
    scen = sample_scenarios(fc, n_scen, seed=3)
    t = time.time()
    opt = optimize_production(sku, scen, own["budget"], time_limit_s=time_limit_s)
    solve_s = time.time() - t
    # TRUE next-season demand, in the same SKU order
    truth_order = world.expected_sku_demand(2)[["style_id", "color", "size"]]
    true_d = world.sample_season_demand(2, n_eval, np.random.default_rng(seed_eval))
    true_df = pd.DataFrame(true_d.T)
    true_df[["style_id", "color", "size"]] = truth_order.to_numpy()
    aligned = sku[["style_id", "color", "size"]].merge(true_df, how="left").drop(columns=["style_id", "color", "size"])
    D = np.nan_to_num(aligned.to_numpy(float).T)
    if demand_shift != 1.0:   # the season turns out stronger/weaker than anything in the data
        D = np.random.default_rng(seed_eval + 1).poisson(D * demand_shift).astype(float)
    plans = {**own["plans"], "Optimized plan": opt["Q"]}
    if with_oracle:
        orc = optimize_production(sku, D[:n_scen], own["budget"], time_limit_s=time_limit_s)
        plans["Best possible (knows true demand)"] = orc["Q"]
    D_eval = D[n_scen:] if with_oracle else D
    results = {name: evaluate_plan(q, sku, D_eval) for name, q in plans.items()}
    return dict(sku=sku, forecast=fc, plans=plans, results=pd.DataFrame(results).T,
                budget=own["budget"], solve_s=solve_s, explain=explain_styles(opt["Q"], sku, scen),
                hist=hist, world=world, true_demand=D, scenarios=scen)


def run_courier_backtest(hist: dict, avg_margin: float, n_orders: int = 6000, seed: int = 5) -> dict:
    """Simulate next season's orders with TRUE delivery rates: owner's habit vs optimized choice."""
    rng = np.random.default_rng(seed)
    choice = choose_couriers(hist["orders"], hist["couriers"], avg_margin)
    plan = choice["plan"]
    cn = list(COURIERS)
    zones = rng.choice(len(ZONES), p=ZONE_SHARE, size=n_orders)

    def profit(pick):
        tot = 0.0
        delivered = 0
        for zi in zones:
            k = pick(zi)
            d = COURIERS[k]
            p, fee = d["p"][zi], d["fee"][zi]
            ok = rng.random() < p
            delivered += ok
            tot += (avg_margin if ok else 0) - fee - (0 if ok else fee * RETURN_FEE_SHARE)
        return tot, delivered

    def owner_pick(zi):
        if zi <= 1 and rng.random() < 0.15:
            return "CityRunner"
        return "FastBox" if rng.random() < 0.72 else "NileExpress"

    best = plan.sort_values("share", ascending=False).drop_duplicates("zone").set_index("zone").courier.to_dict()

    def opt_pick(zi):
        return best[ZONES[zi]]

    o_p, o_d = profit(owner_pick)
    n_p, n_d = profit(opt_pick)
    return dict(choice=choice, owner_profit=o_p, opt_profit=n_p, owner_delivered=o_d, opt_delivered=n_d,
                n_orders=n_orders, assignment=best)
