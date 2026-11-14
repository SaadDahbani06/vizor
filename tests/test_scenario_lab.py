"""Milestone 5: Scenario Lab counterfactual engine (product price, supply, demand, marketing)."""
import numpy as np
import pytest

from decisiondesk.agent.tools import BusinessSession, run_tool
from decisiondesk.decision.product_scenario import (
    BOM,
    CARD_FEE_RATE,
    PACKAGING_PER_GUEST,
    ProductScenario,
    baseline,
    compare,
    recommend,
    sensitivity,
    simulate,
    stockout_risk,
)
from decisiondesk.products import MAINS, PRODUCT_IDS
from decisiondesk.ui import views

FAST = ("seasonal_naive", "weekday_mean_12w", "historical_mean_28d", "ridge")
TACO = PRODUCT_IDS.index("tacos")


@pytest.fixture(scope="module")
def session():
    return BusinessSession.from_demo("restaurant", models=FAST, n_sims=400)


@pytest.fixture(scope="module")
def ctx(session):
    return session.product_context()


def run(ctx, **kw):
    sc = ProductScenario(**{"product_id": "tacos", **kw})
    base = baseline(ctx, sc.horizon_days, sc.product_id)
    scen = simulate(ctx, sc)
    return sc, base, scen, compare(ctx, scen, base)


# ------------------------------------------------------------------ baseline and identities
def test_zero_change_scenario_equals_baseline_exactly(ctx):
    for h in (7, 14, 28):
        _sc, base, scen, c = run(ctx, horizon_days=h)
        for attr in ("revenue", "variable", "fixed", "profit", "cash", "sold", "demand", "lost"):
            np.testing.assert_array_equal(getattr(scen, attr), getattr(base, attr))
        assert c["profit"]["change"] == 0 and c["revenue"]["change_90pct_range"] == [0.0, 0.0]
        assert scen.revenue.shape == (ctx.n_sims, h)


def test_simulation_is_reproducible(ctx):
    a = simulate(ctx, ProductScenario("burritos", price_change_pct=5))
    b = simulate(ctx, ProductScenario("burritos", price_change_pct=5))
    np.testing.assert_array_equal(a.profit, b.profit)


def test_profit_cost_and_revenue_identities(ctx):
    _sc, _base, scen, _c = run(ctx, price_change_pct=4, demand_shock_pct=10)
    np.testing.assert_allclose(scen.profit, scen.revenue - scen.variable - scen.fixed)
    mains = scen.sold[:, :, [PRODUCT_IDS.index(p) for p in MAINS]].sum(-1)
    rev = (scen.sold * scen.prices).sum(-1) + ctx.other_rev_per_main * mains
    np.testing.assert_allclose(scen.revenue, rev)
    var = ((scen.sold * ctx.unit_cost).sum(-1) + ctx.other_food_per_main * mains
           + PACKAGING_PER_GUEST * mains / ctx.mains_per_guest + CARD_FEE_RATE * scen.revenue)
    np.testing.assert_allclose(scen.variable, var)
    assert np.all(scen.sold <= scen.demand + 1e-9)          # sales never exceed demand
    assert np.all(scen.end_stock >= -1e-9)                   # stock never negative


def test_product_baseline_reconciles_with_aggregate_revenue_model(ctx):
    base = baseline(ctx, 28)
    agg = ctx.agg.targets["revenue"].point.sum()
    assert abs(base.revenue.sum(1).mean() / agg - 1) < 0.05


# ------------------------------------------------------------------ price and demand
def test_price_increase_lowers_demand_and_revenue_does_not_rise_one_for_one(ctx):
    _sc, base, scen, c = run(ctx, price_change_pct=10)
    assert c["demand_units"]["change"] < 0 and c["units_sold"]["change"] < 0
    taco_rev = c["products"][TACO]["revenue_change"]
    base_taco_rev = base.product_revenue[:, TACO].mean()
    assert taco_rev < 0.10 * base_taco_rev                    # not +10%
    assert c["new_price"] == pytest.approx(round(ctx.prices[TACO] * 1.10, 2))
    # substitution: some lost taco demand moves to the other main dishes
    others = [PRODUCT_IDS.index(p) for p in MAINS if p != "tacos"]
    assert (scen.demand[:, :, others].sum() > base.demand[:, :, others].sum())
    assert scen.demand[:, :, PRODUCT_IDS.index("drinks")].sum() == pytest.approx(
        base.demand[:, :, PRODUCT_IDS.index("drinks")].sum())


def test_demand_response_follows_constant_elasticity(ctx):
    sc, base, scen, _c = run(ctx, price_change_pct=10)
    ratio = scen.demand[:, :, TACO].sum(1) / base.demand[:, :, TACO].sum(1)
    implied = np.log(ratio) / np.log(1.10)
    assert implied.min() >= -1.8 - 1e-6 and implied.max() <= -0.6 + 1e-6   # within the assumed range
    pinned = simulate(ctx, sc, fixed_levels={"elasticity": "high"})
    np.testing.assert_allclose(pinned.demand[:, :, TACO], base.demand[:, :, TACO] * 1.10 ** -0.6)


def test_price_cut_raises_demand(ctx):
    _, _, _, c = run(ctx, price_change_pct=-10)
    assert c["demand_units"]["change"] > 0 and c["new_price"] < c["current_price"]


