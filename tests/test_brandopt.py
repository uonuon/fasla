import numpy as np
import pandas as pd
import pytest
from scipy.stats import poisson

from brandopt.couriers import choose_couriers, success_rates
from brandopt.forecast import build_forecast, corrected_sales, sample_scenarios
from brandopt.production import evaluate_plan, optimize_production
from brandopt.sim import BrandWorld, default_styles


def one_sku(cost=200, price=500, n=400, lam=100, seed=0):
    sku = pd.DataFrame([dict(style_id="A", color="Black", size="M", unit_cost=cost, price=price)])
    scen = np.random.default_rng(seed).poisson(lam, size=(n, 1)).astype(float)
    return sku, scen


def test_newsvendor_matches_critical_ratio():
    """Unconstrained single SKU: optimum = demand quantile at (p-c)/(p-v)."""
    sku, scen = one_sku()
    v = 0.30 * 500
    cr = (500 - 200) / (500 - v)
    res = optimize_production(sku, scen, budget=1e9, moq_style=0, min_color=0)
    target = poisson.ppf(cr, 100)
    assert abs(res["Q"][0] - target) <= 3


def test_budget_respected_and_binding():
    sku, scen = one_sku()
    res = optimize_production(sku, scen, budget=200 * 50, moq_style=0, min_color=0)
    assert res["Q"][0] * 200 <= 200 * 50
    assert res["Q"][0] >= 49


def test_moq_all_or_nothing():
    """Low demand style below MOQ: either produce >= MOQ or nothing."""
    sku = pd.DataFrame([dict(style_id="A", color="Black", size=s, unit_cost=300, price=600) for s in "SML"])
    scen = np.random.default_rng(1).poisson(10, size=(200, 3)).astype(float)
    res = optimize_production(sku, scen, budget=1e9, moq_style=100, min_color=20)
    tot = res["Q"].sum()
    assert tot == 0 or tot >= 100


def test_color_minimum():
    sku = pd.DataFrame([dict(style_id="A", color=c, size=s, unit_cost=200, price=600)
                        for c in ("Black", "Pink") for s in "SML"])
    lam = np.array([60, 80, 60, 2, 3, 2])
    scen = np.random.default_rng(2).poisson(lam, size=(200, 6)).astype(float)
    q = optimize_production(sku, scen, budget=1e9, moq_style=100, min_color=20)["Q"]
    pink = q[3:].sum()
    assert pink == 0 or pink >= 20


@pytest.fixture(scope="module")
def world_hist():
    w = BrandWorld(default_styles(), seed=7)
    return w, w.simulate_history()


def test_censoring_correction_raises_soldout_estimates(world_hist):
    w, h = world_hist
    cs = corrected_sales(h["orders"], h["production"])
    so = cs[cs.sold_out]
    assert len(so) > 0
    assert (so.demand_est >= so.sold - 1e-9).all()
    assert so.demand_est.sum() > so.sold.sum() * 1.05


def test_forecast_close_to_truth_for_existing_styles(world_hist):
    w, h = world_hist
    fc = build_forecast(h["orders"], h["production"], h["products"])
    est = fc["sku"].groupby("style_id")["mean"].sum()
    true = w.expected_sku_demand(2).groupby("style_id")["mean"].sum()
    old = [s.style_id for s in w.styles if not s.new]
    err = np.abs(np.log(est[old] / true[old]))
    assert np.median(err) < 0.25   # typical style within ~25%


def test_size_shares_recover_true_curve(world_hist):
    w, h = world_hist
    fc = build_forecast(h["orders"], h["production"], h["products"])
    sh = fc["sku"].groupby("size")["mean"].sum()
    sh = sh / sh.sum()
    true = w.expected_sku_demand(2).groupby("size")["mean"].sum()
    true = true / true.sum()
    assert np.abs(sh - true).max() < 0.04


def test_scenarios_shape_and_mean(world_hist):
    w, h = world_hist
    fc = build_forecast(h["orders"], h["production"], h["products"])
    sc = sample_scenarios(fc, 400, seed=1)
    assert sc.shape == (400, len(fc["sku"]))
    ratio = sc.sum(1).mean() / fc["sku"]["mean"].sum()
    assert 0.9 < ratio < 1.1


def test_courier_choice_prefers_better_courier(world_hist):
    w, h = world_hist
    ch = choose_couriers(h["orders"], h["couriers"], avg_margin=650)
    best = ch["plan"].sort_values("share", ascending=False).drop_duplicates("zone").set_index("zone").courier
    assert best["Cairo"] == "CityRunner"          # cheapest and most reliable in Cairo
    assert best["Upper Egypt"] == "NileExpress"   # FastBox refusals are much higher there
    assert ch["optimized_per_order"] >= ch["current_per_order"]


def test_courier_min_share_contract(world_hist):
    w, h = world_hist
    ch = choose_couriers(h["orders"], h["couriers"], avg_margin=650, min_share={"FastBox": 0.4})
    vol = h["orders"].groupby("zone").size()
    fb = ch["plan"][ch["plan"].courier == "FastBox"]
    share = (fb.share * fb.zone.map(vol)).sum() / vol.sum()
    assert share >= 0.4 - 1e-6


def test_evaluate_plan_accounting():
    sku, _ = one_sku()
    d = np.array([[80.0], [120.0]])
    r = evaluate_plan(np.array([100]), sku, d)
    # scenario 1: sell 80, 20 left at 150 -> 40000 + 3000 - 20000; scenario 2: sell 100 -> 50000 - 20000
    assert r["profit"] == pytest.approx(((40000 + 3000 - 20000) + (50000 - 20000)) / 2)
    assert r["lost_units"] == pytest.approx(10)


def test_forecast_accuracy_guardrail():
    """Accuracy on simulated brands with known true demand (python -m brandopt.accuracy for the full table).
    Before sell-out detection and the stable-sizes growth rule: orders-only bias -22%, template +8%."""
    from brandopt.accuracy import run
    d = run(6).groupby("variant")[["style_wape", "bias"]].mean()
    oo, tp = d.loc["orders only"], d.loc["template (production known)"]
    assert abs(oo.bias) < 0.12 and oo.style_wape < 0.20, oo.to_dict()
    assert abs(tp.bias) < 0.08 and tp.style_wape < 0.23, tp.to_dict()


def test_running_season_is_not_read_as_a_drop(world_hist):
    """An export taken mid-season (the usual case for orders-only uploads and Shopify) must not shrink the plan.
    Before season coverage: cutting the latest season after 6 of its 16 weeks cut the forecast by ~40%."""
    w, h = world_hist
    full = build_forecast(h["orders"], h["production"], h["products"])["sku"]["mean"].sum()
    cut = h["orders"][h["orders"].date < pd.Timestamp("2025-11-12")]
    part = build_forecast(cut, h["production"], h["products"])["sku"]["mean"].sum()
    assert 0.8 < part / full < 1.25, (part, full)
