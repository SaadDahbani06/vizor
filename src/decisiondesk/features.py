"""Origin-anchored, leakage-safe features for direct multi-horizon forecasting.

A forecast is issued at *origin* index ``o`` (the first unseen day) for target days
``t = o + h - 1`` with ``h = 1..H``. Every feature for the pair (o, h) is computed from
``y[:o]`` only, plus calendar facts about ``t`` that are genuinely known in advance.
That makes leakage auditable: perturbing ``y[o:]`` cannot change any feature at origin ``o``
(see ``tests/test_models.py``).

Same-day covariates such as ``units_sold`` are deliberately NOT used to predict revenue:
in the synthetic data revenue == units * price, so that would be pure leakage.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WARMUP_DAYS = 84  # 12 weeks needed for the 12-week same-weekday mean
FEATURE_COLUMNS = (
    "h", "seasonal_naive", "wd_mean_4", "wd_mean_12", "roll_mean_7", "roll_mean_28",
    "roll_std_28", "trend_7_28", "yoy_same_weekday", "yoy_available",
    "doy_sin", "doy_cos", *(f"dow_{d}" for d in range(7)),
)


def _windowed(y: np.ndarray, ends: np.ndarray, width: int, fn) -> np.ndarray:
    """fn over y[end-width:end] for each end (exclusive), vectorized via a sliding view."""
    view = np.lib.stride_tricks.sliding_window_view(y, width)  # view[i] = y[i:i+width]
    return fn(view[ends - width], axis=1)


def build_origin_features(y: np.ndarray, dates: pd.DatetimeIndex, origins: np.ndarray,
                          horizon: int, exog: pd.DataFrame | None = None) -> pd.DataFrame:
    """Feature rows for every (origin, h) pair; columns include `origin`, `t` and FEATURE_COLUMNS.

    Requires each origin >= WARMUP_DAYS. ``dates`` must cover every target index t used
    (calendar features only); target values y[t] are never read here.
    ``exog`` (optional) is indexed by origin and holds information known at that origin, e.g.
    point-in-time macro features from ``decisiondesk.macro``; its columns must start with ``x_``.
    """
    y = np.asarray(y, dtype=float)
    origins = np.asarray(origins, dtype=int)
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if origins.size and origins.min() < WARMUP_DAYS:
        raise ValueError(f"origins must be >= {WARMUP_DAYS} (feature warm-up)")
    if origins.size and origins.max() > len(y):
        raise ValueError("origin beyond observed history")

    o = np.repeat(origins, horizon)
    h = np.tile(np.arange(1, horizon + 1), origins.size)
    t = o + h - 1
    # Most recent observed day with the same weekday as t, strictly before the origin.
    base = t - 7 * np.ceil(h / 7).astype(int)
    same_wd = np.stack([y[base - 7 * j] for j in range(12)], axis=1)
    lag364 = t - 364
    yoy_ok = lag364 >= 0  # always < o because h <= 364 is enforced below
    if horizon > 364:
        raise ValueError("horizon must be <= 364 for the year-over-year feature to stay causal")

    roll7 = _windowed(y, o, 7, np.mean)
    roll28 = _windowed(y, o, 28, np.mean)
    std28 = _windowed(y, o, 28, np.std)
    target_dates = dates[t]
    doy = target_dates.day_of_year.to_numpy()
    dow = target_dates.day_of_week.to_numpy()
    wd12 = same_wd.mean(axis=1)

    frame = pd.DataFrame({
        "origin": o, "t": t, "h": h,
        "seasonal_naive": same_wd[:, 0],
        "wd_mean_4": same_wd[:, :4].mean(axis=1),
        "wd_mean_12": wd12,
        "roll_mean_7": roll7, "roll_mean_28": roll28, "roll_std_28": std28,
        "trend_7_28": np.divide(roll7, roll28, out=np.ones_like(roll7), where=roll28 != 0),
        "yoy_same_weekday": np.where(yoy_ok, y[np.where(yoy_ok, lag364, 0)], wd12),
        "yoy_available": yoy_ok.astype(float),
        "doy_sin": np.sin(2 * np.pi * doy / 365.25),
        "doy_cos": np.cos(2 * np.pi * doy / 365.25),
    })
    for d in range(7):
        frame[f"dow_{d}"] = (dow == d).astype(float)
    if exog is not None:
        if not all(str(c).startswith("x_") for c in exog.columns):
            raise ValueError("exogenous feature columns must start with 'x_'")
        frame = frame.join(exog, on="origin")
        if frame[list(exog.columns)].isna().any().any():
            raise ValueError("exogenous features missing for some requested origins")
    return frame


def training_rows(y: np.ndarray, dates: pd.DatetimeIndex, cutoff: int, horizon: int,
                  exog: pd.DataFrame | None = None) -> tuple[pd.DataFrame, np.ndarray]:
    """All (origin, h) rows whose *target* is observed before ``cutoff``; returns (X, y_target).

    Only y[:cutoff] is touched, so a model fit on these rows has never seen data at or after
    the cutoff.
    """
    if cutoff - 1 < WARMUP_DAYS:
        raise ValueError("Not enough history before cutoff to build any training rows")
    origins = np.arange(WARMUP_DAYS, cutoff)
    frame = build_origin_features(y[:cutoff], dates, origins, horizon, exog)
    frame = frame[frame["t"] < cutoff].reset_index(drop=True)
    return frame, np.asarray(y, dtype=float)[frame["t"].to_numpy()]
