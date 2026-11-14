"""Decision engine tests (offline). Analytic checks use hand-built forecast bundles with known
point forecasts; pipeline checks use the real leaderboard on synthetic data."""
import dataclasses

import numpy as np
import pandas as pd
import pytest

from decisiondesk.decision.baseline import (
    ForecastBundle,
    TargetForecast,
    build_forecast_bundle,
    make_draws,
)
from decisiondesk.decision.engine import evaluate_actions
from decisiondesk.decision.ranking import rank, rank_weighted, sensitivity
from decisiondesk.decision.restaurant import UNITS as R_UNITS
from decisiondesk.decision.restaurant import (
    RestaurantDecisionConfig,
    analytic_expected_delta,
    restaurant_actions,
    simulate_restaurant,
)
from decisiondesk.decision.retail import UNITS as S_UNITS
from decisiondesk.decision.retail import (
    RetailDecisionConfig,
    RetailInputs,
    retail_actions,
    retail_inputs,
    simulate_retail,
)
from decisiondesk.decision.runner import run_retail
from decisiondesk.decision.types import (
    Action,
    Assumption,
    Constraint,
    Objective,
    OrganizationContext,
    StartingCash,
    Unsupported,
)
from decisiondesk.leaderboard import EvaluationConfig, build_leaderboard, forecast_selected
from decisiondesk.synthetic import make_sample

H = 28


def make_bundle(points: dict[str, float | np.ndarray], noise: float = 0.0, folds: int = 6,
                seed: int = 0) -> ForecastBundle:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=H, freq="D")
    targets = {}
    for name, value in points.items():
        point = np.full(H, float(value)) if np.isscalar(value) else np.asarray(value, float)
        targets[name] = TargetForecast(name, "test_model", dates, point,
                                       rng.normal(0, noise * point.mean(), (folds, H)))
    return ForecastBundle("test", "SYNTHETIC", pd.Timestamp("2025-12-31"), H, targets)


def fixed(cfg: RestaurantDecisionConfig) -> RestaurantDecisionConfig:
    """All uncertain assumptions pinned at their central value."""
    return dataclasses.replace(cfg, **{f.name: getattr(cfg, f.name).at("value")
                                       for f in dataclasses.fields(cfg)
                                       if isinstance(getattr(cfg, f.name), Assumption)})


RESTAURANT = make_bundle({"revenue": 2000.0, "variable_costs": 800.0, "fixed_costs": 1000.0})


def restaurant_outcomes(bundle=RESTAURANT, cfg=None, n=2000, seed=1, cash=None, constraints=None):
    cfg = cfg or RestaurantDecisionConfig()
    actions = restaurant_actions(cfg)
    draws = make_draws(bundle, n, seed)
    sim = lambda a, d: simulate_restaurant(bundle, a, d, cash)
    cons = cfg.constraints if constraints is None else constraints
    return actions, evaluate_actions(actions, sim, draws, cons, R_UNITS), sim, draws


# ---------------------------------------------------------------- calculations and baseline
def test_no_action_paired_deltas_are_exactly_zero():
    _, outs, _, _ = restaurant_outcomes(make_bundle(
        {"revenue": 2000.0, "variable_costs": 800.0, "fixed_costs": 1000.0}, noise=0.1))
    base = next(o for o in outs if o.action.is_baseline)
    for d in base.deltas.values():
        assert d.mean == d.p05 == d.p95 == 0.0


def test_zero_size_actions_reproduce_the_baseline_exactly():
    bundle = make_bundle({"revenue": 2000.0, "variable_costs": 800.0, "fixed_costs": 1000.0},
                         noise=0.1)
    draws = make_draws(bundle, 500, 3)
    base = simulate_restaurant(bundle, Action("n", "n", "no_action", is_baseline=True), draws)
    cfg = RestaurantDecisionConfig()
    zero = [
        Action("p", "p", "price", {"price_increase": 0.0}, (cfg.elasticity,)),
        Action("s", "s", "supplier", {"supplier_reduction": 0.0},
               (cfg.supplier_share, cfg.supplier_success)),
        Action("c", "c", "discretionary", {"discretionary_cut": 0.0},
               (cfg.discretionary_share, cfg.discretionary_demand_loss.at("low"))),
    ]
    for action in zero:
        out = simulate_restaurant(bundle, action, draws)
        for metric in base:
            np.testing.assert_allclose(out[metric], base[metric], err_msg=action.action_id)


