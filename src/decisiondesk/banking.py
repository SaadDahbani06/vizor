"""Selectable SIMULATED banking source: Capital One Nessie sandbox (read-only import).

Imports accounts and transactions with the existing connector and normalizer, categorizes cash
inflows/outflows, flags internal transfers, identifies missing bill records, and reports whether
the history is fit for validated forecasting (it is not, today). Nessie account balances are shown
only as "reported by sandbox" and are never used as cash balances.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .connectors.nessie import NessieClient
from .nessie_sandbox import ACCOUNTS, CUSTOMER, fetch_all
from .transactions import nessie_to_canonical, transactions_to_daily

MIN_FORECAST_DAYS = 504
EXPECTED_RECURRING = ("rent", "utilities", "payroll")


@dataclass
class BankingImport:
    source: str
    accounts: list[dict]
    transactions: pd.DataFrame
    daily: pd.DataFrame
    by_category: pd.DataFrame
    missing_bills: list[str]
    unreadable: dict
    duplicates_dropped: int
    forecast_eligible: bool
    eligibility_reasons: list[str]
    notes: list[str] = field(default_factory=list)


def _business_kind(desc: str) -> str:
    from .transactions import TAG_RE
    m = TAG_RE.search(desc or "")
    return m["kind"] if m else ""


def import_banking(client: NessieClient | None = None) -> BankingImport:
    client = client or NessieClient()
    records, account_ids, merchants, unreadable = fetch_all(client)
    tx, report = nessie_to_canonical(records, org_account_ids=set(account_ids.values()),
                                     merchants=merchants)
    customer = next((c for c in client.list_customers()
                     if c.get("last_name") == CUSTOMER["last_name"]), None)
    raw_accounts = client.list_accounts(customer["_id"]) if customer else []
    accounts = []
    for role, aid in account_ids.items():
        rows = tx[tx.account_id == aid]
        reported = next((a.get("balance") for a in raw_accounts if a.get("_id") == aid), None)
        accounts.append({
            "role": role, "nickname": ACCOUNTS[role]["nickname"],
            "inflows": float(rows.loc[rows.amount > 0, "amount"].sum()),
            "outflows": float(-rows.loc[rows.amount < 0, "amount"].sum()),
            "internal_transfers_net": float(rows.loc[rows.is_internal_transfer, "amount"].sum()),
            "transactions": len(rows), "balance_reported_by_sandbox": reported,
            "balance_note": "Nessie balances do not change with posted transactions; not used "
                            "as a cash balance."})
    cash = tx[tx.counts_as_cash]
    by_cat = (cash.assign(flow=lambda d: d.amount.gt(0).map({True: "inflow", False: "outflow"}))
              .groupby(["flow", "category"])["amount"].agg(["count", "sum"]).reset_index())
    daily = transactions_to_daily(tx) if len(cash) else pd.DataFrame()
    kinds = {_business_kind(d) for d in tx.description}
    missing = [(f"bills list unreadable on the server ({len(unreadable)} endpoint(s)): bill "
               "records exist in the sandbox but cannot be imported")] if unreadable else []
    for need in EXPECTED_RECURRING:
        if need not in kinds:
            missing.append(f"no {need} payment found in imported transactions")
    days = 0 if daily.empty else len(daily)
    reasons = []
    if days < MIN_FORECAST_DAYS:
        reasons.append(f"{days} days of history < {MIN_FORECAST_DAYS} needed for validated "
                       "28-day forecasts")
    if missing:
        reasons.append("known missing bill records would understate costs")
    reasons.append("simulated sandbox data, not the business's real bank history")
    return BankingImport(
        source="Capital One Nessie sandbox (SIMULATED)", accounts=accounts, transactions=tx,
        daily=daily, by_category=by_cat, missing_bills=missing, unreadable=unreadable,
        duplicates_dropped=report.duplicates_dropped, forecast_eligible=False,
        eligibility_reasons=reasons,
        notes=[("Forecasts and decisions stay on the longer synthetic business dataset until "
               "enough consistent bank history exists.")])
