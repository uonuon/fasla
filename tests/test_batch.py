from datetime import date

import numpy as np
import pandas as pd
import pytest

from brandopt.batch import (BatchSettings, clean_orders, clean_products, forecast_monthly, materials_plan,
                            monthly_table, pack_amount, plan_batch, ramadan_share)
from brandopt.batch_excel import read_inputs, run, write_report, write_template
from brandopt.batch_sample import sample_brand, sample_food_brand


def test_pack_amount_reads_english_and_arabic():
    assert pack_amount("250 ml") == (250, "ml")
    assert pack_amount("1 L") == (1000, "ml")
    assert pack_amount("500g") == (500, "g")
    assert pack_amount("١٠٠ مل") == (100, "ml")
    assert pack_amount("نص كيلو") == (None, "")
    assert pack_amount("0.5 kg") == (500, "g")


def test_ramadan_share():
    assert ramadan_share(pd.Timestamp("2026-03-01")) > 0.6       # Ramadan 2026: 18 Feb - 19 Mar, Eid after
    assert ramadan_share(pd.Timestamp("2026-07-01")) == 0
    assert 0.3 < ramadan_share(pd.Timestamp("2026-02-01")) < 0.5


def test_orders_with_arabic_headers_and_cancellations():
    o = pd.DataFrame({"التاريخ": ["2026-01-05", "2026-01-06", "2026-01-07"], "المنتج": ["عسل", "عسل", "عسل"],
                      "الحجم": ["250 g"] * 3, "الكمية": [1, 2, 3], "الحالة": ["delivered", "ملغي", "refused"]})
    c = clean_orders(o)
    assert c.qty.sum() == 4                         # cancelled dropped, refused kept as demand
    assert set(c.columns) == {"date", "product", "variant", "pack", "qty"}


def test_products_need_price_and_cost():
    with pytest.raises(ValueError):
        clean_products(pd.DataFrame({"product": ["x"], "price": [10]}))


@pytest.fixture(scope="module")
def food():
    return sample_food_brand()


def test_plan_respects_batch_rules(food):
    orders, products = food
    p = products.copy()
    p.loc[p["product"] == "Beef Tallow", "batch_multiple"] = 6
    r = plan_batch(orders, p, BatchSettings())
    plan = r["plan"]
    pp = clean_products(p)
    for (prod, var), g in plan.groupby(["product", "variant"]):
        info = pp[(pp["product"] == prod) & (pp["variant"] == var)]
        amt = (g.make.to_numpy() * info.amount.to_numpy()).sum()
        if g.make.sum() > 0 and info.batch_min_amount.max() > 0:
            assert amt >= info.batch_min_amount.max() - 1e-6, (prod, var)
    tallow = plan[plan["product"] == "Beef Tallow"]
    assert (tallow.make % 6 == 0).all()
    assert (plan.make >= 0).all()


def test_budget_is_respected(food):
    orders, products = food
    full = plan_batch(orders, products, BatchSettings())
    cut = plan_batch(orders, products, BatchSettings(budget=full["total_cost"] * 0.5))
    assert cut["total_cost"] <= full["total_cost"] * 0.5 + 1e-6
    assert any("budget" in n for n in cut["notes"])


def test_shelf_life_caps_production(food):
    orders, products = food
    p = products.copy()
    p.loc[p["product"] == "Figs & Olive", "batch_min_amount"] = 0
    base = plan_batch(orders, p, BatchSettings())["plan"]
    p["shelf_life_months"] = p["shelf_life_months"].astype(float)
    p.loc[p["product"] == "Figs & Olive", "shelf_life_months"] = 1.2      # sells for only ~0.2 months after margin
    short = plan_batch(orders, p, BatchSettings())["plan"]
    f0 = base[base["product"] == "Figs & Olive"].make.iloc[0]
    f1 = short[short["product"] == "Figs & Olive"].make.iloc[0]
    assert f1 < f0


def test_stock_on_hand_reduces_production(food):
    orders, products = food
    p = products.copy()
    p["batch_min_amount"] = 0
    a = plan_batch(orders, p, BatchSettings())["plan"].make.sum()
    p["on_hand"] = p["on_hand"] + 30
    b = plan_batch(orders, p, BatchSettings())["plan"].make.sum()
    assert b < a


def test_forecast_beats_three_month_average():
    errs = []
    for seed in range(1, 5):
        o, p = sample_brand(seed=seed, end="2026-11-30", months=26)
        o, p = clean_orders(o), clean_products(p)
        p = p[p.expected_monthly_units == 0].reset_index(drop=True)
        for cut in ("2026-01", "2026-06"):
            ts = pd.Period(cut).to_timestamp()
            tr, te = o[o.date < ts], o[o.date >= ts]
            fc = forecast_monthly(tr, p, 3, first_month=pd.Period(cut))
            act = monthly_table(te, p).iloc[:3].to_numpy()
            naive = monthly_table(tr, p).to_numpy()[-3:].mean(0)
            errs.append((np.abs(fc["mean"][:len(act)] - act).sum() / act.sum(),
                         np.abs(naive - act).sum() / act.sum()))
    ours, avg3 = np.mean(errs, axis=0)
    assert ours < avg3
    assert ours < 0.2


def test_new_product_uses_owner_estimate():
    o, p = sample_brand()
    r = plan_batch(o, p, BatchSettings())
    row = r["plan"][r["plan"]["product"] == "Hair Growth Serum"].iloc[0]
    assert row.make > 0 and "new" in row.note


def test_excel_round_trip(tmp_path):
    src, out = tmp_path / "in.xlsx", tmp_path / "out.xlsx"
    write_template(str(src))
    inp = read_inputs(str(src))
    res = run(inp)
    write_report(res, str(out))
    sheets = pd.read_excel(out, sheet_name=None)
    assert {"Summary", "Make this batch", "Batches", "Materials to order", "Forecast by month"} <= set(sheets)
    assert sheets["Make this batch"].make.sum() > 0
    blank = tmp_path / "blank.xlsx"
    write_template(str(blank), example=False)
    with pytest.raises(ValueError):
        run(read_inputs(str(blank)))


def test_materials_late_flag(food):
    orders, products = food
    r = plan_batch(orders, products, BatchSettings(plan_start=date(2026, 12, 20)))
    bom = pd.DataFrame([("*", "*", "*", "Label", 1)], columns=["product", "variant", "pack", "material", "qty_per_unit"])
    mats = pd.DataFrame([("Label", 0, 30, 100, 1)], columns=["material", "on_hand", "lead_time_days", "order_multiple", "unit_cost"])
    m = materials_plan(r, bom, mats, today=date(2026, 11, 25))
    assert m.iloc[0].to_order % 100 == 0 and m.iloc[0].to_order >= r["plan"].make.sum()
    assert m.iloc[0].status.startswith("late")
    m2 = materials_plan(r, bom, mats, today=date(2026, 11, 1))
    assert m2.iloc[0].status == "order"
