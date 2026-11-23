"""Excel in, Excel out: the brand fills one workbook, gets one report back."""
from __future__ import annotations

import numpy as np
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .couriers import choose_couriers
from .forecast import build_forecast, sample_scenarios, weekly_profile
from .insights import cash_flow, returns_report
from .production import evaluate_plan, explain_styles, optimize_production

SETTINGS = [
    ("budget", 2_000_000, "Cash available for production this season (EGP)"),
    ("factory_moq", 100, "Factory minimum pieces per design"),
    ("min_per_color", 20, "Minimum pieces per colour run"),
    ("leftover_share", 0.30, "What an unsold piece is worth at season end, as a share of price"),
    ("start_cash", 2_300_000, "Cash in the bank today (EGP)"),
    ("deposit_share", 0.5, "Share of production paid to the factory up front"),
    ("lead_weeks", 4, "Weeks from order to stock arriving"),
    ("remit_weeks", 1, "Weeks until couriers pay COD cash back"),
    ("ads_share", 0.12, "Ad spend as a share of revenue"),
    ("fixed_weekly", 15000, "Fixed weekly costs: rent, salaries (EGP)"),
]

README = [
    "Brand Planner input workbook",
    "",
    "Fill these sheets, then run the planner. Example rows are from a made-up brand.",
    "Products: one row per style x colour x size, with unit cost and selling price.",
    "Orders: every order from past seasons. status = delivered, refused, or size_exchange.",
    "Production: how many pieces you produced per style x colour x size each season.",
    "Couriers: fee and return fee per courier and zone.",
    "OwnerEstimates (optional): your own guess of pieces you will sell per style next season.",
    "Settings: budget, factory minimums, cash, and timings.",
]

FILL_HEAD = PatternFill("solid", fgColor="1F2A44")
FONT_HEAD = Font(bold=True, color="FFFFFF")


def _style_sheet(ws, df: pd.DataFrame, widths: dict | None = None, money: tuple = (), pct: tuple = ()):
    for j, col in enumerate(df.columns, 1):
        c = ws.cell(row=1, column=j)
        c.fill, c.font = FILL_HEAD, FONT_HEAD
        c.alignment = Alignment(vertical="center", wrap_text=True)
        w = (widths or {}).get(col, max(10, min(40, len(str(col)) + 2)))
        ws.column_dimensions[get_column_letter(j)].width = w
        for i in range(2, ws.max_row + 1):
            cell = ws.cell(row=i, column=j)
            if col in money:
                cell.number_format = '#,##0'
            elif col in pct:
                cell.number_format = '0.0%'
    ws.freeze_panes = "A2"


def write_inputs(path: str, products, orders, production, couriers, owner_estimates=None, settings=None):
    st = dict((k, v) for k, v, _ in SETTINGS)
    st.update(settings or {})
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame({"Read me": README}).to_excel(xw, sheet_name="README", index=False)
        pd.DataFrame([(k, st[k], d) for k, _, d in SETTINGS], columns=["setting", "value", "meaning"]).to_excel(xw, sheet_name="Settings", index=False)
        products.to_excel(xw, sheet_name="Products", index=False)
        o = orders.copy()
        o["date"] = pd.to_datetime(o.date).dt.date
        o.to_excel(xw, sheet_name="Orders", index=False)
        production.to_excel(xw, sheet_name="Production", index=False)
        couriers.to_excel(xw, sheet_name="Couriers", index=False)
        oe = owner_estimates if owner_estimates is not None else pd.DataFrame(columns=["style_id", "expected_units"])
        oe.to_excel(xw, sheet_name="OwnerEstimates", index=False)
    wb = load_workbook(path)
    for name in wb.sheetnames:
        ws = wb[name]
        df = pd.DataFrame([[c.value for c in ws[1]]])
        _style_sheet(ws, pd.DataFrame(columns=[c.value for c in ws[1]]), widths={"meaning": 60, "Read me": 100})
    wb.save(path)


def read_inputs(path: str) -> dict:
    x = pd.read_excel(path, sheet_name=None)
    s = dict(zip(x["Settings"].setting, x["Settings"].value))
    orders = x["Orders"].copy()
    orders["date"] = pd.to_datetime(orders.date)
    oe = x.get("OwnerEstimates")
    owner = {} if oe is None or oe.empty else dict(zip(oe.style_id, oe.expected_units))
    problems = check_inputs(x["Products"], orders, x["Production"], x["Couriers"])
    return dict(settings=s, products=x["Products"], orders=orders, production=x["Production"],
                couriers=x["Couriers"], owner=owner, problems=problems)


