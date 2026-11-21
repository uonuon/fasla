"""Everything the web page needs, as one JSON-ready dict."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .excel import run_plan
from .forecast import assign_seasons, build_forecast, sample_scenarios, sizes_in, weekly_profile
from .insights import cash_flow
from .production import evaluate_plan, explain_styles, optimize_production

USUAL_SPLIT = {"S": 0.15, "M": 0.25, "L": 0.25, "XL": 0.20, "XXL": 0.15}
BUDGET_STEPS = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4)


def usual_way_plan(sku: pd.DataFrame, per_style: dict, budget: float) -> np.ndarray:
    """What owners typically do: their total per style, colours split evenly, the usual size split."""
    q = np.zeros(len(sku))
    for sid, idx in sku.groupby("style_id", sort=False).indices.items():
        tot = per_style.get(sid, 0)
        sub = sku.iloc[idx]
        cols = sub.color.unique()
        for col in cols:
            ks = [k for k in idx if sku.color.iloc[k] == col]
            sz = [str(sku["size"].iloc[k]) for k in ks]
            w = np.array([USUAL_SPLIT.get(z, 0.0) for z in sz]) if all(z in USUAL_SPLIT for z in sz) else np.ones(len(ks))
            w = w / w.sum() if w.sum() > 0 else np.ones(len(ks)) / len(ks)
            for k, wk in zip(ks, w):
                q[k] = tot / len(cols) * wk
    q = np.round(q)
    cost = (q * sku.unit_cost).sum()
    if cost > budget:
        q = np.floor(q * budget / cost)
    return q.astype(int)


def own_data_check(inp: dict, fc: dict) -> dict | None:
    """Plan the most recent season using only the seasons before it, and compare with what really sold
    (sell-outs corrected). Needs 3+ seasons. Per-style error is what the owner feels as cash and leftovers."""
    cs = fc["corrected"]
    seasons = sorted(cs.season.unique())
    if len(seasons) < 3:   # with one earlier season there is no trend to learn: the test says nothing useful
        return None
    try:
        o, prod, labels = assign_seasons(inp["orders"], inp["production"])
        last = seasons[-1]
        o_tr = inp["orders"].loc[o.index[o.season < last]]
        p_tr = inp["production"][prod.season < last]
        f = build_forecast(o_tr, p_tr, inp["products"])
        seen = set(cs[cs.season < last].style_id)
        actual = cs[(cs.season == last) & cs.style_id.isin(seen)].groupby("style_id").demand_est.sum()
        pred = f["sku"].groupby("style_id")["mean"].sum().reindex(actual.index).fillna(0)
        if actual.sum() <= 0:
            return None
        # what most owners do: repeat the previous season's recorded sales
        prev = cs[(cs.season == seasons[-2])].groupby("style_id").sold.sum().reindex(actual.index).fillna(0)
        err = lambda x: float((x - actual).abs().sum() / actual.sum())  # noqa: E731
        rows = [dict(style=str(sid), predicted=_r(pred[sid]), actual=_r(actual[sid])) for sid in actual.index]
        return dict(season=str(labels[last]), styles=len(actual),
                    style_error=_r(err(pred), 3), repeat_last_error=_r(err(prev), 3),
                    total_bias=_r(float(pred.sum() / actual.sum() - 1), 3), rows=rows)
    except Exception:  # noqa: BLE001 - a check that can't run shouldn't block the plan
        return None


def _r(x, d=0):
    return float(round(float(x), d))


def build_view(inp: dict, n_scen: int = 200, budget_steps=BUDGET_STEPS, time_limit_s: int = 40,
               brand_name: str = "Your brand", simulated: bool = False, backtest: pd.DataFrame | None = None) -> dict:
    res = run_plan(inp, n_scen=n_scen, time_limit_s=time_limit_s)
    s = res["settings"]
    sku, fc = res["sku"], res["forecast"]
    lo = float(s["leftover_share"])
    scen = sample_scenarios(fc, n_scen, seed=3)          # what the planner optimises on
    scen_eval = sample_scenarios(fc, 800, seed=11)       # fresh seasons to score plans fairly
    base_budget = float(s["budget"])
    prof = weekly_profile(inp["orders"], inp["production"])
    refusal = float(res["returns"]["overall_refusal"])
    fee = float(res["couriers"]["table"].fee.mean())

    def pack(q, budget):
        ev = evaluate_plan(q, sku, scen_eval, lo)
        exp_sold = np.minimum(scen_eval, q).mean(0)
        cf = cash_flow(q, sku, exp_sold, float(s["start_cash"]), prof, float(s["deposit_share"]), int(s["lead_weeks"]),
                       int(s["remit_weeks"]), fee, refusal, float(s["ads_share"]), float(s["fixed_weekly"]))
        ex = explain_styles(q, sku, scen_eval, lo)
        return dict(budget=_r(budget), profit=_r(ev["profit"]), p10=_r(ev["profit_p10"]), p90=_r(ev["profit_p90"]),
                    revenue=_r(ev["revenue"]), cash_used=_r(ev["production_cost"]), units=int(q.sum()),
                    leftover=_r(ev["leftover_units"]), leftover_cost=_r(ev["leftover_cost"]), lost=_r(ev["lost_units"]),
                    fill=_r(ev["fill_rate"], 3), Q=[int(v) for v in q],
                    cash=[_r(v) for v in cf.balance], cash_min=_r(cf.balance.min()), cash_min_week=int(cf.balance.idxmin()),
                    style=[dict(id=r.style_id, planned=int(r.planned), sold=_r(r.expected_sold, 1),
                                left=_r(r.expected_leftover, 1), sellout=_r(r.chance_sell_out, 2)) for r in ex.itertuples()])

    variants = []
    for m in budget_steps:
        if abs(m - 1.0) < 1e-9:
            q = res["Q"]
        else:
            q = optimize_production(sku, scen, base_budget * m, moq_style=int(s["factory_moq"]),
                                    min_color=int(s["min_per_color"]), leftover_share=lo, time_limit_s=time_limit_s)["Q"]
        variants.append(dict(mult=m, **pack(q, base_budget * m)))

    est = inp["owner"] or {sid: v["mean"] for sid, v in fc["styles"].items()}
    usual = pack(usual_way_plan(sku, est, base_budget), base_budget)

    styles = []
    for sid, info in fc["styles"].items():
        g = sku[sku.style_id == sid]
        tot = scen_eval[:, g.index.to_numpy()].sum(1)
        styles.append(dict(id=sid, name=g.name.iloc[0], category=inp["products"].drop_duplicates("style_id").set_index("style_id").category.get(sid, ""),
                           colors=info["colors"], cost=_r(g.unit_cost.iloc[0]), price=_r(g.price.iloc[0]),
                           source=info["source"], f_mean=_r(tot.mean()), f_p10=_r(np.percentile(tot, 10)),
                           f_p90=_r(np.percentile(tot, 90)), owner=_r(inp["owner"].get(sid, 0)) if inp["owner"] else None,
                           size_share=[_r(x, 3) for x in info["size_sh"]]))
    cour = res["couriers"]
    cplan = cour["plan"].sort_values("share", ascending=False).drop_duplicates("zone")
    ctab = cour["table"]
    zones = []
    vol = inp["orders"].groupby("zone").size()
    cur = inp["orders"].groupby(["zone", "courier"]).size().unstack(fill_value=0)
    for r in cplan.itertuples():
        alts = ctab[ctab.zone == r.zone].sort_values("exp_profit", ascending=False)
        zones.append(dict(zone=r.zone, orders=int(vol.get(r.zone, 0)), pick=r.courier, rate=_r(r.p_hat, 3), fee=_r(r.fee),
                          profit=_r(r.exp_profit_per_order),
                          now=cur.loc[r.zone].idxmax() if r.zone in cur.index else "",
                          alts=[dict(courier=a.courier, rate=_r(a.p_hat, 3), fee=_r(a.fee), profit=_r(a.exp_profit),
                                     seen=int(a.orders)) for a in alts.itertuples()]))
    zones.sort(key=lambda z: -z["orders"])
    ret = res["returns"]
    ex = ret["exchanges"]
    data = dict(
        brand=brand_name, simulated=simulated,
        settings={k: (float(v) if isinstance(v, (int, float, np.number)) else v) for k, v in s.items()},
        sizes=sizes_in(inp["products"]),
        sku=[dict(s=r.style_id, c=r.color, z=r.size) for r in sku.itertuples()],
        styles=styles, variants=variants, base=list(budget_steps).index(1.0), usual=usual,
        couriers=dict(zones=zones, now=_r(cour["current_per_order"], 1), plan=_r(cour["optimized_per_order"], 1),
                      orders=int(cour["orders_per_season"] / max(1, fc["corrected"].season.nunique())), advice=cour["advice"].to_dict("records")),
        exchanges=dict(base=_r(ret["base_exchange"], 3),
                       rows=[dict(id=r.style_id, name=r.name, n=int(r.delivered), rate=_r(r.exchange_rate, 3),
                                  low=_r(r.low, 3), high=_r(r.high, 3), flag=bool(r.flag), size=r.worst_size,
                                  size_rate=_r(r.worst_size_rate, 3)) for r in ex.itertuples()]),
        refusal=dict(overall=_r(refusal, 3), zones=[dict(zone=z, rate=_r(r.refusal_rate, 3), n=int(r.orders))
                                                     for z, r in ret["refusal_by_zone"].iterrows()]),
        cash_weeks=len(variants[0]["cash"]), lead_weeks=int(s["lead_weeks"]),
        problems=res["problems"],
        history=dict(orders=int(len(inp["orders"])), seasons=int(fc["corrected"].season.nunique()),
                     soldout=int(fc["corrected"].sold_out.sum()), skus=int(len(sku)), growth=_r(fc["growth"], 3),
                     soldout_inferred=int(fc["corrected"].get("sold_out_inferred", pd.Series(dtype=bool)).sum()),
                     production_known=bool((inp["production"].produced > 0).any())),
        check=own_data_check(inp, fc),
    )
    if backtest is not None:
        data["backtest"] = backtest.to_dict("records")
    return data, res
