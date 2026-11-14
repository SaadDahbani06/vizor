"""External source catalog, relevance-based selection, and a common observation record.

Status values are deliberately literal:
  "implemented_live_tested"   connector exists and a real request succeeded in this repository
  "implemented_needs_download" importer exists and is tested on the official schema, but real data
                               must be downloaded by the user (no documented query API)
  "documented_not_implemented" official access exists; no code here yet
  "simulated"                  sandbox / synthetic, never business evidence
Selection never returns a source merely because a key exists.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd


@dataclass(frozen=True)
class ExternalObservation:
    source: str
    series_id: str
    geography: str
    units: str
    observed_at: str           # period the value describes
    available_at: str | None   # first publication / availability, when known
    retrieved_at: str
    value: float
    data_origin: str           # REAL_PUBLISHED_ECONOMIC | REAL_AGGREGATE_INDUSTRY | ...
    vintage_mode: str          # point_in_time | latest | latest_recomputed
    restrictions: str
    freshness_days: int | None = None  # as-of date minus observed_at

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SourceEntry:
    key: str
    name: str
    status: str
    data_origin: str
    access: str
    reaches_models: str
    reaches_recommendations: str
    industries: tuple[str, ...]
    analyses: tuple[str, ...]
    why: str
    caveat: str


CATALOG: tuple[SourceEntry, ...] = (
    SourceEntry("fred", "FRED / ALFRED (St. Louis Fed)", "implemented_live_tested",
                "REAL_PUBLISHED_ECONOMIC", "API key; series/observations with vintages",
                "Only in ablation experiments; rejected on validation so far",
                "Context and hypothetical stress tests (observed values, as of origin)",
                ("restaurant", "retail", "other"),
                ("cost_inflation", "demand_context", "stress_test"),
                "Inflation (CPI, food CPI), unemployment, retail sales, state labor data",
                "National/state aggregates; monthly; revisions handled with ALFRED vintages"),
    SourceEntry("kwick365", "Kwick365 Independent Restaurant Index", "implemented_live_tested",
                "REAL_AGGREGATE_INDUSTRY", "Open JSON API, last 366 days, no key",
                "As a forecast TARGET (benchmark validation); not as a model input (no vintages)",
                "Benchmark context for restaurant pricing decisions",
                ("restaurant",), ("benchmark", "demand_context", "pricing_context"),
                "Daily order volume, ticket size, tips from ~1,900 independent restaurants",
                "KwickPOS restaurants only (not a probability sample); survivorship-recomputed "
                "history; last 3 days revised nightly"),
    SourceEntry("iowa_liquor", "Iowa Liquor Sales (Iowa Dept. of Revenue, dataset 1051)",
                "implemented_needs_download", "REAL_INDIVIDUAL_STORE_PURCHASES",
                "Official CSV export from data.iowa.gov (CC BY 4.0); no query API",
                "Procurement forecasting for one store, once the export is provided",
                "Procurement budget sizing (purchases only)",
                ("retail",), ("procurement_forecast", "procurement_budget"),
                "Store-level spirits purchases by product and date since 2012",
                "Purchases from the state wholesaler, NOT customer sales or inventory"),
    SourceEntry("sec_edgar", "SEC EDGAR companyfacts", "documented_not_implemented",
                "REAL_PUBLIC_COMPANY", "Public JSON API with identifying User-Agent",
                "No", "No (raw client exists; no tag mapping or benchmark built)",
                ("restaurant", "retail"), ("public_company_benchmark",),
                "Public-company margins for benchmarking",
                "Quarterly/annual public companies; never a small business's own data"),
    SourceEntry("bls", "BLS Public Data API", "documented_not_implemented",
                "REAL_PUBLISHED_ECONOMIC", "v1 public / v2 registered key", "No", "No",
                ("restaurant", "retail", "other"), ("labor_cost",),
                "Wages and employment by industry and area",
                "Overlaps with FRED for headline series; not needed for the first demo"),
    SourceEntry("eia", "EIA Open Data", "documented_not_implemented", "REAL_PUBLISHED_ECONOMIC",
                "API key", "No", "No", ("restaurant", "retail", "other"), ("energy_cost",),
                "Electricity and fuel prices", "Not material to the first two demos"),
    SourceEntry("usda_ers", "USDA ERS Food Price Outlook", "documented_not_implemented",
                "REAL_PUBLISHED_ECONOMIC", "Official CSV/XLSX downloads", "No", "No",
                ("restaurant",), ("cost_inflation",), "Food price history and USDA forecasts",
                "Contains forecasts; FRED food CPI already covers the observed series"),
    SourceEntry("nessie", "Capital One Nessie sandbox", "implemented_live_tested", "SIMULATED",
                "API key (sandbox)", "No (28 days; ingestion requires 56+)", "No",
                ("restaurant", "retail", "other"), ("transaction_import_demo",),
                "Demonstrates transaction import and normalization",
                "Simulated banking records; static balances; bills unreadable (server bug)"),
)


def select_sources(industry: str, analyses: tuple[str, ...]) -> list[dict]:
    """Sources relevant to this industry and requested analyses, with honest status."""
    out = []
    for s in CATALOG:
        overlap = sorted(set(analyses) & set(s.analyses))
        if industry in s.industries and overlap:
            out.append({"key": s.key, "name": s.name, "status": s.status,
                        "for": overlap, "why": s.why, "caveat": s.caveat,
                        "usable_now": s.status == "implemented_live_tested"
                        and s.data_origin != "SIMULATED"})
    return out


def fred_observation(vintages: pd.DataFrame, series_id: str, asof: pd.Timestamp, units: str,
                     geography: str, restrictions: str) -> ExternalObservation | None:
    """Latest FRED value as published on ``asof`` (point-in-time), as an ExternalObservation."""
    live = vintages[(vintages.realtime_start <= asof) & (vintages.realtime_end >= asof)]
    if live.empty:
        return None
    row = live.sort_values("observed_at").iloc[-1]
    first = vintages.loc[vintages.observed_at == row.observed_at, "published_at"].min()
    return ExternalObservation(
        "FRED/ALFRED", series_id, geography, units, str(row.observed_at.date()),
        str(first.date()), str(vintages["retrieved_at_utc"].iloc[0]), float(row.value),
        "REAL_PUBLISHED_ECONOMIC", "point_in_time", restrictions,
        int((asof - row.observed_at).days))
