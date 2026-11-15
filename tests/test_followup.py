"""Decision follow-up: frozen predictions, checks against later records, the labelled demo replay,
and the price-sensitivity evidence (reported, never applied)."""
import numpy as np
import pandas as pd
import pytest

from decisiondesk.agent.agent import DecisionAgent, OfflinePlanner
from decisiondesk.agent.grounding import ungrounded_numbers
from decisiondesk.agent.tools import BusinessSession, run_tool
from decisiondesk.decision.followup import (
    MIN_DAYS,
    DecisionStore,
    FollowupActuals,
    evaluate,
    make_receipt,
    price_evidence,
)
from decisiondesk.decision.product_scenario import ProductScenario, baseline, compare, simulate
from decisiondesk.decision.replay import REPLAY_LABEL, TRUE_ELASTICITY, demo_replay
from decisiondesk.decision.types import Assumption
from decisiondesk.products import PRODUCT_IDS, restaurant_products
from decisiondesk.synthetic_v2 import load_business

FAST = ("seasonal_naive", "weekday_mean_12w", "historical_mean_28d", "ridge")


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    return BusinessSession.from_demo("restaurant", models=FAST, n_sims=400,
                                     decisions_dir=str(tmp_path_factory.mktemp("decisions")))


@pytest.fixture(scope="module")
def ctx(session):
    return session.product_context()


def approve(ctx, session, **kw):
    sc = ProductScenario(**{"product_id": "tacos", "price_change_pct": 5.0, **kw})
    scen, base = simulate(ctx, sc), baseline(ctx, sc.horizon_days, sc.product_id)
    r, paths = make_receipt(ctx, sc, scen, base, business="Lone Star Taqueria", dataset=session.dataset,
                            action=sc.describe())
    return r, paths, compare(ctx, scen, base)


def fake_actuals(r, paths, *, days, scale=1.0, closed_day=None):
    """Actual records equal to the expected 'with change' path (times ``scale``)."""
    dates = pd.date_range(r["forecast_start"], periods=days, freq="D")
    orders = paths["orders_with_change"].mean(0)[:days] * scale
    contrib = paths["contribution_with_change"].mean(0)[:days] * scale
    closed = np.zeros(days, dtype=bool)
    if closed_day is not None:
        closed[closed_day] = True
        orders[closed_day] = contrib[closed_day] = 0.0
    prod = pd.DataFrame({"date": dates, "product_id": r["product_id"], "units_sold": orders, "demand_est": orders})
    daily = pd.DataFrame({"date": dates, "revenue": contrib, "variable_costs": 0.0, "contribution": contrib,
                          "closed": closed})
    return FollowupActuals(prod, daily, "test records", "TEST")


# ------------------------------------------------------------------ receipts
def test_receipt_freezes_the_decision_lab_prediction(ctx, session, tmp_path):
    r, paths, cmp_ = approve(ctx, session)
    assert r["predicted"]["profit_change"] == pytest.approx(cmp_["profit"]["change"])
    assert r["predicted"]["profit_change_90pct_range"] == pytest.approx(cmp_["profit"]["change_90pct_range"])
    assert r["executed_automatically"] is False and r["change_made_on"] is None
    assert r["price_sensitivity_used"] == {"value": -1.1, "low": -1.8, "high": -0.6,
                                           "source": "default_needs_confirmation"}
    store = DecisionStore(tmp_path)
    store.add(r, paths)
    back = store.paths(r["id"])
    for k, v in paths.items():
        np.testing.assert_allclose(back[k], v, rtol=1e-5, atol=0.01)  # stored as float32
    assert store.get(r["id"])["action"] == "Tacos price +5%"
    assert store.update(r["id"], change_made_on=r["forecast_start"])["change_made_on"] == r["forecast_start"]


