"""Rolling-origin evaluation with no future data in the training slice."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .forecast import seasonal_forecast


def backtest(data: pd.DataFrame, target: str = "revenue", horizon: int = 28,
             min_train: int = 180, step: int = 28) -> dict:
    if target not in data.columns:
        raise ValueError(f"Unknown target: {target}")
    if horizon <= 0 or step <= 0:
        raise ValueError("horizon and step must be positive")
    rows = []
    for cut in range(min_train, len(data) - horizon + 1, step):
        future = seasonal_forecast(data.iloc[:cut].copy(), horizon=horizon)
        truth = data.iloc[cut:cut + horizon]
        for forecast_value, observed_value in zip(future[target], truth[target]):
            rows.append((float(observed_value), float(forecast_value)))
    if not rows:
        raise ValueError("Not enough history for backtesting; add longer data or shorter windows")
    actual, pred = np.asarray(rows).T
    return {"target": target, "n_predictions": len(rows),
            "folds": len(range(min_train, len(data) - horizon + 1, step)),
            "mae": float(np.mean(np.abs(actual - pred))),
            "rmse": float(np.sqrt(np.mean((actual - pred) ** 2))),
            "wape": float(np.sum(np.abs(actual - pred)) / max(np.sum(np.abs(actual)), 1e-9)),
            "evaluation": "rolling-origin; synthetic data if using included examples"}