def test_restaurant_simulation_matches_closed_form_expectations():
    cfg = fixed(RestaurantDecisionConfig())
    _, outs, _, _ = restaurant_outcomes(cfg=cfg, n=20000, constraints=())
    for o in outs:
        expected = analytic_expected_delta(RESTAURANT, o.action)
        got = o.deltas["profit"].mean
        if o.action.kind == "supplier":  # Bernoulli success: Monte Carlo tolerance (4 SE)
            sd = np.std(o.samples["profit"] - outs[0].samples["profit"])
            assert abs(got - expected) < 4 * sd / np.sqrt(20000) + 1e-9
        else:  # deterministic with zero residuals and fixed assumptions
            assert got == pytest.approx(expected, rel=1e-12, abs=1e-9), o.action.action_id


def test_price_increase_does_not_raise_revenue_proportionally():
    p = 0.03
    for elasticity, expected_sign in ((-0.8, 1), (-1.5, -1)):
        asm = Assumption("price_elasticity", elasticity, "ratio", "user_declared")
        a = Action("p", "p", "price", {"price_increase": p}, (asm,))
        out = simulate_restaurant(RESTAURANT, a, make_draws(RESTAURANT, 100, 0))
        change = out["revenue"].mean() / (2000 * H) - 1
        assert change == pytest.approx((1 + p) ** (1 + elasticity) - 1)
        assert np.sign(change) == expected_sign and change < p


# ---------------------------------------------------------------- constraints and ranking
def test_constraints_exclude_actions_and_report_relaxation():
    _, outs, _, _ = restaurant_outcomes()
    eight = next(o for o in outs if o.action.action_id == "raise_prices_8pct")
    assert not eight.feasible and "max price increase" in eight.violations[0]
    rec = rank(outs, Objective.MAX_EXPECTED_PROFIT, horizon_days=H, evidence={})
    assert "raise_prices_8pct" not in [r.action_id for r in rec.ranking if r.feasible]
    assert rec.recommended_action_id != "raise_prices_8pct"
    # Nothing feasible -> no recommendation, but the smallest relaxation is reported.
    tight = (Constraint("budget", "param:implementation_cost", "max", -1.0, "USD"),)
    _, outs2, _, _ = restaurant_outcomes(constraints=tight)
    rec2 = rank(outs2, Objective.MAX_EXPECTED_PROFIT, horizon_days=H, evidence={})
    assert rec2.recommended_action_id is None and rec2.strength == "unavailable"
    assert "Smallest relaxation" in rec2.rationale


def test_ranking_is_deterministic_and_input_order_invariant():
    _, outs, _, _ = restaurant_outcomes()
    r1 = rank(outs, Objective.MAX_EXPECTED_PROFIT, horizon_days=H, evidence={})
    r2 = rank(list(reversed(outs)), Objective.MAX_EXPECTED_PROFIT, horizon_days=H, evidence={})
    assert [r.action_id for r in r1.ranking] == [r.action_id for r in r2.ranking]
    _, again, _, _ = restaurant_outcomes()
    r3 = rank(again, Objective.MAX_EXPECTED_PROFIT, horizon_days=H, evidence={})
    assert [(r.action_id, r.objective_mean) for r in r1.ranking] == \
        [(r.action_id, r.objective_mean) for r in r3.ranking]


def test_exact_ties_are_broken_by_action_id():
    b = make_bundle({"revenue": 2000.0, "variable_costs": 800.0, "fixed_costs": 1000.0})
    acts = [Action("no_action", "none", "no_action", is_baseline=True),
            Action("zz_same", "z", "no_action"), Action("aa_same", "a", "no_action")]
    outs = evaluate_actions(acts, lambda a, d: simulate_restaurant(b, a, d), make_draws(b, 100),
                            (), R_UNITS)
    order = [r.action_id for r in rank(outs, Objective.MAX_EXPECTED_PROFIT, horizon_days=H,
                                       evidence={}).ranking]
    assert order == ["aa_same", "no_action", "zz_same"]


