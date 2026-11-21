"""Which courier to use for each delivery zone, and where COD is too risky.

For a COD order sent with courier k to zone z:
    expected profit = p_kz * margin - fee_kz - (1 - p_kz) * return_fee_kz
p_kz (delivery success) is learned from the brand's own orders with a Beta
prior centred on the zone average, so a courier tried only a few times in a
zone is not over-trusted. Optional minimum-share contracts are respected with
a small linear program.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from ortools.linear_solver import pywraplp


def success_rates(orders: pd.DataFrame, prior_strength: float = 25) -> pd.DataFrame:
    o = orders.copy()
    o["ok"] = o.status.isin(["delivered", "size_exchange"]).astype(int)
    zone_p = o.groupby("zone").ok.mean()
    g = o.groupby(["courier", "zone"]).ok.agg(["sum", "count"]).reset_index()
    g["prior"] = g.zone.map(zone_p)
    g["p_hat"] = (g["sum"] + prior_strength * g.prior) / (g["count"] + prior_strength)
    g["p_raw"] = g["sum"] / g["count"]
    # ~90% interval from the Beta posterior
    a = g["sum"] + prior_strength * g.prior
    b = g["count"] - g["sum"] + prior_strength * (1 - g.prior)
    from scipy.stats import beta
    g["p_low"] = beta.ppf(0.05, a, b)
    g["p_high"] = beta.ppf(0.95, a, b)
    return g.rename(columns={"count": "orders"})


def choose_couriers(orders: pd.DataFrame, couriers: pd.DataFrame, avg_margin: float,
                    min_share: dict | None = None, risky_threshold: float = 0.0) -> dict:
    sr = success_rates(orders)
    df = couriers.merge(sr[["courier", "zone", "p_hat", "p_low", "p_high", "orders"]], on=["courier", "zone"], how="left")
    zone_p = orders.assign(ok=orders.status.isin(["delivered", "size_exchange"])).groupby("zone").ok.mean()
    df["p_hat"] = df.p_hat.fillna(df.zone.map(zone_p)).fillna(0.7)
    df["orders"] = df.orders.fillna(0).astype(int)
    df["exp_profit"] = df.p_hat * avg_margin - df.fee - (1 - df.p_hat) * df.return_fee
    df["cost_per_delivered"] = (df.fee + (1 - df.p_hat) * df.return_fee) / df.p_hat
    vol = orders.groupby("zone").size()
    zones = list(vol.index)
    solver = pywraplp.Solver.CreateSolver("GLOP")
    x = {}
    for _, r in df.iterrows():
        if r.zone in vol.index:
            x[(r.courier, r.zone)] = solver.NumVar(0, 1, "")
    for z in zones:
        ct = solver.Constraint(1, 1)
        for (k, zz), var in x.items():
            if zz == z:
                ct.SetCoefficient(var, 1)
    total = vol.sum()
    if min_share:
        for k, sh in min_share.items():
            ct = solver.Constraint(sh * total, solver.infinity())
            for (kk, z), var in x.items():
                if kk == k:
                    ct.SetCoefficient(var, float(vol[z]))
    obj = solver.Objective()
    for (k, z), var in x.items():
        r = df[(df.courier == k) & (df.zone == z)].iloc[0]
        obj.SetCoefficient(var, float(r.exp_profit * vol[z]))
    obj.SetMaximization()
    solver.Solve()
    plan = []
    for (k, z), var in x.items():
        if var.solution_value() > 1e-6:
            r = df[(df.courier == k) & (df.zone == z)].iloc[0]
            plan.append(dict(zone=z, courier=k, share=var.solution_value(), p_hat=r.p_hat, fee=r.fee,
                             exp_profit_per_order=r.exp_profit, orders_seen=r.orders))
    plan = pd.DataFrame(plan).sort_values(["zone", "share"], ascending=[True, False])
    # current mix value
    cur = orders.groupby(["zone", "courier"]).size().rename("n").reset_index().merge(
        df[["courier", "zone", "exp_profit"]], on=["courier", "zone"], how="left")
    cur_val = (cur.n * cur.exp_profit).sum() / cur.n.sum()
    new_val = sum(var.solution_value() * vol[z] * df[(df.courier == k) & (df.zone == z)].exp_profit.iloc[0]
                  for (k, z), var in x.items()) / total
    best = plan.groupby("zone").exp_profit_per_order.max()
    risky = best[best < risky_threshold].index.tolist()
    low_p = plan.groupby("zone").p_hat.max()
    advice = []
    for z in zones:
        if z in risky:
            advice.append(dict(zone=z, advice="Loses money on COD: ask for prepayment or a deposit"))
        elif low_p[z] < 0.65:
            advice.append(dict(zone=z, advice="High refusal: confirm by call/WhatsApp before shipping"))
    return dict(table=df, plan=plan, current_per_order=cur_val, optimized_per_order=new_val,
                orders_per_season=int(total), advice=pd.DataFrame(advice, columns=["zone", "advice"]))
