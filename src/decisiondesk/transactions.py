"""Canonical row-level transaction schema and conversion to daily cash-basis aggregates.

Sign convention (business perspective of the account holder):
    amount > 0  money INTO the organization (sales deposits, incoming transfers)
    amount < 0  money OUT of the organization (purchases, withdrawals, bills paid, outgoing transfers)

Transfers between two accounts that BOTH belong to the organization are internal: each leg is kept
(one row per account, so per-account balances reconcile) but flagged ``is_internal_transfer`` and
excluded from revenue/cost aggregates, where they would otherwise double count.

Only rows with ``counts_as_cash`` contribute to daily aggregates. Pending/recurring bills are
schedules, not cash that moved.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd

TRANSACTION_COLUMNS = (
    "transaction_id", "source", "source_record_type", "source_record_id", "account_id",
    "posted_date", "amount", "currency", "direction", "status", "counts_as_cash", "category",
    "counterparty", "description", "is_internal_transfer", "data_origin", "retrieved_at_utc",
)
CATEGORIES = ("revenue", "variable_cost", "fixed_cost", "internal_transfer", "external_transfer",
              "uncategorized")
CASH_STATUSES = frozenset({"completed", "executed", "posted"})

# Seeded sandbox records carry a machine-readable tag, e.g. "[MSRD-DEMO v1 card_sales 2026-10-17]".
TAG_RE = re.compile(r"\[MSRD-DEMO v1 (?P<kind>[a-z_]+) (?P<key>[^\]]+)\]")
KIND_TO_CATEGORY = {
    "card_sales": "revenue",
    "supplier_produce": "variable_cost", "supplier_dry_goods": "variable_cost",
    "petty_cash": "variable_cost",
    "payroll": "fixed_cost", "rent": "fixed_cost", "utilities": "fixed_cost", "linen": "fixed_cost",
    "reserve_transfer": "internal_transfer", "reserve_transfer_in": "internal_transfer",
}


@dataclass
class ConversionReport:
    n_input_records: int = 0
    n_rows: int = 0
    duplicates_dropped: int = 0
    by_category: dict = field(default_factory=dict)
    excluded_non_cash: int = 0
    internal_transfer_legs: int = 0
    uncategorized_rows: int = 0
    warnings: list[str] = field(default_factory=list)


def record_id(record: dict) -> str:
    """Source id. prod-api returns ``_id`` for most objects but ``id`` for transfers. Without
    either, key by a content hash so distinct records are never merged as duplicates."""
    rid = record.get("_id") or record.get("id")
    if rid:
        return str(rid)
    digest = hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()
    return f"content-{digest[:16]}"


def _date(record: dict, *keys: str):
    for key in keys:
        value = record.get(key)
        if value:
            return pd.Timestamp(str(value)[:10])
    return pd.NaT


def _category(description: str, default: str) -> str:
    match = TAG_RE.search(description or "")
    if match:
        return KIND_TO_CATEGORY.get(match["kind"], "uncategorized")
    return default


def nessie_to_canonical(records: dict[str, dict[str, list[dict]]], *, org_account_ids: set[str],
                        merchants: dict[str, str] | None = None, currency: str = "USD",
                        retrieved_at_utc: str | None = None) -> tuple[pd.DataFrame, ConversionReport]:
    """Convert Nessie records to canonical rows.

    records: {account_id: {"deposits": [...], "withdrawals": [...], "purchases": [...],
              "transfers": [...], "bills": [...]}} exactly as returned by GET endpoints.
    org_account_ids: every account owned by the organization (decides internal transfers).
    """
    merchants = merchants or {}
    retrieved = retrieved_at_utc or datetime.now(UTC).isoformat(timespec="seconds")
    report = ConversionReport()
    rows = []
    for account_id, kinds in records.items():
        for kind, items in kinds.items():
            for rec in items:
                report.n_input_records += 1
                rid = record_id(rec)
                if rid.startswith("content-"):
                    report.warnings.append(f"{kind} record without _id/id; keyed by content hash")
                desc = rec.get("description") or rec.get("nickname") or ""
                status = str(rec.get("status", "")).lower()
                amount = float(rec.get("amount", rec.get("payment_amount", 0)) or 0)
                internal = False
                if kind == "deposits":
                    signed, date, counterparty = amount, _date(rec, "transaction_date"), rec.get("medium", "")
                    category = _category(desc, "uncategorized")
                elif kind == "withdrawals":
                    signed, date, counterparty = -amount, _date(rec, "transaction_date"), rec.get("medium", "")
                    category = _category(desc, "uncategorized")
                elif kind == "purchases":
                    signed = -amount
                    date = _date(rec, "purchase_date", "transaction_date")
                    counterparty = merchants.get(rec.get("merchant_id"), rec.get("merchant_id", ""))
                    category = _category(desc, "uncategorized")
                elif kind == "transfers":
                    payer, payee = rec.get("payer_id"), rec.get("payee_id")
                    if payee and account_id == payee and account_id != payer:
                        signed, counterparty = amount, payer
                    elif account_id == payer or not payer:
                        # prod-api transfers carry no payee; a transfer listed under an account
                        # is that account's outgoing leg.
                        signed, counterparty = -amount, payee
                    else:
                        report.warnings.append(f"transfer {rid} lists neither leg as {account_id}")
                        continue
                    internal = ((payer in org_account_ids and payee in org_account_ids)
                                or _category(desc, "") == "internal_transfer")
                    category = "internal_transfer" if internal else "external_transfer"
                    date = _date(rec, "transaction_date")
                elif kind == "bills":
                    signed = -float(rec.get("payment_amount", 0) or 0)
                    date = _date(rec, "payment_date", "upcoming_payment_date", "creation_date")
                    counterparty = rec.get("payee", "")
                    category = _category(desc, "fixed_cost")
                else:
                    raise ValueError(f"Unknown Nessie record kind: {kind}")
                rows.append({
                    "transaction_id": f"nessie:{kind}:{rid}:{account_id}",
                    "source": "nessie", "source_record_type": kind.rstrip("s"),
                    "source_record_id": rid, "account_id": account_id, "posted_date": date,
                    "amount": signed, "currency": currency,
                    "direction": "inflow" if signed > 0 else "outflow", "status": status,
                    "counts_as_cash": status in CASH_STATUSES, "category": category,
                    "counterparty": str(counterparty or ""), "description": desc,
                    "is_internal_transfer": internal or category == "internal_transfer",
                    "data_origin": "SYNTHETIC_SANDBOX_NESSIE", "retrieved_at_utc": retrieved,
                })
    frame = pd.DataFrame(rows, columns=list(TRANSACTION_COLUMNS))
    before = len(frame)
    frame = frame.drop_duplicates("transaction_id").sort_values(
        ["posted_date", "transaction_id"]).reset_index(drop=True)
    report.duplicates_dropped = before - len(frame)
    report.n_rows = len(frame)
    report.by_category = frame["category"].value_counts().to_dict()
    report.excluded_non_cash = int((~frame["counts_as_cash"]).sum())
    report.internal_transfer_legs = int(frame["is_internal_transfer"].sum())
    report.uncategorized_rows = int((frame["category"] == "uncategorized").sum())
    if frame["posted_date"].isna().any():
        report.warnings.append(f"{int(frame['posted_date'].isna().sum())} row(s) have no date")
    return frame, report


def transactions_to_daily(tx: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    """Cash-basis daily aggregates in the canonical daily schema (positive cost magnitudes).

    NOTE: this is CASH basis. Payroll and rent land on the day they are paid, unlike the
    accrual-style daily ``fixed_costs`` in the synthetic CSVs; do not mix the two in one series.
    """
    cash = tx[tx["counts_as_cash"] & ~tx["is_internal_transfer"] & tx["posted_date"].notna()]
    first = pd.Timestamp(start) if start is not None else cash["posted_date"].min()
    last = pd.Timestamp(end) if end is not None else cash["posted_date"].max()
    days = pd.date_range(first, last, freq="D")
    pivot = cash.pivot_table(index="posted_date", columns="category", values="amount",
                             aggfunc="sum").reindex(days, fill_value=0.0).fillna(0.0)

    def col(name):
        return pivot[name] if name in pivot else pd.Series(0.0, index=days)

    out = pd.DataFrame({
        "date": days,
        "revenue": col("revenue").to_numpy(),
        "variable_costs": -col("variable_cost").to_numpy(),
        "fixed_costs": -col("fixed_cost").to_numpy(),
        "external_transfers_net": col("external_transfer").to_numpy(),
        "uncategorized_net": col("uncategorized").to_numpy(),
    })
    return out