def test_status_moves_from_waiting_to_checked(ctx, session):
    r, paths, _ = approve(ctx, session)
    assert evaluate(r, paths, None)["status"] == "waiting_for_change"
    r["change_made_on"] = r["forecast_start"]
    assert evaluate(r, paths, None)["status"] == "tracking"
    assert evaluate(r, paths, fake_actuals(r, paths, days=MIN_DAYS - 2))["status"] == "too_early"
    part = evaluate(r, paths, fake_actuals(r, paths, days=14))
    assert part["status"] == "partial" and part["days_observed"] == 14
    full = evaluate(r, paths, fake_actuals(r, paths, days=28))
    assert full["status"] == "complete"
    assert full["orders"]["verdict"] == "as_predicted"
    assert full["profit_before_fixed_costs"]["verdict"] == "as_predicted"
    late = dict(r, change_made_on=str((pd.Timestamp(r["forecast_end"]) - pd.Timedelta(days=2)).date()))
    assert evaluate(late, paths, fake_actuals(r, paths, days=28))["status"] == "changed_too_late"


def test_results_far_from_the_prediction_are_flagged(ctx, session):
    r, paths, _ = approve(ctx, session)
    r["change_made_on"] = r["forecast_start"]
    low = evaluate(r, paths, fake_actuals(r, paths, days=28, scale=0.6))
    assert low["orders"]["verdict"] == "below" and "cannot tell which" in low["summary"]
    high = evaluate(r, paths, fake_actuals(r, paths, days=28, scale=1.5))
    assert high["orders"]["verdict"] == "above"


def test_a_late_change_is_compared_only_from_the_day_it_was_made(ctx, session):
    r, paths, _ = approve(ctx, session)
    r["change_made_on"] = str((pd.Timestamp(r["forecast_start"]) + pd.Timedelta(days=7)).date())
    ev = evaluate(r, paths, fake_actuals(r, paths, days=28))
    assert ev["window"][0] == r["change_made_on"] and ev["days_observed"] == 21
    assert ev["orders"]["actual"] == pytest.approx(paths["orders_with_change"].mean(0)[7:].sum())


def test_closed_days_are_left_out_of_the_comparison(ctx, session):
    r, paths, _ = approve(ctx, session)
    r["change_made_on"] = r["forecast_start"]
    ev = evaluate(r, paths, fake_actuals(r, paths, days=28, closed_day=12))
    day12 = str((pd.Timestamp(r["forecast_start"]) + pd.Timedelta(days=12)).date())
    assert ev["closed_days_left_out"] == [day12] and day12 in ev["summary"]
    expected = np.delete(paths["orders_with_change"], 12, axis=1).sum(1)
    assert ev["orders"]["with_change"]["expected"] == pytest.approx(expected.mean())
    assert len(ev["daily"]["dates"]) == 27


# ------------------------------------------------------------------ demo replay
def test_replay_is_labelled_deterministic_and_applies_the_change(ctx, session):
    r, _paths, _ = approve(ctx, session, price_change_pct=10.0)
    hist, ds = restaurant_products(), load_business("restaurant")
    a = demo_replay(hist, ds, r)
    b = demo_replay(hist, ds, r)
    pd.testing.assert_frame_equal(a.products, b.products)
    assert a.label == REPLAY_LABEL and a.origin == "SYNTHETIC_REPLAY" and a.meta["data_origin"] == "SYNTHETIC"
    assert a.products["date"].min() == pd.Timestamp(r["forecast_start"])
    assert a.daily["closed"].sum() == 1  # Thanksgiving 2026-11-26 falls in the window
    none = demo_replay(hist, ds, r, true_elasticity=0.0)
    tacos = lambda x: x.products[x.products.product_id == "tacos"]["units_sold"].sum()
    burritos = lambda x: x.products[x.products.product_id == "burritos"]["units_sold"].sum()
    assert tacos(a) < tacos(none) and burritos(a) >= burritos(none)
    new_price = a.products[(a.products.product_id == "tacos")]["price"].unique()
    assert list(new_price) == [r["new_price"]]


def test_replay_never_uses_the_forecast_or_the_simulation(ctx, session, monkeypatch):
    r, _paths, _ = approve(ctx, session)
    import decisiondesk.decision.product_scenario as ps
    import decisiondesk.leaderboard as lb

    def boom(*a, **k):
        raise AssertionError("the replay must not call Vizor's forecasting or simulation")
    for mod, name in ((ps, "simulate"), (ps, "build_forecast_bundle"), (lb, "build_leaderboard_cached"),
                      (lb, "build_leaderboard")):
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, boom)
    out = demo_replay(restaurant_products(), load_business("restaurant"), r)
    assert len(out.daily) == 28


