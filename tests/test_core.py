import numpy as np
import pandas as pd
import pytest

from decisiondesk.backtest import backtest
from decisiondesk.data import load_daily_data
from decisiondesk.decisions import compare_actions, operational_inventory_signal
from decisiondesk.forecast import seasonal_forecast
from decisiondesk.risk import simulate_cash_risk
from decisiondesk.synthetic import make_sample


def test_synthetic_reproducible():
    pd.testing.assert_frame_equal(make_sample(seed=1), make_sample(seed=1))


def test_forecast_uses_only_prior_data():
    a = make_sample(days=365)
    earlier = a.iloc[:300]
    p1 = seasonal_forecast(earlier, horizon=28)
    b = a.copy()
    b.loc[300:, "revenue"] += 1000000
    p2 = seasonal_forecast(b.iloc[:300], horizon=28)
    pd.testing.assert_frame_equal(p1, p2)


def test_risk_reproducible_and_valid_probability():
    data = make_sample(days=365)
    forecast = seasonal_forecast(data)
    r1 = simulate_cash_risk(data, forecast, starting_cash=10000, seed=7, n=300)
    r2 = simulate_cash_risk(data, forecast, starting_cash=10000, seed=7, n=300)
    assert r1 == r2
    assert 0 <= r1["cash_shortage_probability"] <= 1


def test_price_scenario_zero_is_baseline():
    data = make_sample(days=365)
    fc = seasonal_forecast(data)
    result = compare_actions(fc, price_increase=0, supplier_discount=0)
    assert np.allclose(result.projected_net_cash, result.projected_net_cash.iloc[0])


def test_backtest_outputs_metrics():
    scores = backtest(make_sample(days=365))
    assert scores["n_predictions"] > 0
    assert scores["mae"] >= 0
    assert scores["rmse"] >= 0


def test_operational_signal_available():
    signal = operational_inventory_signal(make_sample(days=100))
    assert signal["supported"]


def test_csv_validates_gaps(tmp_path):
    data = make_sample(days=80).drop(index=[20])
    path = tmp_path / "bad.csv"
    data.to_csv(path, index=False)
    with pytest.raises(ValueError, match="continuous"):
        load_daily_data(str(path))


def test_custom_mapping(tmp_path):
    data = make_sample(days=80).rename(columns={"revenue": "sales"})
    path = tmp_path / "mapped.csv"
    data.to_csv(path, index=False)
    parsed = load_daily_data(str(path), mapping={"sales": "revenue"})
    assert "revenue" in parsed
