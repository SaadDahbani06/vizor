"""Business profile, configurable column mapping, and supported-analysis detection.

A U.S. small business uploads its own CSV/Excel file and maps its columns to canonical ROLES.
Before any analysis it must CONFIRM its industry, state, decision objective and constraints. The
system then reports which analyses the mapped columns support, and refuses the rest with the
missing columns named. No metric is generated for a role that was not uploaded.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from .data import DatasetSpec, validate_daily_frame

US_STATES = frozenset(["AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY"])
INDUSTRIES = ("restaurant", "retail", "other")

# role -> canonical column. Business-identifier and currency roles are metadata, not series.
ROLES = {
    "date": "date",
    "revenue": "revenue",                    # sales / gross receipts
    "variable_costs": "variable_costs",      # COGS, food, merchandise
    "fixed_costs": "fixed_costs",            # rent, salaried labor, utilities
    "units_sold": "units_sold",              # operational demand (orders, covers, units)
    "inventory_on_hand": "inventory_on_hand",
    "purchase_dollars": "purchase_dollars",  # procurement spend
    "purchase_units": "purchase_units",
    "net_cash_flow": "net_cash_flow",        # if the business tracks cash directly
}
METADATA_ROLES = ("business_id", "currency")

# analysis -> (required roles, description, extra requirement)
ANALYSES = {
    "revenue_forecast": (("revenue",), "Forecast revenue with the model leaderboard"),
    "demand_forecast": (("units_sold",), "Forecast operational demand (orders/units)"),
    "procurement_forecast": (("purchase_dollars",), "Forecast purchasing spend"),
    "cash_risk": (("revenue", "variable_costs", "fixed_costs"),
                  "28-day backtested cash-shortfall simulation (needs confirmed starting cash)"),
    "restaurant_financial_decisions": (("revenue", "variable_costs", "fixed_costs"),
                                       "Price / supplier / discretionary-cost scenarios"),
    "inventory_policy_decisions": (("units_sold", "inventory_on_hand", "revenue",
                                    "variable_costs"),
                                   "Reorder / maintain / reduce inventory policies"),
    "procurement_budget_decisions": (("purchase_dollars",),
                                     "Size a procurement cash reserve from forecast spend"),
}
MIN_DAYS = {"leaderboard": 504, "baseline": 56}


@dataclass(frozen=True)
class BusinessProfile:
    """What the organization itself declares; nothing here is inferred from the data."""

    business_name: str
    industry: str
    state: str
    objective: str
    constraints: dict = field(default_factory=dict)
    currency: str = "USD"
    business_id: str | None = None
    confirmed_by_user: bool = False

    def __post_init__(self) -> None:
        if self.industry not in INDUSTRIES:
            raise ValueError(f"industry must be one of {INDUSTRIES}")
        if self.state.upper() not in US_STATES:
            raise ValueError("state must be a two-letter U.S. state code (or DC)")

    def require_confirmation(self) -> None:
        if not self.confirmed_by_user:
            raise ValueError("The business must confirm industry, state, objective and "
                             "constraints before analysis (BusinessProfile.confirmed_by_user)")


@dataclass(frozen=True)
class ColumnMapping:
    """{uploaded column -> role}. Roles must be known; each role may be mapped once."""

    columns: dict[str, str]
    date_format: str | None = None

    def __post_init__(self) -> None:
        unknown = set(self.columns.values()) - set(ROLES) - set(METADATA_ROLES)
        if unknown:
            raise ValueError(f"unknown roles {sorted(unknown)}; known: {sorted(ROLES)}")
        roles = list(self.columns.values())
        if len(roles) != len(set(roles)):
            raise ValueError("each role may be mapped from exactly one uploaded column")
        if "date" not in roles:
            raise ValueError("a date column must be mapped")


@dataclass
class CapabilityReport:
    rows: int
    days: int
    roles_present: list[str]
    supported: dict[str, str]
    unsupported: dict[str, str]
    notes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def read_upload(path: str | Path, sheet: str | int | None = 0) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path, sheet_name=sheet)
    return pd.read_csv(path)


def apply_mapping(raw: pd.DataFrame, mapping: ColumnMapping) -> tuple[pd.DataFrame, dict]:
    missing = [c for c in mapping.columns if c not in raw.columns]
    if missing:
        raise ValueError(f"mapped columns not found in the upload: {missing}")
    series_cols = {src: ROLES[role] for src, role in mapping.columns.items() if role in ROLES}
    meta = {role: raw[src].dropna().unique().tolist()[:5]
            for src, role in mapping.columns.items() if role in METADATA_ROLES}
    frame = raw[list(series_cols)].rename(columns=series_cols)
    frame["date"] = pd.to_datetime(frame["date"], format=mapping.date_format)
    return frame, meta


def capabilities(frame: pd.DataFrame, profile: BusinessProfile) -> CapabilityReport:
    present = [r for r in ROLES if r != "date" and ROLES[r] in frame.columns]
    days = int(frame["date"].nunique())
    supported, unsupported, notes = {}, {}, []
    for name, (needs, desc) in ANALYSES.items():
        lacking = [r for r in needs if r not in present]
        if lacking:
            unsupported[name] = f"{desc}: missing {lacking}"
            continue
        if name.endswith("_forecast") and days < MIN_DAYS["leaderboard"]:
            unsupported[name] = (f"{desc}: {days} days < {MIN_DAYS['leaderboard']} needed for "
                                 "rolling validation + untouched test at a 28-day horizon")
            continue
        if days < MIN_DAYS["baseline"]:
            unsupported[name] = f"{desc}: {days} days < {MIN_DAYS['baseline']} minimum"
            continue
        if name == "restaurant_financial_decisions" and profile.industry != "restaurant":
            unsupported[name] = f"{desc}: profile industry is {profile.industry}"
            continue
        supported[name] = desc
    if "cash_risk" in supported:
        notes.append("Cash risk also needs a starting cash balance confirmed by the user.")
    if "purchase_dollars" in present and "units_sold" not in present:
        notes.append("Purchases are not customer demand: inventory and demand decisions stay "
                     "unsupported unless units sold and inventory are uploaded.")
    return CapabilityReport(len(frame), days, present, supported, unsupported, notes)


def validate_for_financial_ingestion(frame: pd.DataFrame, profile: BusinessProfile,
                                     missing_days: str = "error"):
    """Route financial uploads through the existing Milestone 1 validation unchanged."""
    profile.require_confirmation()
    return validate_daily_frame(frame, spec=DatasetSpec(currency=profile.currency,
                                                        timezone="UTC",
                                                        business_type=profile.industry,
                                                        currency_declared_by_user=True),
                                missing_days=missing_days, source=profile.business_name)