def test_demand_shock_scope(ctx):
    _, base, scen, _c = run(ctx, demand_shock_pct=20)
    np.testing.assert_allclose(scen.demand[:, :, TACO], base.demand[:, :, TACO] * 1.2)
    np.testing.assert_allclose(scen.demand[:, :, 1:], base.demand[:, :, 1:])
    _, base, scen, _ = run(ctx, demand_shock_pct=20, demand_shock_scope="all")
    np.testing.assert_allclose(scen.demand, base.demand * 1.2)


# ------------------------------------------------------------------ supply and stock-outs
def test_cutting_supply_caps_sales_but_not_demand(ctx):
    _sc, base, scen, c = run(ctx, supply_change_pct=-80)
    np.testing.assert_allclose(scen.demand, base.demand)    # customers still want tacos
    assert c["units_sold"]["change"] < 0
    assert c["stockout_risk"]["scenario"] > c["stockout_risk"]["baseline"]
    assert c["lost_units"]["scenario"] > c["lost_units"]["baseline"]
    assert c["revenue"]["change"] < 0


def test_demand_surge_without_more_stock_creates_stockouts(ctx):
    _, base, scen, c = run(ctx, demand_shock_pct=50)
    assert stockout_risk(scen, TACO) > stockout_risk(base, TACO)
    assert c["units_sold"]["change"] < c["demand_units"]["change"]  # sales lag demand


def test_extra_purchase_is_paid_early_and_the_ordering_rule_orders_less_later(ctx):
    _sc, base, scen, c = run(ctx, extra_units=600, horizon_days=14)
    cost = 600 * BOM[TACO] @ ctx.ing_cost
    np.testing.assert_allclose(scen.purchases[:, 0], base.purchases[:, 0] + cost)
    # order-up-to: the next deliveries top up to the same target, so they shrink
    assert scen.purchases[:, 1:].sum() < base.purchases[:, 1:].sum()
    assert c["ending_inventory_value"]["change"] >= -1e-6
    # profit counts ingredients when used; cash counts them when paid (net-7): they differ
    assert c["cash_flow"]["change"] != pytest.approx(c["profit"]["change"])


def test_marketing_cut_saves_money_and_may_cost_demand(ctx):
    _, base, scen, c = run(ctx, marketing_change_pct=-100)
    saved = ctx.marketing_monthly * 12 / 365.25 * 28
    np.testing.assert_allclose(base.fixed.sum(1) - scen.fixed.sum(1), saved)
    assert c["demand_units"]["change"] <= 0


# ------------------------------------------------------------------ uncertainty, policy, recommendation
def test_uncertainty_ranges_are_ordered_and_explained(ctx):
    _, _, _, c = run(ctx, price_change_pct=5)
    lo, hi = c["profit"]["change_90pct_range"]
    assert lo <= c["profit"]["change"] <= hi and lo < hi
    assert 0 <= c["probability_profit_improves"] <= 1
    assert "not a guarantee" in c["interval_meaning"] and "NOT an estimated causal effect" in c["effect_type"]


def test_price_policy_is_flagged_not_overridden(ctx):
    _, _, _, c = run(ctx, price_change_pct=10)
    assert c["within_policy"] is False and "exceeds" in c["policy_violations"][0]
    rec = recommend(ctx, ProductScenario("tacos", price_change_pct=10))
    assert all(r["within_policy"] for r in rec["ranked"])
    assert any(r["scenario"]["price_change_pct"] == 10 for r in rec["outside_policy"])
    assert rec["requires_human_approval"]


def test_recommendation_and_sensitivity_come_from_simulations(ctx):
    rec = recommend(ctx, ProductScenario("tacos"))
    assert rec["ranked"] == sorted(rec["ranked"], key=lambda r: -r["profit_change"])
    if rec["recommended"]:
        assert rec["recommended"]["action"] in rec["text"]
        sens = sensitivity(ctx, ProductScenario(**rec["recommended"]["scenario"]))
        assert sens == rec["sensitivity"]
        assert {s["assumption"] for s in sens} >= {"substitution_share"}


@pytest.mark.parametrize("bad", [{"product_id": "pizza"}, {"horizon_days": 10}, {"price_change_pct": 80},
                                 {"extra_units": -5}, {"demand_shock_scope": "some"}])
def test_invalid_scenarios_are_rejected(bad):
    with pytest.raises(ValueError):
        ProductScenario(**{"product_id": "tacos", **bad})


def test_view_frames_and_stockout_date(ctx):
    _sc, base, scen, c = run(ctx, supply_change_pct=-100)
    f = views.demand_supply_frame(scen, "tacos")
    assert len(f) == 28 and (f["demand_lo"] <= f["demand_hi"]).all()
    assert views.stockout_date(scen, "tacos") is not None
    b = views.band_frame(scen, base, "profit")
    assert (b["scen_lo"] <= b["scen_hi"]).all()
    assert set(views.impact_frame(c)["metric"]) == {"Revenue change", "Gross profit change"}


def test_tool_uses_lab_settings_and_persists_them(session):
    out = run_tool(session, "simulate_product_scenario",
                   {"product": "burrito", "price_change_pct": 5, "supply_change_pct": None,
                    "extra_units": None, "demand_shock_pct": None, "demand_shock_scope": None,
                    "marketing_change_pct": None, "horizon_days": 14})
    assert out["scenario"]["product_id"] == "burritos" and out["horizon_days"] == 14
    assert session.state.product_scenario["price_change_pct"] == 5
    again = run_tool(session, "simulate_product_scenario", {"product": None})  # keeps lab settings
    assert again["scenario"] == out["scenario"]
    assert again["operating_profit"]["change"] == out["operating_profit"]["change"]
