"""Milestone 5.1: Monte Carlo uncertainty calibration (whole-window error sampling)."""
import numpy as np
import pytest

from decisiondesk.agent.tools import BusinessSession, run_tool
from decisiondesk.calibration import (
    METHODS,
    board_validation,
    cash_validation,
    chronological_windows,
    evaluate,
    lab_validation,
    past_pool,
    sample_errors,
    validation_for,
    wilson,
    window_forecasts,
)
from decisiondesk.decision.baseline import WEEKLY_BLOCK, make_draws
from decisiondesk.decision.product_scenario import ProductScenario, baseline, compare, simulate
from decisiondesk.products import PRODUCT_IDS
from decisiondesk.risk import simulate_cash_paths

FAST = ("seasonal_naive", "weekday_mean_12w", "historical_mean_28d", "ridge")


def offset_windows(n_windows: int, rng, level_sd=40.0, noise_sd=20.0, H=28) -> np.ndarray:
    """Errors = a level offset for the whole window + daily noise (the structure we measured)."""
    return rng.normal(0, level_sd, (n_windows, 1)) + rng.normal(0, noise_sd, (n_windows, H))


@pytest.fixture(scope="module")
def rs():
    return BusinessSession.from_demo("restaurant", models=FAST, n_sims=400)


# ------------------------------------------------------------------ the bug and the fix
def test_weekly_blocks_understate_and_whole_windows_preserve_multiweek_spread():
    rng = np.random.default_rng(0)
    pool = offset_windows(400, rng)
    true_sd = pool.sum(1).std()
    weekly = sample_errors(pool, 20000, rng, 7).sum(1).std()
    whole = sample_errors(pool, 20000, rng, None).sum(1).std()
    assert weekly < 0.75 * true_sd              # the Milestone 5 bug: totals far too narrow
    assert abs(whole / true_sd - 1) < 0.05      # whole windows reproduce the real spread


def test_whole_window_totals_are_calibrated_and_weekly_are_not_on_offset_data():
    rng = np.random.default_rng(1)
    errs = offset_windows(160, rng)
    r = evaluate(errs, methods={"weekly": METHODS["weekly_blocks"], "whole": METHODS["whole_window"]},
                 horizons=(28,), min_past=40, n=2000)
    weekly, whole = r["methods"]["weekly"][28], r["methods"]["whole"][28]
    assert 0.70 <= whole["total_coverage"] <= 0.90
    assert weekly["total_coverage"] < whole["total_coverage"] - 0.10
    assert abs(weekly["daily_coverage"] - whole["daily_coverage"]) < 0.03  # daily ranges unaffected


def test_evaluation_never_uses_the_window_being_judged_or_later_ones():
    for k in range(8, 31):
        assert all(i < k for i in past_pool(k, None))
        assert all(i < 25 for i in past_pool(k, None, first_test=25))  # final test: validation only
        assert past_pool(k, 12) == list(range(max(0, k - 12), k))
    rng = np.random.default_rng(2)
    errs = offset_windows(30, rng)
    before = evaluate(errs[:20], horizons=(28,), methods={"w": METHODS["whole_window"]}, n=500)
    poisoned = errs.copy()
    poisoned[20:] *= 1000                       # later windows must not change earlier verdicts
    after = evaluate(poisoned[:20], horizons=(28,), methods={"w": METHODS["whole_window"]}, n=500)
    assert before["methods"]["w"][28]["total_hits"] == after["methods"]["w"][28]["total_hits"]


def test_wilson_interval_reflects_small_samples():
    lo, hi = wilson(18, 23)
    assert lo < 0.78 < hi and hi - lo > 0.25


# ------------------------------------------------------------------ simulator: horizons and CRN
def test_each_path_replays_one_whole_window(rs):
    bundle = rs.bundle(("revenue", "variable_costs", "fixed_costs"))
    draws = make_draws(bundle, 300, seed=5)
    assert draws.block is None and draws.fold_idx.shape == (300, 1)
    sims = bundle.simulate(draws)
    for name, tf in bundle.targets.items():
        expect = tf.point + tf.residuals[draws.fold_idx[:, 0]]
        if name in ("revenue", "variable_costs", "fixed_costs"):
            expect = np.maximum(expect, 0)
        np.testing.assert_allclose(sims[name], expect)
    weekly = make_draws(bundle, 300, seed=5, block=WEEKLY_BLOCK)  # old method still available
    assert weekly.fold_idx.shape == (300, 4) and bundle.simulate(weekly)["revenue"].shape == (300, 28)