# ---------------------------------------------------------------- uncertainty and sensitivity
def test_simulations_are_reproducible_and_use_common_random_numbers():
    _, a, _, _ = restaurant_outcomes(make_bundle(
        {"revenue": 2000.0, "variable_costs": 800.0, "fixed_costs": 1000.0}, noise=0.1), seed=5)
    _, b, _, _ = restaurant_outcomes(make_bundle(
        {"revenue": 2000.0, "variable_costs": 800.0, "fixed_costs": 1000.0}, noise=0.1), seed=5)
    _, c, _, _ = restaurant_outcomes(make_bundle(
        {"revenue": 2000.0, "variable_costs": 800.0, "fixed_costs": 1000.0}, noise=0.1), seed=6)
    np.testing.assert_array_equal(a[1].samples["profit"], b[1].samples["profit"])
    assert not np.array_equal(a[1].samples["profit"], c[1].samples["profit"])
    # Streams depend on (seed, name) only, so evaluation order cannot change draws.
    d = make_draws(RESTAURANT, 300, 9)
    np.testing.assert_array_equal(d.uniform("price_elasticity"), d.uniform("price_elasticity"))
    # Paired (CRN) differences are far less noisy than unpaired ones.
    paired = a[1].samples["profit"] - a[0].samples["profit"]
    unpaired = a[1].samples["profit"] - c[0].samples["profit"]
    assert paired.std() < 0.5 * unpaired.std()


def test_sensitivity_moves_with_elasticity_and_flags_rank_changes():
    cfg = RestaurantDecisionConfig()
    profits = []
    for level in ("low", "value", "high"):
        a = Action("p", "p", "price", {"price_increase": 0.03}, (cfg.elasticity.at(level),))
        profits.append(simulate_restaurant(RESTAURANT, a, make_draws(RESTAURANT, 100))["profit"]
                       .mean())
    assert profits[0] < profits[1] < profits[2]  # less elastic demand -> more profit
    actions, outs, sim, draws = restaurant_outcomes()
    rows = sensitivity(actions, sim, draws, cfg.constraints, R_UNITS,
                       Objective.MAX_EXPECTED_PROFIT)
    assert {r["assumption"] for r in rows} >= {"price_elasticity"}
    flips = [r for r in rows if r["top_action"] != rank(
        outs, Objective.MAX_EXPECTED_PROFIT, horizon_days=H, evidence={}).recommended_action_id]
    rec = rank(outs, Objective.MAX_EXPECTED_PROFIT, horizon_days=H, evidence={},
               sensitivity=rows)
    assert (rec.strength == "assumption-sensitive") == bool(flips)


def test_small_uncertain_advantage_is_not_recommended():
    """A tiny expected gain swamped by negotiation risk must not become a recommendation."""
    cfg = RestaurantDecisionConfig()
    risky = Action("supplier_tiny", "tiny supplier deal", "supplier",
                   {"supplier_reduction": 0.001, "start_day": 0},
                   (cfg.supplier_share, cfg.supplier_success), implementation_cost=40.0)
    acts = [Action("no_action", "none", "no_action", is_baseline=True), risky]
    b = make_bundle({"revenue": 2000.0, "variable_costs": 800.0, "fixed_costs": 1000.0})
    outs = evaluate_actions(acts, lambda a, d: simulate_restaurant(b, a, d),
                            make_draws(b, 2000), (), R_UNITS)
    rec = rank(outs, Objective.MAX_EXPECTED_PROFIT, horizon_days=H, evidence={})
    assert rec.strength in ("not distinguishable", "robust") and (
        rec.recommended_action_id in (None, "no_action"))


# ---------------------------------------------------------------- objectives
def test_cash_objective_requires_user_confirmed_starting_cash():
    for cash in (None, StartingCash(5000, "Nessie balance", confirmed_by_user=False)):
        _, outs, _, _ = restaurant_outcomes(cash=cash)
        rec = rank(outs, Objective.MIN_CASH_SHORTAGE_RISK, horizon_days=H, evidence={})
        assert rec.strength == "unavailable" and rec.recommended_action_id is None
    _, outs, _, _ = restaurant_outcomes(cash=StartingCash(100.0, "user", True))
    rec = rank(outs, Objective.MIN_CASH_SHORTAGE_RISK, horizon_days=H, evidence={})
    assert rec.strength != "unavailable" and rec.ranking


