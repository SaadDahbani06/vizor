"""Simple reproducible weekly seasonal baseline; advanced ML is a future milestone."""
from __future__ import annotations

import pandas as pd

TARGETS = ("revenue", "variable_costs", "fixed_costs", "units_sold")


def seasonal_forecast(history: pd.DataFrame, horizon: int = 90, lookback_days: int = 84) -> pd.DataFrame:
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if len(history) < 56:
        raise ValueError("Need at least 56 historical days")
    history = history.sort_values("date").copy()
    recent = history.tail(lookback_days).copy()
    recent["weekday"] = recent["date"].dt.day_of_week
    future_dates = pd.date_range(history["date"].max() + pd.Timedelta(days=1), periods=horizon)
    result = pd.DataFrame({"date": future_dates})
    result["weekday"] = result["date"].dt.day_of_week
    for target in TARGETS:
        if target not in history.columns:
            continue
        # Training-only weekday averages; prevents information from future dates.
        means = recent.groupby("weekday")[target].mean()
        result[target] = result["weekday"].map(means).astype(float)
    result["net_cash_flow"] = result["revenue"] - result["variable_costs"] - result["fixed_costs"]
    return result.drop(columns=["weekday"])
