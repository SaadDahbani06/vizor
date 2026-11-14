"""External economic context for explanations and hypothetical stress tests.

Four kinds of statement are kept apart (each item carries ``category``):
  observed_economic_data      FRED/ALFRED values as published on the forecast origin date
  historical_relationship     statistical links estimated from data (none: FRED features failed
                              validation in the Milestone 2 ablation, so none is claimed)
  hypothetical_change         user/analyst "what if" shocks, e.g. supplier costs +X %
  estimated_intervention      action effects; here always ASSUMED, never estimated causally

FRED values are NOT fed into the forecasting models (ablation rejected them).
"""
from __future__ import annotations

import copy

import pandas as pd

from ..connectors.base import ConnectorError
from ..connectors.fred import FREDConnector
from ..macro import DEFAULT_SERIES, _latest_comparable, snapshot
from .baseline import ForecastBundle


def economic_context(business_type: str, asof: pd.Timestamp,
                     cache_dir: str = "data/cache/fred") -> tuple[list[dict], list[str]]:
    """Point-in-time FRED readings as of ``asof`` from the local cache (no network calls)."""
    notes, items = [], []
    conn = FREDConnector(cache_dir=cache_dir, offline=True)
    for spec in DEFAULT_SERIES.get(business_type, ()):
        try:
            vintages, prov = conn.fetch_vintages(spec.series_id, "2021-01-01")
        except ConnectorError as exc:
            notes.append(f"FRED {spec.series_id} unavailable offline ({exc}); run "
                         "`python -m decisiondesk.ablation` once with a key to cache it.")
            continue
        snap = snapshot(vintages, asof)
        if snap.empty:
            continue
        latest = snap.index.max()
        # Newest month with a published comparison month in the same vintage (October 2025 was
        # never published, so e.g. October 2026 has no year-earlier value).
        comp = _latest_comparable(snap, pd.DateOffset(years=1))
        if comp is None:
            yoy = None
        elif spec.transform == "level_chg3":  # rates: change in percentage points, not %
            yoy = float(snap[comp] - snap[comp - pd.DateOffset(years=1)])
        else:
            yoy = 100 * (snap[comp] / snap[comp - pd.DateOffset(years=1)] - 1)
        published = vintages.loc[vintages.observed_at == latest, "published_at"].min()
        items.append({
            "category": "observed_economic_data", "series_id": spec.series_id,
            "why": spec.why, "observed_at": str(latest.date()), "value": float(snap[latest]),
            "units": prov.units, "yoy_pct": None if yoy is None else round(float(yoy), 2),
            "yoy_unit": "percentage points" if spec.transform == "level_chg3" else "%",
            "yoy_period": None if comp is None else str(comp.date())[:7],
            "published_at": str(published.date()), "as_of": str(asof.date()),
            "retrieved_at_utc": str(vintages["retrieved_at_utc"].iloc[0]),
            "source_url": prov.source_url, "vintage_mode": prov.vintage_mode,
        })
    return items, notes


def stressed(bundle: ForecastBundle, factors: dict[str, float]) -> ForecastBundle:
    """Copy of the baseline with targets scaled by hypothetical factors (point and errors)."""
    out = copy.deepcopy(bundle)
    for target, factor in factors.items():
        tf = out.targets[target]
        tf.point = tf.point * factor
        tf.residuals = tf.residuals * factor
    out.notes = [*out.notes, f"HYPOTHETICAL stress: {factors}"]
    return out
