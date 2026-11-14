"""Milestone 5: product-level synthetic data and dashboard metrics reconcile with the ledger."""
import numpy as np
import pandas as pd
import pytest

from decisiondesk import dashboard as dash
from decisiondesk.agent.tools import BusinessSession, ProductLayerUnavailable, run_tool
from decisiondesk.products import (
    BOM,
    INTRADAY,
    PRODUCT_IDS,
    build_product_history,
    menu_prices,
    ration,
    restaurant_products,
    unit_costs,
)
from decisiondesk.synthetic_v2 import (
    FIXED_COMPONENTS,
    VARIABLE_COMPONENTS,
    load_business,
    restaurant_cost_components,
)

FAST = ("seasonal_naive", "weekday_mean_12w", "historical_mean_28d", "ridge")


@pytest.fixture(scope="module")
def ph():
    return restaurant_products()


@pytest.fixture(scope="module")
def rs():
    return BusinessSession.from_demo("restaurant", models=FAST, n_sims=300)


# ------------------------------------------------------------------ dataset integrity
def test_existing_daily_dataset_is_unchanged_and_reproducible(ph):
    ds = load_business("restaurant")
    assert ds.daily["revenue"].sum() == pytest.approx(load_business("restaurant").daily["revenue"].sum())
    again = build_product_history(ds)
    pd.testing.assert_frame_equal(again.sales, ph.sales)       # deterministic seed
    pd.testing.assert_frame_equal(again.ledger, ph.ledger)


def test_product_revenue_and_food_cost_reconcile_with_daily_totals(ph):
    r = ph.recon
    assert np.abs(r["tracked_revenue"] + r["other_items_revenue"] - r["revenue"]).max() < 0.011
    assert (r["other_items_revenue"] >= -0.005).all() and (r["other_food_cost"] >= -0.005).all()
    assert np.abs(r["tracked_cogs"] + r["other_food_cost"] - r["food_cost"]).max() < 0.011
    s = ph.sales
    by_day = s.groupby("date")["revenue"].sum().to_numpy()
    np.testing.assert_allclose(by_day, r["tracked_revenue"].to_numpy(), atol=0.011)
    np.testing.assert_allclose(s["revenue"], s["price"] * s["units_sold"], atol=0.006)
    open_ = r[~r["closed"]]
    share = open_["tracked_revenue"].sum() / open_["revenue"].sum()
    assert 0.80 < share < 0.90  # five products ~85% of sales; the rest is other menu items
    assert ph.metadata["mix_adjustments"] == 0


def test_cost_components_reconcile_with_variable_and_fixed_costs():
    ds = load_business("restaurant")
    c = restaurant_cost_components(ds)
    np.testing.assert_allclose(c[list(VARIABLE_COMPONENTS)].sum(1), ds.daily["variable_costs"], atol=0.006)
    np.testing.assert_allclose(c[list(FIXED_COMPONENTS)].sum(1), ds.daily["fixed_costs"], atol=0.006)
    with pytest.raises(ValueError):
        restaurant_cost_components(load_business("retail"))


def test_inventory_never_negative_and_sales_never_exceed_demand_or_stock(ph):
    led, s = ph.ledger, ph.sales
    assert (led["closing"] >= 0).all()
    np.testing.assert_allclose(led["opening"] + led["delivered"] - led["used"], led["closing"], atol=0.01)
    assert (s["units_sold"] <= s["demand"]).all() and (s["lost_units"] == s["demand"] - s["units_sold"]).all()
    # usage = BOM x units sold, so stock bounds sales
    sold = ph.wide("units_sold").set_index("date")[list(PRODUCT_IDS)].to_numpy()
    used = led.pivot(index="date", columns="ingredient_id", values="used")[
        ["tortillas", "protein", "cheese", "produce", "beverages"]].to_numpy()
    np.testing.assert_allclose(sold @ BOM, used, atol=0.01)
    assert (s.loc[s["stockout"], "lost_units"] > 0).all()
    assert 0 < s["stockout"].sum() < 0.05 * len(s)  # sell-outs happen, but rarely


def test_demand_estimate_uses_sellout_time_and_is_close_to_hidden_demand(ph):
    s = ph.sales
    so = s[s["stockout"]]
    assert (so["sellout_time"] != "").all() and (s.loc[~s["stockout"], "sellout_time"] == "").all()
    assert (s.loc[~s["stockout"], "demand_est"] == s.loc[~s["stockout"], "units_sold"]).all()
    gap_raw = (so["demand"] - so["units_sold"]).abs().mean()
    gap_est = (so["demand"] - so["demand_est"]).abs().mean()
    assert gap_est < 0.5 * gap_raw  # the estimate removes most of the censoring
    assert INTRADAY[0] == 0 and INTRADAY[-1] == pytest.approx(1) and (np.diff(INTRADAY) >= 0).all()


def test_rationing_respects_shared_ingredients():
    demand = np.array([100.0, 50.0, 20.0, 20.0, 80.0])
    need = demand @ BOM
    stock = need.copy()
    stock[1] *= 0.5  # half the protein needed
    fill, f = ration(demand, stock)
    assert f[1] == pytest.approx(0.5)
    assert np.all((demand * fill) @ BOM <= stock + 1e-9)
    assert fill[4] == 1.0 and np.all(fill[:4] <= 0.5 + 1e-9)  # drinks need no protein


