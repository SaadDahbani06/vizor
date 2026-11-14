import json

import numpy as np
import pandas as pd
import pytest

from decisiondesk.data import DatasetSpec, ingest_daily_csv, validate_daily_frame
from decisiondesk.risk import backtested_cash_risk, cumulative_interval_check, simulate_cash_paths
from decisiondesk.synthetic import make_sample


def test_closed_days_imputed_and_flagged(tmp_path):
    data = make_sample(days=80)
    fixed_before = data.loc[19, "fixed_costs"]
    path = tmp_path / "gaps.csv"
    data.drop(index=[20, 21]).to_csv(path, index=False)
    frame, report = ingest_daily_csv(str(path), missing_days="closed_zero",
                                     spec=DatasetSpec(currency="USD",
                                                      currency_declared_by_user=True))
    assert len(frame) == 80 and frame["is_imputed"].sum() == 2
    assert (frame.loc[20:21, ["revenue", "variable_costs", "units_sold"]] == 0).all().all()
    assert (frame.loc[20:21, "fixed_costs"] == fixed_before).all()
    assert report.missing_days == ["2024-01-21", "2024-01-22"]
    assert report.imputed_columns["fixed_costs"] == "forward_fill"
    assert len(report.sha256) == 64
    assert not any("Currency" in w for w in report.warnings)


def test_report_contains_no_raw_rows(tmp_path):
    data = make_sample(days=80)
    path = tmp_path / "ok.csv"
    data.to_csv(path, index=False)
    _, report = ingest_daily_csv(str(path))
    text = json.dumps(report.to_dict())
    assert f"{data.revenue.iloc[5]:.1f}" not in text
    assert any("Currency not declared" in w for w in report.warnings)


def test_strict_mode_still_rejects_gaps():
    with pytest.raises(ValueError, match="closed_zero"):
        validate_daily_frame(make_sample(days=80).drop(index=[10]))


def test_timezone_and_intraday_handling():
    data = make_sample(days=80)
    aware = data.assign(date=data.date.dt.tz_localize("America/New_York"))
    frame, _ = validate_daily_frame(aware, spec=DatasetSpec(timezone="America/New_York"))
    assert frame.date.dt.tz is None and frame.date.iloc[0] == pd.Timestamp("2024-01-01")
    with pytest.raises(ValueError, match="intra-day"):
        validate_daily_frame(data.assign(date=data.date + pd.Timedelta(hours=13)))


def test_bad_spec_and_mapping():
    with pytest.raises(ValueError, match="ISO-4217"):
        DatasetSpec(currency="dollars")
    data = make_sample(days=80).rename(columns={"revenue": "sales"})
    with pytest.raises(ValueError, match="duplicate"):
        validate_daily_frame(data, mapping={"sales": "revenue", "variable_costs": "revenue"})


def test_zero_error_paths_are_deterministic():
    point = np.full(28, 100.0)
    paths = simulate_cash_paths(point, np.zeros((5, 28)), starting_cash=1000, n=200)
    np.testing.assert_allclose(paths, np.tile(1000 + np.cumsum(point), (200, 1)))


def test_calibration_check_refuses_with_few_windows():
    rng = np.random.default_rng(1)
    check = cumulative_interval_check(np.zeros((6, 28)), rng.normal(size=(6, 28)),
                                      rng.normal(size=(12, 28)), n=500)
    assert check["windows"] == 6 and not check["assessable"]
    assert "NOT established" in check["verdict"]


def test_block_bootstrap_cumulative_coverage_near_nominal():
    # When future errors come from the same distribution as backtest errors, the 80% interval
    # for cumulative cash should cover roughly 80% of windows. Tests the mechanics only.
    rng = np.random.default_rng(2)
    val = rng.normal(0, 50, size=(150, 28))
    actual = rng.normal(0, 50, size=(60, 28))
    check = cumulative_interval_check(np.zeros((60, 28)), actual, val, n=1000)
    assert check["assessable"]
    assert 0.65 <= check["empirical_coverage"] <= 0.93


def test_shortfall_frequency_monotone_in_starting_cash():
    rng = np.random.default_rng(3)
    point = np.full(28, -20.0)
    val, test = rng.normal(0, 80, (12, 28)), rng.normal(0, 80, (6, 28))
    kwargs = {"holdout_residuals": test, "holdout_pred": np.zeros((6, 28)),
              "holdout_actual": test, "n": 1000}
    low = backtested_cash_risk(point, val, starting_cash=200, **kwargs)
    high = backtested_cash_risk(point, val, starting_cash=5000, **kwargs)
    assert low["simulated_shortfall_frequency"] >= high["simulated_shortfall_frequency"]
    assert 0 <= low["simulated_shortfall_frequency"] <= 1
    assert not low["holdout_calibration_check"]["assessable"]
