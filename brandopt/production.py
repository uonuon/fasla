"""How many pieces to produce of every style x colour x size.

Model (stochastic newsvendor, sample average approximation):
    maximise   avg over scenarios  sum_k (price_k - leftover_k) * sold_{s,k}
               - sum_k (cost_k - leftover_k) * Q_k
    subject to sold_{s,k} <= demand_{s,k},  sold_{s,k} <= Q_k + stock_k
               sum_k cost_k * Q_k <= budget                       (cash)
               style produced  ->  sum of its pieces >= factory MOQ
               colour produced ->  its pieces >= minimum per colour run
leftover_k is what an unsold piece is worth at the end (clearance price).
Without constraints the answer per SKU is the classic critical ratio
(price - cost) / (price - leftover) quantile of demand.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from ortools.linear_solver import pywraplp


def leftover_value(sku: pd.DataFrame, leftover_share: float) -> np.ndarray:
    """What an unsold piece is worth: leftover_share of its price, capped at a share of its cost: 95% by
    default, or the owner's own answer (column leftover_cap, e.g. 0.5 = "I clear them at about half of cost").
    The cap matters: at 30% of price a piece that costs 40% of price is worth 75% of its cost unsold, so
    overstock looks almost free and the plan makes about twice the likely demand."""
    p = sku.price.to_numpy(float)
    c = sku.unit_cost.to_numpy(float)
    cap = sku["leftover_cap"].to_numpy(float) if "leftover_cap" in sku.columns else 0.95
    return np.minimum(leftover_share * p, np.minimum(cap, 0.95) * c)


SOLVER = "SCIP"
BIG_CATALOG = 600


def optimize_production(sku: pd.DataFrame, scenarios: np.ndarray, budget: float,
                        moq_style: int = 100, min_color: int = 20, leftover_share: float = 0.30,
                        stock: np.ndarray | None = None, time_limit_s: int = 60,
                        must_produce: set | None = None, max_style: dict | None = None,
                        solver_name: str | None = None) -> dict:
    """sku needs columns style_id, color, size, unit_cost, price (same order as scenario columns)."""
    n, K = scenarios.shape
    c = sku.unit_cost.to_numpy(float)
    p = sku.price.to_numpy(float)
    v = leftover_value(sku, leftover_share)
    st0 = np.zeros(K) if stock is None else np.asarray(stock, float)
    if K > BIG_CATALOG:
        # the exact model grows as scenarios x SKUs: past ~600 SKUs SCIP stops at its time limit (and HiGHS,
        # ~2x faster, too, by ~2,800). The budget-priced plan is within a fraction of a percent there.
        q = budget_priced_plan(sku, scenarios, c, p, v, budget, moq_style, min_color, st0, must_produce, max_style)
        q = round_plan(sku, q, c, budget, moq_style, min_color)
        return dict(Q=q, status="fast", objective=_saa_value(q, scenarios, c, p, v, st0), leftover_value=v)
    solver = pywraplp.Solver.CreateSolver(solver_name or SOLVER)
    solver.SetTimeLimit(time_limit_s * 1000)
    Q = [solver.NumVar(0, solver.infinity(), f"Q{k}") for k in range(K)]
    sold = [[None] * K for _ in range(n)]
    obj = solver.Objective()
    for s in range(n):
        for k in range(K):
            d = scenarios[s, k]
            x = solver.NumVar(0, float(d), "")
            sold[s][k] = x
            ct = solver.Constraint(-solver.infinity(), float(st0[k]))
            ct.SetCoefficient(x, 1)
            ct.SetCoefficient(Q[k], -1)
            obj.SetCoefficient(x, (p[k] - v[k]) / n)
    for k in range(K):
        obj.SetCoefficient(Q[k], -(c[k] - v[k]))
    obj.SetMaximization()
    bud = solver.Constraint(0, float(budget))
    for k in range(K):
        bud.SetCoefficient(Q[k], c[k])
    big = scenarios.max(axis=0) * 3 + 50
    hint_q = _warm_start(sku, scenarios, c, p, v, budget, moq_style, min_color, st0, must_produce, max_style)
    fast_q = budget_priced_plan(sku, scenarios, c, p, v, budget, moq_style, min_color, st0, must_produce, max_style)
    if _saa_value(fast_q, scenarios, c, p, v, st0) > _saa_value(hint_q, scenarios, c, p, v, st0):
        hint_q = fast_q
    hint_vars, hint_vals = list(Q), [float(x) for x in hint_q]
    for sid, idx in sku.groupby("style_id", sort=False).indices.items():
        y = solver.BoolVar(f"y_{sid}")
        hint_vars.append(y)
        hint_vals.append(1.0 if hint_q[idx].sum() > 0 else 0.0)
        if must_produce and sid in must_produce:
            y.SetLb(1)
        ct = solver.Constraint(0, solver.infinity())          # sum Q >= moq * y
        for k in idx:
            ct.SetCoefficient(Q[k], 1)
        ct.SetCoefficient(y, -moq_style)
        if max_style and sid in max_style:
            cm = solver.Constraint(-solver.infinity(), float(max_style[sid]))
            for k in idx:
                cm.SetCoefficient(Q[k], 1)
        for col, cidx in sku.iloc[idx].groupby("color", sort=False).indices.items():
            ks = [idx[i] for i in cidx]
            w = solver.BoolVar("")
            hint_vars.append(w)
            hint_vals.append(1.0 if hint_q[ks].sum() > 0 else 0.0)
            cw = solver.Constraint(0, solver.infinity())       # colour run minimum
            for k in ks:
                cw.SetCoefficient(Q[k], 1)
            cw.SetCoefficient(w, -min_color)
            for k in ks:                                       # Q_k only if colour produced
                cu = solver.Constraint(-solver.infinity(), 0)
                cu.SetCoefficient(Q[k], 1)
                cu.SetCoefficient(w, -float(big[k]))
            cyw = solver.Constraint(-solver.infinity(), 0)     # colour only if style produced
            cyw.SetCoefficient(w, 1)
            cyw.SetCoefficient(y, -1)
    solver.SetHint(hint_vars, hint_vals)
    status = solver.Solve()
    if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        raise RuntimeError("No feasible production plan (budget too small for the factory minimums?)")
    q = np.array([Q[k].solution_value() for k in range(K)])
    # Under a tight time limit the solver can stop at its first feasible point, and "produce nothing" is always
    # feasible. Never return that silently: fall back to the warm start, which is a sound plan on its own.
    if q.sum() < 0.5 and hint_q.sum() > 0:
        q = hint_q.astype(float)
        status = pywraplp.Solver.FEASIBLE
    # a solver stopped by its time limit can return less than the quick plan: never keep the worse one
    if status != pywraplp.Solver.OPTIMAL and _saa_value(hint_q, scenarios, c, p, v, st0) > _saa_value(q, scenarios, c, p, v, st0):
        q = hint_q.astype(float)
    q = round_plan(sku, q, c, budget, moq_style, min_color)
    return dict(Q=q, status="optimal" if status == pywraplp.Solver.OPTIMAL else "feasible",
                objective=solver.Objective().Value(), leftover_value=v)


def round_plan(sku, q, c, budget, moq_style, min_color):
    """Round to whole pieces, keep minimums and the budget."""
    q = np.floor(q + 0.5)
    q[q < 0.5] = 0
    for sid, idx in sku.groupby("style_id", sort=False).indices.items():
        tot = q[idx].sum()
        if 0 < tot < moq_style:
            k = idx[np.argmax(q[idx])]
            q[k] += moq_style - tot
        for col, cidx in sku.iloc[idx].groupby("color", sort=False).indices.items():
            ks = [idx[i] for i in cidx]
            t = q[ks].sum()
            if 0 < t < min_color:
                q[ks[int(np.argmax(q[ks]))]] += min_color - t
    while (q * c).sum() > budget + 1e-6:
        # remove one piece from the SKU with the most pieces whose style/colour stays above minimum
        order = np.argsort(-q)
        done = False
        for k in order:
            if q[k] <= 0:
                break
            sid, col = sku.style_id.iloc[k], sku.color.iloc[k]
            st_tot = q[(sku.style_id == sid).to_numpy()].sum()
            co_tot = q[((sku.style_id == sid) & (sku.color == col)).to_numpy()].sum()
            if st_tot - 1 >= moq_style and co_tot - 1 >= min_color:
                q[k] -= 1
                done = True
                break
        if not done:
            break
    return q.astype(int)


def evaluate_plan(q: np.ndarray, sku: pd.DataFrame, demand: np.ndarray, leftover_share: float = 0.30,
                  stock: np.ndarray | None = None) -> dict:
    """Score a plan against demand scenarios (n x K). Returns averages per scenario."""
    c = sku.unit_cost.to_numpy(float)
    p = sku.price.to_numpy(float)
    v = leftover_value(sku, leftover_share)
    avail = q + (0 if stock is None else stock)
    sold = np.minimum(demand, avail)
    left = avail - sold
    lost = demand - sold
    revenue = (sold * p).sum(1)
    cost = (q * c).sum()
    leftover_val = (left * v).sum(1)
    profit = revenue + leftover_val - cost
    return dict(
        profit=profit.mean(), profit_p10=np.percentile(profit, 10), profit_p90=np.percentile(profit, 90),
        revenue=revenue.mean(), production_cost=cost, units=int(q.sum()),
        leftover_units=left.sum(1).mean(), leftover_cost=(left * c).sum(1).mean(),
        lost_units=lost.sum(1).mean(), lost_margin=(lost * (p - c)).sum(1).mean(),
        fill_rate=sold.sum() / max(demand.sum(), 1),
        sell_through=sold.sum(1).mean() / max(avail.sum(), 1),
    )


def explain_styles(q: np.ndarray, sku: pd.DataFrame, scen: np.ndarray, leftover_share: float = 0.30,
                   stock: np.ndarray | None = None) -> pd.DataFrame:
    """Per-style story: forecast, plan, critical ratio, risks. `planned` is new production; stock already on
    hand sells first and counts toward what is sold and left over."""
    st = np.zeros(len(q)) if stock is None else np.asarray(stock, float)
    c = sku.unit_cost.to_numpy(float)
    p = sku.price.to_numpy(float)
    v = leftover_value(sku, leftover_share)
    rows = []
    for sid, idx in sku.groupby("style_id", sort=False).indices.items():
        d = scen[:, idx].sum(1)
        qq = q[idx].sum()
        have = qq + st[idx].sum()
        sold = np.minimum(scen[:, idx], q[idx] + st[idx]).sum(1)
        cr = float(np.mean((p[idx] - c[idx]) / (p[idx] - v[idx])))
        rows.append(dict(style_id=sid, forecast_mean=d.mean(), forecast_p10=np.percentile(d, 10),
                         forecast_p90=np.percentile(d, 90), planned=int(qq), on_hand=int(st[idx].sum()),
                         critical_ratio=cr, expected_sold=sold.mean(), expected_leftover=(have - sold).mean(),
                         # demand for the style reaches everything there is of it. (It used to need every size and
                         # colour gone at once, which is ~0% for any style with a dozen SKUs and read as "never".)
                         chance_sell_out=float(np.mean(d >= have)) if have > 0 else 0.0,
                         cash=float((q[idx] * c[idx]).sum())))
    return pd.DataFrame(rows)


def _warm_start(sku, scenarios, c, p, v, budget, moq_style, min_color, stock, must_produce=None, max_style=None):
    """A quick plan that respects the budget and minimums: per SKU, the newsvendor quantile of demand
    (critical ratio (p-c)/(p-v)), minus stock; colours and styles under their minimums are dropped (unless the
    style must be produced), the rest scaled down to fit the budget."""
    ratio = np.clip((p - c) / np.maximum(p - v, 1e-9), 0.05, 0.95)
    q = np.array([np.quantile(scenarios[:, k], ratio[k]) for k in range(scenarios.shape[1])])
    q = np.maximum(np.floor(q - stock), 0)
    groups = sku.groupby("style_id", sort=False).indices
    for _ in range(8):
        for sid, idx in groups.items():
            if max_style and sid in max_style and q[idx].sum() > max_style[sid]:
                q[idx] = np.floor(q[idx] * max_style[sid] / q[idx].sum())
            for _, cidx in sku.iloc[idx].groupby("color", sort=False).indices.items():
                ks = idx[cidx]
                if 0 < q[ks].sum() < min_color:
                    q[ks] = 0
            if q[idx].sum() < moq_style:
                if must_produce and sid in must_produce and q[idx].sum() > 0:
                    q[idx] = np.ceil(q[idx] * moq_style / q[idx].sum())
                else:
                    q[idx] = 0
        spend = float(q @ c)
        if spend <= budget:
            break
        q = np.floor(q * budget / spend)
    if float(q @ c) > budget:
        return np.zeros_like(q)
    return q


def _style_groups(sku: pd.DataFrame) -> list:
    return [(sid, idx, [idx[ci] for ci in sku.iloc[idx].groupby("color", sort=False).indices.values()])
            for sid, idx in sku.groupby("style_id", sort=False).indices.items()]


def _saa_value(q, scenarios, c, p, v, stock) -> float:
    """The model's objective (expected profit over the scenarios) of plan q."""
    return float(((p - v) * np.minimum(scenarios, q + stock).mean(0)).sum() - ((c - v) * q).sum())