def check_inputs(products, orders, production, couriers) -> list:
    """Plain-language data problems, so the owner can fix them before trusting the plan."""
    p = []
    keys = set(zip(products.style_id, products.color, products["size"]))
    bad = orders[[k not in keys for k in zip(orders.style_id, orders.color, orders["size"])]]
    if len(bad):
        p.append(f"{len(bad)} orders are for products not in the Products sheet (e.g. {bad.style_id.iloc[0]} {bad.color.iloc[0]} {bad['size'].iloc[0]}).")
    st = set(orders.status.unique()) - {"delivered", "refused", "size_exchange"}
    if st:
        p.append(f"Unknown order status values: {sorted(st)}. Use delivered, refused or size_exchange.")
    if (products.price <= products.unit_cost).any():
        p.append("Some products sell at or below cost.")
    z = set(orders.zone) - set(couriers.zone)
    if z:
        p.append(f"Zones with orders but no courier price: {sorted(z)}.")
    return p


def sku_stock(sku: pd.DataFrame) -> np.ndarray:
    """Pieces already on hand per SKU (unknown = 0), in sku row order."""
    if "on_hand" not in sku.columns:
        return np.zeros(len(sku))
    return pd.to_numeric(sku.on_hand, errors="coerce").fillna(0).clip(lower=0).to_numpy(float)


def run_plan(inp: dict, n_scen: int = 200, time_limit_s: int = 60) -> dict:
    s = inp["settings"]
    fc = build_forecast(inp["orders"], inp["production"], inp["products"], owner_estimates=inp["owner"] or None)
    cols = ["style_id", "name", "color", "size", "unit_cost", "price"]
    cols += ["on_hand"] if "on_hand" in inp["products"].columns else []
    sku = fc["sku"].merge(inp["products"][cols], on=["style_id", "color", "size"])
    stock = sku_stock(sku)
    scen = sample_scenarios(fc, n_scen, seed=3)
    opt = optimize_production(sku, scen, float(s["budget"]), moq_style=int(s["factory_moq"]),
                              min_color=int(s["min_per_color"]), leftover_share=float(s["leftover_share"]),
                              stock=stock, time_limit_s=time_limit_s)
    q = opt["Q"]
    ev = evaluate_plan(q, sku, scen, float(s["leftover_share"]), stock=stock)
    expl = explain_styles(q, sku, scen, float(s["leftover_share"]), stock=stock)
    margin = float(((sku.price - sku.unit_cost) * sku["mean"]).sum() / sku["mean"].sum())
    cour = choose_couriers(inp["orders"], inp["couriers"], margin)
    ret = returns_report(inp["orders"], inp["products"])
    exp_sold = np.minimum(scen, q + stock).mean(0)
    prof = weekly_profile(inp["orders"], inp["production"])
    cf = cash_flow(q, sku, exp_sold, float(s["start_cash"]), prof, float(s["deposit_share"]), int(s["lead_weeks"]),
                   int(s["remit_weeks"]), float(cour["table"].fee.mean()), float(ret["overall_refusal"]),
                   float(s["ads_share"]), float(s["fixed_weekly"]))
    return dict(sku=sku, Q=q, stock=stock, forecast=fc, eval=ev, explain=expl, couriers=cour, returns=ret, cash=cf,
                settings=s, margin=margin, problems=inp["problems"])


