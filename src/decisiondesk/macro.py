"""Point-in-time macro features aligned to forecast origins.

For a forecast issued at origin ``o`` the information cut-off is the last observed business day,
``asof = dates[o - 1]``. A macro value is usable only if an ALFRED vintage valid on ``asof``
contained it (``realtime_start <= asof <= realtime_end``). Changes such as year-over-year are
computed *within that same vintage*, so later revisions can never leak into earlier origins.

Feature values are constant across horizons of one origin (they are known at issue time),
which fits the origin-anchored design in ``features.py``. Column names start with ``x_`` so the
ML models pick them up automatically.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .features import WARMUP_DAYS


@dataclass(frozen=True)
class MacroSpec:
    series_id: str
    transform: str  # "yoy_pct" (price/volume indexes) | "level_chg3" (rates)
    why: str


DEFAULT_SERIES = {
    "restaurant": (
        MacroSpec("CUSR0000SAF11", "yoy_pct", "CPI food at home: proxy for food input costs"),
        MacroSpec("CUSR0000SEFV", "yoy_pct", "CPI food away from home: restaurant menu prices"),
        MacroSpec("UNRATE", "level_chg3", "Unemployment rate: local demand / labor context"),
    ),
    "retail": (
        MacroSpec("CPIAUCSL", "yoy_pct", "CPI all items: general price level"),
        MacroSpec("RSXFS", "yoy_pct", "Advance retail sales ex food services: sector demand"),
        MacroSpec("UNRATE", "level_chg3", "Unemployment rate: consumer demand context"),
    ),
}


def snapshot(vintages: pd.DataFrame, asof) -> pd.Series:
    """Values as published on ``asof``: Series indexed by observation date."""
    asof = pd.Timestamp(asof)
    live = vintages[(vintages["realtime_start"] <= asof) & (vintages["realtime_end"] >= asof)]
    return live.set_index("observed_at")["value"].sort_index()


def _features_from_snapshot(snap: pd.Series, asof: pd.Timestamp, spec: MacroSpec) -> dict:
    sid = spec.series_id.lower()
    if snap.empty:
        return {}
    latest = snap.index.max()
    value = float(snap.loc[latest])
    out = {f"x_{sid}_age_days": float((asof - latest).days)}
    if spec.transform == "yoy_pct":
        prior = latest - pd.DateOffset(years=1)
        out[f"x_{sid}_yoy_pct"] = (100 * (value / snap.loc[prior] - 1)
                                   if prior in snap.index else np.nan)
    elif spec.transform == "level_chg3":
        prior = latest - pd.DateOffset(months=3)
        out[f"x_{sid}_level"] = value
        out[f"x_{sid}_chg3"] = value - snap.loc[prior] if prior in snap.index else np.nan
    else:
        raise ValueError(f"unknown transform {spec.transform}")
    return out


def point_in_time_table(vintages: pd.DataFrame, asof_dates, spec: MacroSpec) -> pd.DataFrame:
    """One row per as-of date with the features a forecaster could have known that day."""
    asof_index = pd.DatetimeIndex(pd.unique(pd.DatetimeIndex(asof_dates)))
    rows = [_features_from_snapshot(snapshot(vintages, d), d, spec) for d in asof_index]
    return pd.DataFrame(rows, index=asof_index)


def origin_exog(dates: pd.Series, vintages: dict[str, pd.DataFrame],
                specs: tuple[MacroSpec, ...]) -> pd.DataFrame:
    """Exogenous features indexed by origin 0..n (origin o uses information as of dates[o-1]).

    Origins before the feature warm-up are left NaN (models never use them). Raises if any usable
    origin lacks a value, rather than silently imputing macro data.
    """
    dates = pd.DatetimeIndex(pd.to_datetime(dates))
    n = len(dates)
    asof = pd.DatetimeIndex([pd.NaT, *dates])  # origin o -> dates[o-1]
    parts = []
    for spec in specs:
        table = point_in_time_table(vintages[spec.series_id], dates, spec)
        parts.append(table.reindex(asof).set_axis(range(n + 1)))
    exog = pd.concat(parts, axis=1)
    usable = exog.iloc[WARMUP_DAYS:]
    if usable.isna().any().any():
        bad = usable.columns[usable.isna().any()].tolist()
        raise ValueError(f"Point-in-time macro features missing for some origins: {bad}. "
                         "Fetch vintages with an earlier observation_start.")
    return exog
