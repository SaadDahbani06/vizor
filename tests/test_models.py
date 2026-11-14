import json

import numpy as np
import pandas as pd
import pytest

from decisiondesk import models as model_mod
from decisiondesk.features import WARMUP_DAYS, build_origin_features, training_rows
from decisiondesk.forecast import seasonal_forecast
from decisiondesk.leaderboard import (
    EvaluationConfig,
    build_leaderboard,
    forecast_selected,
    interval_coverage,
    interval_offsets,
    plan_folds,
    run_cutoffs,
    select_model,
)
from decisiondesk.synthetic import make_sample

H = 28


def _series(days=400):
    data = make_sample(days=days)
    dates = pd.date_range(data.date.iloc[0], periods=days + H, freq="D")
    return data, data.revenue.to_numpy(float), dates


def test_features_ignore_data_at_or_after_origin():
    _, y, dates = _series()
    origin = 300
    a = build_origin_features(y, dates, np.array([origin]), H)
    poisoned = y.copy()
    poisoned[origin:] = 1e9
    b = build_origin_features(poisoned, dates, np.array([origin]), H)
    pd.testing.assert_frame_equal(a, b)


def test_training_rows_only_use_targets_before_cutoff():
    _, y, dates = _series()
    cutoff = 250
    X1, t1 = training_rows(y, dates, cutoff, H)
    poisoned = y.copy()
    poisoned[cutoff:] = -1e9
    X2, t2 = training_rows(poisoned, dates, cutoff, H)
    pd.testing.assert_frame_equal(X1, X2)
    np.testing.assert_array_equal(t1, t2)
    assert X1["t"].max() < cutoff and X1["origin"].min() >= WARMUP_DAYS


@pytest.mark.parametrize("name", ["ridge", "xgboost", "catboost"])
def test_fitted_models_do_not_see_future(name):
    ok, why = model_mod.REGISTRY[name].availability()
    if not ok:
        pytest.skip(why)
    _, y, dates = _series()
    p1, _ = run_cutoffs(name, y, dates, [280], H)
    poisoned = y.copy()
    poisoned[280:] *= 50
    p2, _ = run_cutoffs(name, poisoned, dates, [280], H)
    np.testing.assert_allclose(p1, p2)


def test_seasonal_naive_definition():
    _, y, dates = _series()
    o = 200
    feats = build_origin_features(y, dates, np.array([o]), H)
    expected = [y[o - 7 + (h - 1) % 7] for h in range(1, H + 1)]
    np.testing.assert_array_equal(feats["seasonal_naive"], expected)


def test_weekday_mean_matches_starter_baseline():
    data, y, dates = _series()
    o = 300
    feats = build_origin_features(y, dates, np.array([o]), H)
    starter = seasonal_forecast(data.iloc[:o], horizon=H, lookback_days=84)
    np.testing.assert_allclose(feats["wd_mean_12"], starter["revenue"])


def test_fold_plan_isolates_holdout():
    cfg = EvaluationConfig()
    val, test, start = plan_folds(730, cfg)
    assert max(val) + cfg.horizon <= start == min(test)
    assert max(test) + cfg.horizon == 730
    assert len(test) == cfg.holdout_folds
    with pytest.raises(ValueError, match="Need about"):
        plan_folds(300, cfg)


def test_selection_falls_back_to_baseline():
    assert select_model({"seasonal_naive": .12, "weekday_mean_12w": .10, "ridge": .11},
                        min_relative_improvement=.02)[0] == "weekday_mean_12w"
    # ML wins on paper but by less than the required margin -> keep baseline.
    choice, reason = select_model({"seasonal_naive": .12, "weekday_mean_12w": .100,
                                   "xgboost": .099}, min_relative_improvement=.02)
    assert choice == "weekday_mean_12w" and "keeping the baseline" in reason
    assert select_model({"seasonal_naive": .12, "weekday_mean_12w": .10, "ridge": .08},
                        min_relative_improvement=.02)[0] == "ridge"


def test_interval_coverage_near_nominal_on_stationary_noise():
    rng = np.random.default_rng(0)
    residuals = rng.normal(0, 1, size=(200, H))
    offsets = interval_offsets(residuals, 0.8)
    actual = rng.normal(0, 1, size=(200, H))
    cov = interval_coverage(np.zeros((200, H)), actual, offsets)
    assert 0.76 <= cov <= 0.84


def test_leaderboard_end_to_end(tmp_path, monkeypatch):
    # Pretend catboost is missing: the board must report it, not crash.
    spec = model_mod.REGISTRY["catboost"]
    monkeypatch.setitem(model_mod.REGISTRY, "catboost",
                        model_mod.ModelSpec(spec.name, spec.kind, spec.build,
                                            requires="definitely_not_installed_pkg"))
    data = make_sample(days=600)
    cfg = EvaluationConfig(holdout_folds=3)
    board = build_leaderboard(data, "revenue", dataset="t", data_label="SYNTHETIC", cfg=cfg,
                              models=("ridge", "catboost"))
    names = [m.name for m in board.models]
    assert {"seasonal_naive", "weekday_mean_12w", "ridge", "catboost"} == set(names)
    cat = next(m for m in board.models if m.name == "catboost")
    assert not cat.available and "not installed" in cat.unavailable_reason
    assert board.meta["n_holdout_folds"] == 3
    assert "SYNTHETIC" in board.meta["caveat"]
    json_path, csv_path = board.save(tmp_path)
    saved = json.loads(json_path.read_text())
    assert saved["selected"] == board.selected and csv_path.exists()

    fc = forecast_selected(data, "revenue", board)
    assert len(fc) == H and (fc["lower"] <= fc["upper"]).all()
    assert fc["date"].iloc[0] == data["date"].iloc[-1] + pd.Timedelta(days=1)


def test_net_cash_target_supported():
    data = make_sample(days=600)
    board = build_leaderboard(data, "net_cash_flow", dataset="t", data_label="SYNTHETIC",
                              cfg=EvaluationConfig(holdout_folds=3),
                              models=("seasonal_naive", "weekday_mean_12w"))
    assert board.val_residuals.shape[1] == H


@pytest.mark.parametrize("name", ["ridge", "xgboost", "catboost"])
def test_constant_anchored_target_is_handled(name):
    """A perfectly weekly target (e.g. scheduled fixed costs) leaves y - anchor constant;
    every ML model must still fit and reproduce it instead of crashing."""
    ok, why = model_mod.REGISTRY[name].availability()
    if not ok:
        pytest.skip(why)
    dates = pd.date_range("2024-01-01", periods=300 + H, freq="D")
    y = np.where(dates[:300].day_of_week == 0, 1130.0, 980.0)
    pred, actual = run_cutoffs(name, y, dates, [260], H)
    np.testing.assert_allclose(pred[0], actual[0])