def test_products_and_costs_share_the_window_of_each_path(rs):
    ctx = rs.product_context()
    D = ctx.base_demand()
    idx = ctx.draws.fold_idx[:, 0]
    for i, p in enumerate(PRODUCT_IDS):
        tf = ctx.bundle.targets[f"demand_{p}"]
        np.testing.assert_allclose(D[:, :, i], tf.point + tf.residuals[idx])  # clipped later, in simulate()
    tf = ctx.agg.targets["fixed_costs"]
    np.testing.assert_allclose(ctx.base_fixed(), np.maximum(tf.point + tf.residuals[idx], 0))


def test_short_horizons_are_the_first_days_of_the_same_windows(rs):
    ctx = rs.product_context()
    b7, b28 = baseline(ctx, 7), baseline(ctx, 28)
    np.testing.assert_allclose(b7.demand, b28.demand[:, :7])
    for h in (7, 14, 28):
        v = lab_validation(ctx, h)
        assert v["revenue_total"]["horizon_days"] == h and v["revenue_total"]["windows"] > 0


def test_common_random_numbers_survive_the_new_sampler(rs):
    ctx = rs.product_context()
    sc = ProductScenario("tacos", price_change_pct=0.0)
    np.testing.assert_array_equal(simulate(ctx, sc).profit, baseline(ctx, 28).profit)
    c = compare(ctx, simulate(ctx, ProductScenario("tacos", price_change_pct=5)), baseline(ctx, 28))
    assert c["range_validation"]["profit_total"]["method"] == "whole_window"
    assert "cannot be validated" in c["range_validation"]["note"]


def test_cash_paths_default_to_whole_windows():
    res = np.vstack([np.full(28, -10.0), np.full(28, 10.0)])
    paths = simulate_cash_paths(np.zeros(28), res, starting_cash=0.0, n=500, seed=1)
    finals = set(np.round(paths[:, -1], 6))
    assert finals == {-280.0, 280.0}  # whole windows: never a mix of the two


# ------------------------------------------------------------------ real boards (regression guard)
def test_restaurant_revenue_total_ranges_no_longer_too_narrow(rs):
    errs, first_test = chronological_windows(rs.board("revenue"))
    assert errs.shape[0] == first_test + 6
    r = evaluate(errs, horizons=(28,), methods={"old": METHODS["weekly_blocks"], "new": METHODS["whole_window"]}, n=2000)
    old, new = r["methods"]["old"][28], r["methods"]["new"][28]
    assert new["total_coverage"] >= 0.75 and new["total_coverage"] > old["total_coverage"]
    assert new["mean_total_width"] > old["mean_total_width"]


def test_validation_forecasts_reproduce_stored_errors_and_cash_check_runs(rs):
    b = rs.board("net_cash_flow")
    preds = window_forecasts(rs.history, "net_cash_flow", b)
    errs, _ = chronological_windows(b)
    assert preds.shape == errs.shape
    cv = cash_validation(rs.history, b)
    assert cv["windows"] == errs.shape[0] - 8 and cv["shortfall_probability_validated"] is False
    assert 0 <= cv["running_min_coverage"] <= 1


def test_tools_report_the_check_that_matches_the_displayed_range(rs):
    fc = run_tool(rs, "forecast_business", {"target": "revenue"})
    v = fc["interval_80_total_validation"]
    assert v["method"] == "whole_window" and v["horizon_days"] == 28
    assert v == {k: (round(x, 3) if isinstance(x, float) else x) for k, x in board_validation(rs.board("revenue")).as_dict().items()}
    assert "not the 28-day total range" in fc["final_test"]["interval_80_coverage_meaning"]
    cash = run_tool(rs, "assess_cash_risk", {"starting_cash": 2000})
    assert "NOT a validated probability" in cash["warning"]
    assert cash["range_validation"]["shortfall_probability_validated"] is False
    sim = run_tool(rs, "simulate_product_scenario", {"product": "tacos", "price_change_pct": 5})
    assert "inside their 80% range" in sim["range_check"]["revenue_total"]


def test_validation_text_flags_small_samples():
    rng = np.random.default_rng(3)
    v = validation_for(offset_windows(14, rng), 28)
    assert v.windows == 6 and not v.assessable and "NOT established" in v.text()