def budget_priced_plan(sku, scenarios, c, p, v, budget, moq_style, min_color, stock, must_produce=None,
                       max_style=None, iters=40) -> np.ndarray:
    """Near-optimal plan in milliseconds, for big catalogues and as the solver's starting point.

    The model is separable per SKU except for the budget. Price each unit of budget at lambda: every SKU's
    best quantity is then its demand quantile at (p - c - lambda*c) / (p - v), minus stock. Colours and styles
    under their minimums are raised to the minimum or dropped, whichever is worth more at that lambda.
    Bisect lambda until the plan fits the budget, then spend what the minimums' steps left over on whole
    styles/colours, best value per unit of cash first. On test instances: within 0.4% of the exact optimum,
    except tight budgets with high minimums (up to ~16% below), where the exact solver is also run."""
    n, K = scenarios.shape
    srt = np.sort(scenarios, axis=0)
    groups = _style_groups(sku)
    base = np.minimum(scenarios, stock).mean(0)

    def val(q, ks):
        return float(((p[ks] - v[ks]) * (np.minimum(scenarios[:, ks], q + stock[ks]).mean(0) - base[ks])
                      - (c[ks] - v[ks]) * q).sum())

    def raise_to(q, m):
        t = q.sum()
        up = np.round(q * m / t)
        if up.sum() < m:
            up[np.argmax(q)] += m - up.sum()
        return up

    def plan(lam):
        cr = np.clip((p - c - lam * c) / np.maximum(p - v, 1e-9), 0, 0.999)
        tgt = np.where(cr > 0, srt[np.minimum((cr * n).astype(int), n - 1), np.arange(K)], 0.0)
        q = np.maximum(np.round(tgt - stock), 0)
        for sid, idx, colors in groups:
            must = bool(must_produce and sid in must_produce)
            for ks in colors:
                t = q[ks].sum()
                if 0 < t < min_color:
                    up = raise_to(q[ks], min_color)
                    q[ks] = up if (must or val(up, ks) - lam * float(c[ks] @ up) > 0) else 0
            if max_style and sid in max_style and q[idx].sum() > max_style[sid]:
                q[idx] = np.floor(q[idx] * max_style[sid] / q[idx].sum())
            t = q[idx].sum()
            if must and t == 0:
                q[idx] = np.maximum(np.round(tgt[idx]), 0)
                if q[idx].sum() == 0:
                    q[idx[0]] = 1
                t = q[idx].sum()
            if 0 < t < moq_style:
                up = raise_to(q[idx], moq_style)
                q[idx] = up if (must or val(up, idx) - lam * float(c[idx] @ up) > 0) else 0
        return q

    q = plan(0.0)
    if float(c @ q) <= budget:
        return q
    lo, hi = 0.0, 1.0
    while float(c @ plan(hi)) > budget and hi < 1e6:
        hi *= 2
    best = plan(hi)
    if float(c @ best) > budget:
        return np.zeros(K)
    for _ in range(iters):
        mid = (lo + hi) / 2
        qm = plan(mid)
        if float(c @ qm) <= budget:
            hi, best = mid, qm
        else:
            lo = mid
    over, q = plan(lo), best.copy()
    cands = []
    for sid, idx, colors in groups:
        for ks in ([idx] if q[idx].sum() == 0 else colors):
            ks = np.asarray(ks)
            add = np.maximum(over[ks] - q[ks], 0)
            cost = float(c[ks] @ add)
            if cost > 0:
                gain = val(q[ks] + add, ks) - val(q[ks], ks)
                if gain > 0:
                    cands.append((gain / cost, ks, add, cost))
    left = budget - float(c @ q)
    for _, ks, add, cost in sorted(cands, key=lambda x: -x[0]):
        if cost <= left:
            q[ks] += add
            left -= cost
    return q