def write_report(res: dict, path: str, backtest: pd.DataFrame | None = None):
    sku, q, s = res["sku"], res["Q"], res["settings"]
    ev, cour, ret, cf = res["eval"], res["couriers"], res["returns"], res["cash"]
    names = sku.drop_duplicates("style_id").set_index("style_id").name
    plan = sku[["style_id", "name", "color", "size", "unit_cost", "price"]].copy()
    plan["forecast"] = sku["mean"].round(1)
    plan["produce"] = q
    plan["cash"] = q * sku.unit_cost
    piv = plan.pivot_table(index=["style_id", "name", "color"], columns="size", values="produce", aggfunc="sum", sort=False)
    from .forecast import sizes_in
    piv = piv[[c for c in sizes_in(sku) if c in piv.columns]]
    piv["Total"] = piv.sum(axis=1)
    piv = piv.reset_index()
    ex = res["explain"].copy()
    ex.insert(1, "name", ex.style_id.map(names))
    ex["source"] = ex.style_id.map({k: v["source"] for k, v in res["forecast"]["styles"].items()})
    ex["why"] = [
        ("Not produced: expected sales too low for the factory minimum or a poor use of cash" if r.planned == 0 else
         f"Plan {r.planned} vs forecast {r.forecast_mean:.0f}: a piece earns {r.critical_ratio:.0%} of its risk back, "
         f"so it pays to cover {'more' if r.planned > r.forecast_mean else 'less'} than the average")
        for r in ex.itertuples()]
    ex = ex[["style_id", "name", "source", "forecast_p10", "forecast_mean", "forecast_p90", "planned", "expected_sold",
             "expected_leftover", "chance_sell_out", "cash", "why"]]
    plan_c = cour["plan"].copy()
    min_bal = cf.balance.min()
    summary = [
        ("Production budget (EGP)", float(s["budget"])),
        ("Cash used by the plan (EGP)", float((q * sku.unit_cost).sum())),
        ("Pieces to produce", int(q.sum())),
        ("Styles produced", int((res["explain"].planned > 0).sum())),
        ("Expected profit this season (EGP)", ev["profit"]),
        ("Bad-case profit, 1 in 10 seasons (EGP)", ev["profit_p10"]),
        ("Expected pieces left at season end", ev["leftover_units"]),
        ("Expected sales missed (sold out)", ev["lost_units"]),
        ("Expected share of demand served", ev["fill_rate"]),
        ("Courier choice: profit per COD order today (EGP)", cour["current_per_order"]),
        ("Courier choice: profit per COD order with plan (EGP)", cour["optimized_per_order"]),
        ("Lowest cash balance in the season (EGP)", float(min_bal)),
        ("Week of lowest cash", int(cf.loc[cf.balance.idxmin(), "week"])),
        ("Overall COD refusal rate", ret["overall_refusal"]),
        ("Data problems found", len(res["problems"])),
    ]
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame(summary, columns=["What", "Value"]).to_excel(xw, sheet_name="Summary", index=False)
        piv.to_excel(xw, sheet_name="Production plan", index=False)
        ex.to_excel(xw, sheet_name="Why this plan", index=False)
        plan.to_excel(xw, sheet_name="Plan per SKU", index=False)
        plan_c[["zone", "courier", "share", "p_hat", "fee", "exp_profit_per_order", "orders_seen"]].rename(
            columns={"p_hat": "delivery_rate", "exp_profit_per_order": "profit_per_order"}).to_excel(xw, sheet_name="Courier per zone", index=False)
        cour["table"][["courier", "zone", "fee", "return_fee", "orders", "p_hat", "p_low", "p_high", "exp_profit", "cost_per_delivered"]].rename(
            columns={"p_hat": "delivery_rate", "p_low": "rate_low", "p_high": "rate_high", "exp_profit": "profit_per_order"}).to_excel(xw, sheet_name="All couriers", index=False)
        cour["advice"].to_excel(xw, sheet_name="COD risk", index=False)
        ret["exchanges"].to_excel(xw, sheet_name="Size exchanges", index=False)
        ret["refusal_by_zone"].reset_index().to_excel(xw, sheet_name="Refusals by zone", index=False)
        cf.to_excel(xw, sheet_name="Cash flow", index=False)
        pd.DataFrame({"problem": res["problems"] or ["No problems found"]}).to_excel(xw, sheet_name="Data checks", index=False)
        if backtest is not None:
            backtest.to_excel(xw, sheet_name="Backtest", index=False)
    wb = load_workbook(path)
    money_cols = ("Value", "cash", "unit_cost", "price", "fee", "return_fee", "profit_per_order", "cost_per_delivered",
                  "cash_in", "cash_out", "balance", "forecast_p10", "forecast_mean", "forecast_p90", "expected_sold",
                  "expected_leftover", "profit", "leftover_units", "lost_units")
    pct_cols = ("share", "delivery_rate", "rate_low", "rate_high", "chance_sell_out", "exchange_rate", "low", "high",
                "worst_size_rate", "refusal_rate", "fill_rate", "gain_vs_owner")
    for name in wb.sheetnames:
        ws = wb[name]
        cols = [c.value for c in ws[1]]
        _style_sheet(ws, pd.DataFrame(columns=cols), widths={"What": 52, "why": 90, "advice": 60, "name": 26, "problem": 90, "plan": 44},
                     money=money_cols, pct=pct_cols)
    ws = wb["Summary"]
    for i, (k, v) in enumerate(summary, 2):
        c = ws.cell(row=i, column=2)
        c.number_format = '0.0%' if ("rate" in k or "share" in k) else '#,##0'
    wb.save(path)