INPUTS = RetailInputs(100.0, pd.Timestamp("2025-12-31"),
                      Assumption("unit_price", 50.0, "USD/unit", "user_declared"),
                      Assumption("unit_cost", 30.0, "USD/unit", "user_declared"))
RETAIL = make_bundle({"units_sold": 40.0})
RCFG = RetailDecisionConfig(lead_time_days=3, review_days=7,
                            holding_rate=Assumption("holding_cost_annual_rate", 0.365, "rate",
                                                    "user_declared"),
                            constraints=())


def retail_outcomes(cfg=RCFG, bundle=RETAIL):
    actions, _ = retail_actions(bundle, INPUTS, cfg)
    sim = lambda a, d: simulate_retail(bundle, a, d, INPUTS)
    return evaluate_actions(actions, sim, make_draws(bundle, 200), cfg.constraints, S_UNITS)


def test_retail_inventory_dynamics_match_hand_calculation():
    outs = retail_outcomes()
    base = next(o for o in outs if o.action.is_baseline)
    # No orders, 100 on hand, 40/day: sells 100 (days 0-2), loses 28*40 - 100.
    assert base.metrics["lost_units"].mean == pytest.approx(28 * 40 - 100)
    assert base.metrics["profit"].mean == pytest.approx(
        100 * 20 - (60 + 20) * 30 * 0.365 / 365)  # end-of-day stock 60, 20, 0 ...
    maintain = next(o for o in outs if o.action.action_id == "maintain_stock")
    # Hand-simulated (lead 3, review 7, 40/day, 100 on hand): orders 280 (one week expected),
    # then replaces actual weekly sales 260, 280, 260 -> 1,080 units, 4 orders.
    assert maintain.metrics["cash_outlay"].mean == pytest.approx(1080 * 30 + 4 * 75)
    assert maintain.metrics["peak_inventory"].mean == pytest.approx(280)


def test_objectives_change_the_recommendation():
    outs = retail_outcomes()
    picks = {obj: rank(outs, obj, horizon_days=H, evidence={}).recommended_action_id
             for obj in (Objective.MAX_EXPECTED_PROFIT, Objective.MIN_OPERATING_COST,
                         Objective.MIN_STOCKOUT_RISK)}
    assert picks[Objective.MIN_OPERATING_COST] == "no_order"  # ordering always costs money
    assert picks[Objective.MAX_EXPECTED_PROFIT] != "no_order"
    assert picks[Objective.MIN_STOCKOUT_RISK] == "reorder_service_level"
    assert rank(outs, Objective.MIN_CASH_SHORTAGE_RISK, horizon_days=H,
                evidence={}).strength == "unavailable"  # not modeled for inventory


def test_weighted_ranking_requires_declared_weights_and_states_normalization():
    outs = retail_outcomes()
    with pytest.raises(ValueError, match="explicitly declared"):
        rank_weighted(outs, {})
    with pytest.raises(ValueError, match="direction"):
        rank_weighted(outs, {"peak_inventory": 1.0})
    scores = rank_weighted(outs, {"profit": 0.7, "stockout_after_arrival": 0.3})
    assert all("min-max" in s["normalization"] for s in scores)
    assert [s["score"] for s in scores] == sorted((s["score"] for s in scores), reverse=True)


# ---------------------------------------------------------------- data requirements
def test_missing_inventory_fields_return_unsupported():
    history = make_sample("retail", days=600).drop(columns=["inventory_on_hand"])
    result = retail_inputs(history)
    assert isinstance(result, Unsupported) and result.missing == ["inventory_on_hand"]
    run = run_retail(history, OrganizationContext("x", "retail", "SYNTHETIC"),
                     Objective.MAX_EXPECTED_PROFIT, dataset="x")
    assert run.unsupported is not None and run.recommendation is None and run.bundle is None


def test_unknown_action_kind_raises():
    with pytest.raises(ValueError, match="unknown"):
        simulate_restaurant(RESTAURANT, Action("x", "x", "teleport"), make_draws(RESTAURANT, 100))


# ---------------------------------------------------------------- pipeline integration
CFG = EvaluationConfig(holdout_folds=3)
MODELS = ("seasonal_naive", "weekday_mean_12w", "ridge")