def test_replay_end_to_end_and_the_hidden_value_is_not_used(ctx, session):
    r, paths, _ = approve(ctx, session)
    r["change_made_on"] = r["forecast_start"]
    act = demo_replay(restaurant_products(), load_business("restaurant"), r)
    ev = evaluate(r, paths, act)
    assert ev["status"] == "complete" and ev["actuals_label"] == REPLAY_LABEL
    assert ev["closed_days_left_out"] == ["2026-11-26"]
    assert "hidden" not in str({k: v for k, v in ev.items() if k != "summary"}).lower()
    assert str(TRUE_ELASTICITY["tacos"]) not in ev["summary"]
    assert ev["price_evidence"]["applied_to_simulations"] is False


def test_replay_refuses_other_businesses(ctx, session):
    r, _p, _ = approve(ctx, session)
    with pytest.raises(ValueError, match="demo restaurant"):
        demo_replay(restaurant_products(), load_business("retail"), r)


# ------------------------------------------------------------------ price evidence
PRIOR = Assumption("price_elasticity_tacos", -1.1, "ratio", "default_needs_confirmation", -1.8, -0.6, "triangular")


def test_price_evidence_finds_a_clear_effect_when_noise_is_small():
    rng = np.random.default_rng(0)
    no_change = 1500 * np.exp(rng.normal(0, 0.005, 2000))
    actual = 1500 * 1.20 ** -1.5
    ev = price_evidence(actual, no_change, 20, PRIOR, n_days=28)
    assert ev["available"] and ev["strength"] == "clear"
    assert ev["after_test"]["low"] <= -1.5 <= ev["after_test"]["high"]
    assert abs(ev["after_test"]["middle"] - -1.5) < 0.1
    assert ev["applied_to_simulations"] is False


def test_price_evidence_admits_when_one_test_cannot_tell():
    rng = np.random.default_rng(0)
    no_change = 1500 * np.exp(rng.normal(0, 0.09, 2000))   # about 9% normal variation, as measured
    ev = price_evidence(1500 * 1.05 ** -1.5, no_change, 5, PRIOR, n_days=28)
    assert ev["strength"] == "very little" and "cannot pin it down" in ev["text"]
    assert ev["normal_variation_pct"] > abs(ev["expected_effect_pct"])


def test_price_evidence_is_not_offered_for_non_price_decisions(ctx, session):
    r, paths, _ = approve(ctx, session, price_change_pct=0.0, supply_change_pct=25.0)
    r["change_made_on"] = r["forecast_start"]
    assert "price_evidence" not in evaluate(r, paths, fake_actuals(r, paths, days=28))


def test_learned_ranges_are_not_applied_to_the_engine(ctx):
    for pid in PRODUCT_IDS:  # every simulation still uses the shared assumption
        assert ctx.elasticity_for(pid).source == "default_needs_confirmation"


# ------------------------------------------------------------------ Vizor AI
def test_followups_reach_vizor_ai_with_grounded_numbers(ctx, session):
    store = session.decision_store()
    r, paths, _ = approve(ctx, session)
    store.add(r, paths)
    store.update(r["id"], change_made_on=r["forecast_start"])
    store.save_actuals(r["id"], demo_replay(restaurant_products(), load_business("restaurant"),
                                            store.get(r["id"])))
    out = run_tool(session, "get_decision_followups", {})
    d = next(x for x in out["decisions"] if x["action"] == "Tacos price +5%" and x["status"] == "complete")
    assert d["records_used"] == REPLAY_LABEL and d["orders_verdict"] in ("as_predicted", "above", "below")
    assert OfflinePlanner(session).fast("Did the taco price change work?") is None  # not "today's taco price"
    reply = DecisionAgent(session, mode="offline").ask("Did the taco price change work?")
    assert [c["name"] for c in reply.tool_calls] == ["get_decision_followups"]
    assert OfflinePlanner(session).fast("What's the price of tacos?") is not None
    assert "Demo replay · synthetic" in reply.text
    assert ungrounded_numbers(reply.text, [c["output"] for c in reply.tool_calls], []) == []
