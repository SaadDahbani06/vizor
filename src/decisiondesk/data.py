"""Explicit, auditable ingestion of daily business aggregates.

No automatic guessing of goals, currency or missing values: the user declares them in a
``DatasetSpec`` and every transformation is recorded in an ``IngestionReport``. Reports carry
summary statistics and a file hash only, never raw rows, so they are safe to log or show to an LLM.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

FINANCIAL_COLUMNS = ("date", "revenue", "variable_costs", "fixed_costs")
OPTIONAL_COLUMNS = ("units_sold", "inventory_on_hand")
MIN_DAYS = 56

DEFAULT_METRIC_DEFINITIONS = {
    "revenue": "Gross sales for the calendar day, positive magnitude, in `currency`.",
    "variable_costs": "Costs that scale with volume (COGS, food, merchandise), positive magnitude.",
    "fixed_costs": "Daily allocation of rent, salaried labor, utilities, positive magnitude.",
    "units_sold": "Count of units / covers sold during the day.",
    "inventory_on_hand": "Units on hand at end of day (after receipts and sales).",
}

# How each column is filled when the user explicitly declares missing days as closures.
_CLOSED_DAY_FILL = {
    "revenue": "zero",
    "variable_costs": "zero",
    "units_sold": "zero",
    "fixed_costs": "forward_fill",  # rent etc. still accrues on a closed day
    "inventory_on_hand": "forward_fill",  # no sales, assume no receipts
}


@dataclass(frozen=True)
class DatasetSpec:
    """User-declared facts about an upload; the system never infers these silently."""

    currency: str = "USD"
    timezone: str = "UTC"
    business_type: str = "unspecified"
    metric_definitions: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_METRIC_DEFINITIONS))
    currency_declared_by_user: bool = False

    def __post_init__(self) -> None:
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise ValueError("currency must be an ISO-4217 three-letter code, e.g. 'USD'")


@dataclass
class IngestionReport:
    source: str
    sha256: str | None
    spec: dict
    column_mapping: dict[str, str]
    n_rows_raw: int
    n_rows_final: int
    start_date: str
    end_date: str
    missing_day_policy: str
    missing_days: list[str]
    imputed_columns: dict[str, str]
    optional_columns_present: list[str]
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"source={self.source} sha256={(self.sha256 or 'n/a')[:12]}",
            (f"rows raw={self.n_rows_raw} final={self.n_rows_final} "
             f"range={self.start_date}..{self.end_date}"),
            (f"currency={self.spec['currency']}"
             f"{'' if self.spec['currency_declared_by_user'] else ' (default, not declared)'} "
             f"tz={self.spec['timezone']}"),
            f"missing days={len(self.missing_days)} policy={self.missing_day_policy}",
        ]
        lines += [f"WARNING: {w}" for w in self.warnings]
        return "\n".join(lines)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_daily_frame(frame: pd.DataFrame, *, spec: DatasetSpec | None = None,
                         mapping: dict[str, str] | None = None,
                         missing_days: str = "error", source: str = "in-memory",
                         sha256: str | None = None) -> tuple[pd.DataFrame, IngestionReport]:
    """Validate canonical daily aggregates and return (clean_frame, report).

    missing_days:
      - "error" (default): any gap in the calendar raises; the user must resolve it.
      - "closed_zero": the user asserts gaps are closures. Sales-type columns become 0,
        fixed costs / inventory are forward-filled, and an ``is_imputed`` flag is added.
    """
    if missing_days not in {"error", "closed_zero"}:
        raise ValueError("missing_days must be 'error' or 'closed_zero'")
    spec = spec or DatasetSpec()
    mapping = dict(mapping or {})
    n_raw = len(frame)
    frame = frame.rename(columns=mapping).copy()
    if frame.columns.duplicated().any():
        raise ValueError("Column mapping produced duplicate column names")
    missing = set(FINANCIAL_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing required canonical fields: {sorted(missing)}")

    warnings: list[str] = []
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    if frame["date"].isna().any():
        raise ValueError("Dates must not be missing")
    if getattr(frame["date"].dt, "tz", None) is not None:
        frame["date"] = frame["date"].dt.tz_convert(spec.timezone).dt.tz_localize(None)
    if (frame["date"] != frame["date"].dt.normalize()).any():
        raise ValueError("Dates carry intra-day times; aggregate to calendar days first")

    numeric = [c for c in (*FINANCIAL_COLUMNS[1:], *OPTIONAL_COLUMNS) if c in frame.columns]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if frame[column].isna().any():
            raise ValueError(f"{column} has blank values; fill or remove them explicitly")
        if (frame[column] < 0).any():
            raise ValueError(f"{column} must be a nonnegative magnitude (costs are not signed)")
    if frame["date"].duplicated().any():
        raise ValueError("Expected one row per day; aggregate transaction-level input separately")
    frame = frame.sort_values("date").reset_index(drop=True)

    full_range = pd.date_range(frame["date"].iloc[0], frame["date"].iloc[-1], freq="D")
    gaps = full_range.difference(pd.DatetimeIndex(frame["date"]))
    imputed: dict[str, str] = {}
    if len(gaps):
        if missing_days == "error":
            raise ValueError(
                f"Daily data must be continuous; {len(gaps)} missing day(s), first "
                f"{gaps[0].date()}. Resolve them or pass missing_days='closed_zero' if they "
                "are closures.")
        frame = frame.set_index("date").reindex(full_range)
        frame.index.name = "date"
        frame["is_imputed"] = frame[numeric[0]].isna()
        for column in numeric:
            how = _CLOSED_DAY_FILL[column]
            frame[column] = frame[column].fillna(0.0) if how == "zero" else frame[column].ffill()
            imputed[column] = how
        frame = frame.reset_index()
        warnings.append(f"{len(gaps)} day(s) imputed as closures; flagged in `is_imputed`")
    else:
        frame["is_imputed"] = False

    if len(frame) < MIN_DAYS:
        raise ValueError(f"At least {MIN_DAYS} daily observations are required for the baseline")
    zero_revenue = int((frame["revenue"] == 0).sum())
    if zero_revenue and not len(gaps):
        warnings.append(f"{zero_revenue} day(s) with zero revenue; confirm they are closures")
    if not spec.currency_declared_by_user:
        warnings.append(f"Currency not declared by user; assuming {spec.currency}")
    unknown = [c for c in frame.columns
               if c not in (*FINANCIAL_COLUMNS, *OPTIONAL_COLUMNS, "is_imputed")]
    if unknown:
        warnings.append(f"Ignoring unmapped columns: {unknown}")

    report = IngestionReport(
        source=source, sha256=sha256, spec=asdict(spec), column_mapping=mapping,
        n_rows_raw=n_raw, n_rows_final=len(frame),
        start_date=str(frame["date"].iloc[0].date()), end_date=str(frame["date"].iloc[-1].date()),
        missing_day_policy=missing_days, missing_days=[str(d.date()) for d in gaps],
        imputed_columns=imputed,
        optional_columns_present=[c for c in OPTIONAL_COLUMNS if c in frame.columns],
        warnings=warnings)
    return frame, report


def ingest_daily_csv(path: str, mapping: dict[str, str] | None = None, *,
                     spec: DatasetSpec | None = None,
                     missing_days: str = "error") -> tuple[pd.DataFrame, IngestionReport]:
    """Read a CSV, apply the user's column mapping, validate, and return an audit report."""
    raw = pd.read_csv(path)
    return validate_daily_frame(raw, spec=spec, mapping=mapping, missing_days=missing_days,
                                source=Path(path).name, sha256=_sha256(path))


def load_daily_data(path: str, mapping: dict[str, str] | None = None) -> pd.DataFrame:
    """Backward-compatible loader: strict (gaps raise), returns only the frame.

    mapping: {'uploaded_field': 'canonical_field'}, e.g. {'sales': 'revenue'}.
    """
    frame, _ = ingest_daily_csv(path, mapping)
    return frame


def daily_cash_flow(frame: pd.DataFrame) -> pd.Series:
    return frame["revenue"] - frame["variable_costs"] - frame["fixed_costs"]