def test_bundle_uses_the_selected_model_and_its_out_of_sample_errors():
    history = make_sample("restaurant", days=600)
    board = build_leaderboard(history, "revenue", dataset="t", data_label="SYNTHETIC", cfg=CFG,
                              models=MODELS)
    bundle = build_forecast_bundle(history, ("revenue",), dataset="t", data_label="SYNTHETIC",
                                   cfg=CFG, boards={"revenue": board})
    tf = bundle.targets["revenue"]
    assert tf.model == board.selected
    np.testing.assert_allclose(tf.point, forecast_selected(history, "revenue", board)["revenue"])
    np.testing.assert_allclose(tf.residuals[:len(board.val_residuals)], board.val_residuals)
    # Restricting candidates changes the selected model, and the bundle follows it.
    base_only = build_forecast_bundle(history, ("revenue",), dataset="t", data_label="SYNTHETIC",
                                      cfg=CFG, models=("seasonal_naive", "weekday_mean_12w"))
    assert base_only.targets["revenue"].model in ("seasonal_naive", "weekday_mean_12w")


def test_decisions_do_not_use_data_after_the_origin():
    full = make_sample("restaurant", days=700)
    cut = 600
    poisoned = full.copy()
    poisoned.loc[cut:, ["revenue", "variable_costs"]] *= 50
    targets = ("revenue", "variable_costs", "fixed_costs")
    b1 = build_forecast_bundle(full.iloc[:cut], targets, dataset="t", data_label="SYNTHETIC",
                               cfg=CFG, models=MODELS)
    b2 = build_forecast_bundle(poisoned.iloc[:cut], targets, dataset="t", data_label="SYNTHETIC",
                               cfg=CFG, models=MODELS)
    assert b1.targets["revenue"].dates[0] == full["date"].iloc[cut - 1] + pd.Timedelta(days=1)
    _, o1, _, _ = restaurant_outcomes(bundle=b1)
    _, o2, _, _ = restaurant_outcomes(bundle=b2)
    for x, y in zip(o1, o2):
        np.testing.assert_array_equal(x.samples["profit"], y.samples["profit"])


def test_cash_risk_counts_upfront_costs_and_reports_no_difference():
    # +200/day net, no noise, $0 start: end-of-day-0 cash = day-0 net - implementation cost.
    # Supplier deal: 200 - 250 < 0 (certain shortage); price: ~231 - 150 > 0; others > 0.
    _, outs, _, _ = restaurant_outcomes(cash=StartingCash(0.0, "user", True), constraints=())
    risk = {o.action.action_id: o.metrics["cash_shortage"].mean for o in outs}
    assert risk["supplier_4pct"] == 1.0
    assert all(v == 0.0 for k, v in risk.items() if k != "supplier_4pct")
    rec = rank(outs, Objective.MIN_CASH_SHORTAGE_RISK, horizon_days=H, evidence={})
    assert rec.strength == "no difference" and rec.recommended_action_id is None


def test_strength_also_requires_beating_the_runner_up():
    """Two actions that both clearly beat no action but differ by an immaterial $1 must not be
    reported as a robust choice between them (paired draws make the $1 gap 'certain')."""
    cfg = fixed(RestaurantDecisionConfig())
    asm = (cfg.discretionary_share, cfg.discretionary_demand_loss)
    acts = [Action("no_action", "none", "no_action", is_baseline=True),
            Action("a_cut", "cut A", "discretionary", {"discretionary_cut": 0.3}, asm),
            Action("b_cut", "cut B", "discretionary", {"discretionary_cut": 0.3}, asm,
                   implementation_cost=1.0)]
    b = make_bundle({"revenue": 2000.0, "variable_costs": 800.0, "fixed_costs": 1000.0})
    outs = evaluate_actions(acts, lambda a, d: simulate_restaurant(b, a, d),
                            make_draws(b, 500), (), R_UNITS)
    rec = rank(outs, Objective.MAX_EXPECTED_PROFIT, horizon_days=H, evidence={})
    assert outs[1].prob_better["profit"] == 1.0  # clearly beats no action (+$672)
    assert rec.recommended_action_id == "a_cut" and "runner-up" in rec.rationale
    assert rec.strength == "weak" and "not clearly separated" in rec.rationale
