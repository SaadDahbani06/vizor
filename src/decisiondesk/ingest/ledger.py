"""Normalized accrual ledger shared by every financial data source.

Every connector (QuickBooks Online, Square, bank/accounting CSV) converts its records into ledger
entries, one row per transaction line, before anything reaches the forecasting engine:

    entry_id        stable key: "<source>:<entity>:<source_id>:<line>" (used for de-duplication)
    source          quickbooks | square | file:<name>
    source_entity   e.g. Invoice, Bill, Purchase, Order, Refund, row
    source_id       the provider's own id (kept so every number can be traced back)
    sync_token      provider revision (QuickBooks SyncToken); a newer revision replaces an older one
    txn_date        calendar date of the transaction (accrual basis: invoice/bill date)
    amount          positive magnitude in `currency`; refunds, credits and returns are negative
    currency        ISO-4217 code exactly as the source reported it (never converted)
    category        revenue | variable_cost | fixed_cost | uncategorized | excluded
    account_*       account id / name / type when the source has one
    counterparty, description, data_origin, retrieved_at_utc

Rules:
  * Nothing is invented. A record with no amount or no date is skipped and counted in the report.
  * Mixed currencies are never added together: entries outside the chosen currency are left out of
    the daily totals and reported.
  * Days with no entries become zero-activity days and are counted in the report (for transaction
    data "no transactions recorded" is the fact; for a closed business the owner should confirm).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import pandas as pd

LEDGER_COLUMNS = ("entry_id", "source", "source_entity", "source_id", "source_line", "sync_token",
                  "txn_date", "amount", "currency", "category", "account_id", "account_name",
                  "account_type", "counterparty", "description", "data_origin", "retrieved_at_utc")
CATEGORIES = ("revenue", "variable_cost", "fixed_cost", "uncategorized", "excluded")
DAILY_CATEGORY_COLUMNS = {"revenue": "revenue", "variable_cost": "variable_costs", "fixed_cost": "fixed_costs"}


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def entry(source: str, entity: str, source_id: str, line: str | int, *, txn_date, amount, currency: str,
          category: str, sync_token: str | None = None, account_id: str | None = None,
          account_name: str | None = None, account_type: str | None = None, counterparty: str | None = None,
          description: str | None = None, data_origin: str = "", retrieved_at_utc: str | None = None) -> dict:
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    return {"entry_id": f"{source}:{entity}:{source_id}:{line}", "source": source, "source_entity": entity,
            "source_id": str(source_id), "source_line": str(line), "sync_token": sync_token,
            "txn_date": pd.Timestamp(txn_date).normalize(), "amount": float(amount), "currency": currency,
            "category": category, "account_id": account_id, "account_name": account_name,
            "account_type": account_type, "counterparty": counterparty, "description": description,
            "data_origin": data_origin, "retrieved_at_utc": retrieved_at_utc or now_utc()}


@dataclass
class LedgerReport:
    source: str
    n_records_read: int = 0
    n_entries: int = 0
    duplicates_dropped: int = 0
    replaced_by_newer_revision: int = 0
    skipped: dict = field(default_factory=dict)        # reason -> count (never filled in)
    totals: dict = field(default_factory=dict)          # category -> amount
    currency: str | None = None
    other_currencies: dict = field(default_factory=dict)
    start_date: str | None = None
    end_date: str | None = None
    days: int = 0
    zero_activity_days: int = 0
    reconciliation: dict | None = None
    warnings: list[str] = field(default_factory=list)

    def skip(self, reason: str, n: int = 1) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + n

    def to_dict(self) -> dict:
        return asdict(self)


def to_frame(entries: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(entries, columns=list(LEDGER_COLUMNS))
    if len(df):
        df["txn_date"] = pd.to_datetime(df["txn_date"])
        df["amount"] = df["amount"].astype(float)
    return df


def deduplicate(ledger: pd.DataFrame, report: LedgerReport | None = None) -> pd.DataFrame:
    """One row per entry_id. If the source supplies revisions (sync_token), the newest wins;
    otherwise identical repeats are dropped. Re-ingesting the same data is therefore a no-op."""
    if ledger.empty:
        return ledger
    n0 = len(ledger)
    rev = pd.to_numeric(ledger["sync_token"], errors="coerce").fillna(-1)
    exact = int(ledger.duplicated(subset=list(LEDGER_COLUMNS[:9])).sum())
    work = ledger.assign(_rev=rev)
    # A newer revision of a record replaces *all* lines of the older one (an edit can delete a line).
    newest = work.groupby(["source", "source_entity", "source_id"])["_rev"].transform("max")
    work = work[work["_rev"] >= newest]
    kept = work.sort_values(["entry_id", "_rev"]).drop_duplicates("entry_id", keep="last").drop(columns="_rev")
    if report is not None:
        report.duplicates_dropped += exact
        report.replaced_by_newer_revision += (n0 - len(kept)) - exact
    return kept.sort_values(["txn_date", "entry_id"]).reset_index(drop=True)


def merge(existing: pd.DataFrame | None, new: pd.DataFrame, report: LedgerReport | None = None) -> pd.DataFrame:
    """Incremental ingestion: add new entries to a stored ledger without double counting."""
    if existing is None or existing.empty:
        return deduplicate(new, report)
    return deduplicate(pd.concat([existing, new], ignore_index=True), report)


def ledger_to_daily(ledger: pd.DataFrame, report: LedgerReport, *, currency: str | None = None,
                    start=None, end=None) -> pd.DataFrame:
    """Daily totals in the engine's schema: date, revenue, variable_costs, fixed_costs.

    Raises ValueError (with a plain explanation) when the data cannot be turned into honest daily
    totals, e.g. no revenue entries, or a day where refunds exceed sales."""
    if ledger.empty:
        raise ValueError("No transactions were found for this period.")
    cur = currency or ledger["currency"].mode().iloc[0]
    if cur == "UNKNOWN":
        raise ValueError("The currency of these records is unknown, so their amounts cannot be added up safely. "
                         "Set the company's home currency in the source system and import again.")
    report.currency = cur
    other = ledger[ledger["currency"] != cur]
    if len(other):
        report.other_currencies = {k: int(v) for k, v in other["currency"].value_counts().items()}
        report.warnings.append(f"{len(other)} entries in other currencies ({', '.join(report.other_currencies)}) "
                               f"are left out: amounts are never converted between currencies.")
    led = ledger[ledger["currency"] == cur]
    report.totals = {c: round(float(led.loc[led["category"] == c, "amount"].sum()), 2) for c in CATEGORIES}
    unc = led[led["category"] == "uncategorized"]
    if len(unc):
        report.warnings.append(f"{len(unc)} entries ({report.totals['uncategorized']:,.2f} {cur}) could not be "
                               "classified as revenue or cost and are left out of profit; map their accounts "
                               "to include them.")
    use = led[led["category"].isin(DAILY_CATEGORY_COLUMNS)]
    if not (use["category"] == "revenue").any():
        raise ValueError("No revenue entries were found, so sales and profit cannot be calculated.")
    first = pd.Timestamp(start) if start is not None else use["txn_date"].min()
    last = pd.Timestamp(end) if end is not None else use["txn_date"].max()
    use = use[(use["txn_date"] >= first) & (use["txn_date"] <= last)]
    days = pd.date_range(first, last, freq="D")
    pivot = use.pivot_table(index="txn_date", columns="category", values="amount", aggfunc="sum")
    pivot = pivot.reindex(days)
    report.zero_activity_days = int(pivot.isna().all(axis=1).sum())
    pivot = pivot.fillna(0.0)
    daily = pd.DataFrame({"date": days})
    for cat, col in DAILY_CATEGORY_COLUMNS.items():
        daily[col] = pivot[cat].to_numpy() if cat in pivot else 0.0
    negative = daily[(daily[list(DAILY_CATEGORY_COLUMNS.values())] < -0.005).any(axis=1)]
    if len(negative):
        raise ValueError(f"{len(negative)} day(s) have more refunds or credits than sales or costs (first: "
                         f"{negative['date'].iloc[0].date()}). Daily totals would be negative, so they are not "
                         "used; check those days in the source system.")
    daily[list(DAILY_CATEGORY_COLUMNS.values())] = daily[list(DAILY_CATEGORY_COLUMNS.values())].clip(lower=0).round(2)
    report.start_date, report.end_date, report.days = str(first.date()), str(last.date()), len(days)
    if report.zero_activity_days:
        report.warnings.append(f"{report.zero_activity_days} day(s) had no transactions and count as zero activity.")
    return daily


def reconcile(report: LedgerReport, reference: dict[str, float], *, tolerance_pct: float = 1.0) -> dict:
    """Compare ledger totals with a source's own report (e.g. QuickBooks Profit and Loss)."""
    rows = {}
    for cat, ref in reference.items():
        ours = (report.totals.get("variable_cost", 0.0) + report.totals.get("fixed_cost", 0.0)
                if cat == "total_cost" else report.totals.get(cat, 0.0))
        diff = round(ours - ref, 2)
        pct = None if not ref else round(diff / abs(ref) * 100, 2)
        rows[cat] = {"ledger": ours, "source_report": round(ref, 2), "difference": diff, "difference_pct": pct,
                     "matches": abs(diff) <= max(0.01, abs(ref) * tolerance_pct / 100)}
    out = {"tolerance_pct": tolerance_pct, "categories": rows, "all_match": all(r["matches"] for r in rows.values())}
    report.reconciliation = out
    if not out["all_match"]:
        bad = [k for k, r in rows.items() if not r["matches"]]
        report.warnings.append(f"Totals differ from the source's own report for: {', '.join(bad)}. "
                               "See the reconciliation table before relying on these numbers.")
    return out