def test_prices_and_costs_follow_dataset_indexes():
    p = menu_prices(np.array([1.0, 1.1087]))
    assert (np.round(p * 4) == p * 4).all() and (p[:, 1] > p[:, 0]).all()
    c = unit_costs(np.array([1.0, 1.05]))
    np.testing.assert_allclose(c[:, 1] / c[:, 0], 1.05)


# ------------------------------------------------------------------ dashboard metrics
def test_kpis_are_documented_calculations(rs):
    k = dash.kpis(rs, "mtd")
    h = rs.history
    w = dash.window(h, "mtd")
    cur = h[(h.date >= w.start) & (h.date <= w.end)]
    assert w.start.day == 1 and (w.prev_end - w.prev_start).days == (w.end - w.start).days
    assert k["revenue"]["value"] == pytest.approx(cur.revenue.sum())
    prof = (cur.revenue - cur.variable_costs - cur.fixed_costs).sum()
    assert k["net_profit"]["value"] == pytest.approx(prof)
    assert k["net_profit"]["margin_pct"] == pytest.approx(prof / cur.revenue.sum() * 100)
    assert k["expenses"]["value"] == pytest.approx(cur.variable_costs.sum() + cur.fixed_costs.sum())
    assert k["cash"]["value"] == pytest.approx(h.cash_balance.iloc[-1])
    flow = (h.cash_in - h.cash_out).tail(90).mean()
    assert (k["cash"]["runway_days"] is None) == (flow >= 0)
    assert "not a bank balance" in k["cash"]["source"]
    ctx = rs.product_context()
    assert k["inventory"]["value"] == pytest.approx(float(ctx.start_stock @ ctx.ing_cost))
    li = k["liabilities"]
    assert li["payables"] == pytest.approx(h.purchase_dollars.tail(7).sum())  # net-7 terms
    assert li["loan_balance"] is None and "unavailable" in li["note"]
    assert li["due_14d"] >= li["payables"]


def test_rolling_window_comparison_and_bad_period(rs):
    w = dash.window(rs.history, "28d")
    assert (w.end - w.start).days == 27 and (w.start - w.prev_end).days == 1
    with pytest.raises(ValueError):
        dash.window(rs.history, "fortnight")


def test_projected_bills_follow_the_ledger_schedule(rs):
    end = rs.history["date"].max()
    b = dash.project_bills(rs.bills, end, 14)
    assert set(b["category"]) >= {"payroll", "supplies_services"} or set(b["category"]) >= {"payroll", "other_operating"}
    assert (b["date"] > end).all() and (b["date"] <= end + pd.Timedelta(days=14)).all()
    assert "owner_draw" not in set(b["category"])  # equity distribution, not a liability
    rent = b[b["category"] == "rent"]
    assert rent.empty or (rent["date"].dt.day == 1).all()


def test_expense_breakdown_sums_to_total_expenses(rs):
    for period in ("mtd", "28d", "365d"):
        e = dash.expense_breakdown(rs, period)
        k = dash.kpis(rs, period)
        assert e["amount"].sum() == pytest.approx(k["expenses"]["value"], abs=0.5)
        assert e["share_pct"].sum() == pytest.approx(100)
    assert {"Labor (payroll)", "Rent", "Ingredients: five tracked products"} <= set(e["category"])


def test_product_table_and_insights_are_computed_not_hardcoded(rs):
    t = dash.product_table(rs, "28d")
    s = rs.product_context().history.sales
    w = dash.window(rs.history, "28d")
    cur = s[(s.date >= w.start) & (s.date <= w.end)]
    assert list(t["product_id"]) == list(PRODUCT_IDS)
    for _, r in t.iterrows():
        g = cur[cur.product_id == r["product_id"]]
        assert r["units_sold"] == g.units_sold.sum() and r["revenue"] == pytest.approx(g.revenue.sum())
        assert r["gross_margin_pct"] == pytest.approx((g.revenue.sum() - g.cogs.sum()) / g.revenue.sum() * 100)
        assert 0 <= r["stockout_risk_7d"] <= 1 and r["sellable_units"] >= 0
    ins = {i["kind"]: i for i in dash.insights(rs, "28d")}
    assert ins["best_seller"]["product"] == t.loc[t.revenue.idxmax(), "product"]
    assert ins["most_profitable"]["product"] == t.loc[t.gross_profit.idxmax(), "product"]
    assert ins["lowest_margin"]["product"] == t.loc[t.gross_margin_pct.idxmin(), "product"]
    assert "simulated" in ins["stockout_risk"]["basis"]


def test_profit_change_decomposition_adds_up(rs):
    pc = dash.profit_change(rs, 28)
    total = sum(d["profit_effect"] for d in pc["drivers"])
    assert total == pytest.approx(pc["profit"]["change"], abs=1.0)


def test_retail_has_no_product_layer_but_dashboard_still_works():
    r = BusinessSession.from_demo("retail", models=FAST, n_sims=300)
    with pytest.raises(ProductLayerUnavailable):
        r.product_context()
    k = dash.kpis(r, "28d")
    assert k["inventory"]["value"] is not None and k["liabilities"]["payment_terms_days"] == 30
    out = run_tool(r, "get_product_performance", {"period_days": 28})
    assert out["available"] is False and "per product" in out["what_is_needed"]
    e = dash.expense_breakdown(r, "28d")
    assert e["amount"].sum() == pytest.approx(k["expenses"]["value"], abs=0.5)
