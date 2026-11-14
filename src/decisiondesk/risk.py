"""Illustrative residual-bootstrap simulation, NOT a calibrated production risk model."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .forecast import seasonal_forecast


def simulate_cash_risk(history: pd.DataFrame, forecast: pd.DataFrame, *, starting_cash: float,
                       reserve: float = 0.0, n: int = 2000, seed: int = 42) -> dict:
    if n < 100 or not np.isfinite(starting_cash) or not np.isfinite(reserve):
        raise ValueError("n >= 100 and finite cash inputs are required")
    # In-sample residuals are only an approximate bootstrap; risk calibration is still TODO.
    tail = history.tail(min(len(history) - 28, 180)).copy()
    ref = history.iloc[:-len(tail)].copy()
    if len(ref) < 56:
        # Fall back to zero-centered historical day-of-week fluctuations.
        tail = history.copy()
        group = tail.groupby(tail.date.dt.day_of_week)
        residuals = (tail.revenue - tail.variable_costs - tail.fixed_costs
                     - group.revenue.transform("mean") + group.variable_costs.transform("mean")
                     + group.fixed_costs.transform("mean")).to_numpy()
    else:
        # Out-of-sample-ish residuals using a forecast built from earlier observations.
        preds = seasonal_forecast(ref, horizon=len(tail))
        actual_net = (tail.revenue - tail.variable_costs - tail.fixed_costs).to_numpy()
        residuals = actual_net - preds.net_cash_flow.to_numpy()
    residuals = residuals - np.mean(residuals)
    rng = np.random.default_rng(seed)
    innovations = rng.choice(residuals, size=(n, len(forecast)), replace=True)
    paths = starting_cash + np.cumsum(forecast.net_cash_flow.to_numpy()[None, :] + innovations, axis=1)
    running_min = np.minimum(starting_cash, paths.min(axis=1))
    ending = paths[:, -1]
    return {"cash_shortage_probability": float(np.mean(running_min < 0)),
            "reserve_breach_probability": float(np.mean(running_min < reserve)),
            "expected_ending_cash": float(ending.mean()),
            "fifth_percentile_ending_cash": float(np.quantile(ending, .05)),
            "notes": "Illustrative independent residual bootstrap; not validated/calibrated."}


# ---------------------------------------------------------------------------------------------
# Backtest-driven cash risk (Milestone 1). Replaces the i.i.d. in-sample bootstrap above for
# anything shown as a headline number.
# ---------------------------------------------------------------------------------------------
MIN_CALIBRATION_WINDOWS = 20


def _block_bootstrap(residuals: np.ndarray, n: int, rng: np.random.Generator,
                     block: int | None = None) -> np.ndarray:
    """Sample error paths from out-of-sample backtest windows.

    ``block=None`` (default, Milestone 5.1): each path is ONE whole historical window, which keeps
    the correlation of errors across weeks (multi-week totals and the running cash minimum depend
    on it). ``block=7`` is the Milestone 5 method (each forecast week from a random window), kept
    for comparisons. Errors are NOT demeaned, so any systematic historical bias of the model is
    carried into the simulation.
    """
    folds, horizon = residuals.shape
    if block is None or block >= horizon:
        return residuals[rng.integers(0, folds, size=n)].copy()
    out = np.empty((n, horizon))
    for start in range(0, horizon, block):
        stop = min(start + block, horizon)
        pick = rng.integers(0, folds, size=n)
        out[:, start:stop] = residuals[pick, start:stop]
    return out


def simulate_cash_paths(point_net_cash: np.ndarray, residuals: np.ndarray, *,
                        starting_cash: float, n: int = 4000, seed: int = 42,
                        block: int | None = None) -> np.ndarray:
    if n < 100 or not np.isfinite(starting_cash):
        raise ValueError("n >= 100 and finite starting cash are required")
    if residuals.ndim != 2 or residuals.shape[1] != len(point_net_cash):
        raise ValueError("residuals must be folds x horizon, matching the forecast length")
    rng = np.random.default_rng(seed)
    errors = _block_bootstrap(residuals, n, rng, block)
    return starting_cash + np.cumsum(point_net_cash[None, :] + errors, axis=1)


def cumulative_interval_check(holdout_pred: np.ndarray, holdout_actual: np.ndarray,
                              val_residuals: np.ndarray, *, level: float = 0.8,
                              n: int = 4000, seed: int = 0) -> dict:
    """Does the simulated interval for cumulative net cash at the horizon cover the realized
    cumulative net cash in each untouched holdout window? Simulations use validation errors only.
    """
    alpha = 1 - level
    hits = []
    for i, (pred, actual) in enumerate(zip(holdout_pred, holdout_actual)):
        paths = simulate_cash_paths(pred, val_residuals, starting_cash=0.0, n=n, seed=seed + i)
        lo, hi = np.quantile(paths[:, -1], [alpha / 2, 1 - alpha / 2])
        hits.append(bool(lo <= actual.sum() <= hi))
    k = len(hits)
    return {"nominal": level, "windows": k, "covered": int(sum(hits)),
            "empirical_coverage": sum(hits) / k if k else None,
            "assessable": k >= MIN_CALIBRATION_WINDOWS,
            "verdict": ("calibration assessable" if k >= MIN_CALIBRATION_WINDOWS else
                        f"only {k} independent holdout windows (< {MIN_CALIBRATION_WINDOWS}); "
                        "calibration of cumulative cash risk is NOT established")}


def backtested_cash_risk(point_net_cash: np.ndarray, val_residuals: np.ndarray,
                         holdout_residuals: np.ndarray, holdout_pred: np.ndarray,
                         holdout_actual: np.ndarray, *, starting_cash: float,
                         reserve: float = 0.0, level: float = 0.8, n: int = 4000,
                         seed: int = 42) -> dict:
    """Cash-shortfall estimate from out-of-sample errors of the selected net-cash forecaster."""
    residuals = np.vstack([val_residuals, holdout_residuals])
    paths = simulate_cash_paths(point_net_cash, residuals, starting_cash=starting_cash,
                                n=n, seed=seed)
    running_min = np.minimum(starting_cash, paths.min(axis=1))
    ending = paths[:, -1]
    alpha = 1 - level
    check = cumulative_interval_check(holdout_pred, holdout_actual, val_residuals, level=level)
    return {
        "horizon_days": len(point_net_cash),
        "simulated_shortfall_frequency": float(np.mean(running_min < 0)),
        "simulated_reserve_breach_frequency": float(np.mean(running_min < reserve)),
        "median_ending_cash": float(np.median(ending)),
        f"ending_cash_{level:.0%}_interval": [float(np.quantile(ending, alpha / 2)),
                                               float(np.quantile(ending, 1 - alpha / 2))],
        "error_paths_from_folds": int(residuals.shape[0]),
        "mean_historical_error_per_day": float(residuals.mean()),
        "holdout_calibration_check": check,
        "label": ("Frequency across simulations that each replay one whole historical 28-day "
                  "forecast-error window. It is a calibrated probability only if the holdout check "
                  "is assessable and passes."),
    }
