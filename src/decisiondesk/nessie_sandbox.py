"""Idempotent Nessie SANDBOX seeding and import for one fictional restaurant.

Everything created here is SYNTHETIC banking data in Capital One's Nessie hackathon sandbox.
It is generated from our own synthetic restaurant model and must never be presented as real
historical business data or used as evidence of model accuracy.

  python -m decisiondesk.nessie_sandbox plan                 # offline, prints the seed plan
  python -m decisiondesk.nessie_sandbox init                 # dry run: reads Nessie, writes nothing
  python -m decisiondesk.nessie_sandbox init --confirm       # creates only the missing records
  python -m decisiondesk.nessie_sandbox import               # GET records -> canonical CSVs

Idempotency: every record carries a tag "[MSRD-DEMO v1 <kind> <key>]" in its description (bills:
nickname). Before creating anything the script lists what already exists and skips tagged
matches, so re-running ``init --confirm`` creates nothing new. Nessie has no server-side
idempotency keys, so two *concurrent* runs could still race; run one at a time.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np

from .connectors.base import ConnectorError
from .connectors.nessie import RECORD_KINDS, NessieClient
from .synthetic import make_sample
from .transactions import TAG_RE, nessie_to_canonical, transactions_to_daily

SCHEMA_VERSION = "v1"
CUSTOMER = {"first_name": "Harbor Lane", "last_name": "Bistro MSRD-DEMO",
            "address": {"street_number": "100", "street_name": "Example Way",
                        "city": "Princeton", "state": "NJ", "zip": "08540"}}
ACCOUNTS = {
    "checking": {"type": "Checking", "nickname": "MSRD-DEMO operating checking",
                 "rewards": 0, "balance": 15000},
    "savings": {"type": "Savings", "nickname": "MSRD-DEMO cash reserve", "rewards": 0,
                "balance": 5000},
}
MERCHANTS = {
    "produce": {"name": "MSRD-DEMO Garden State Produce (fictional)", "category": "food",
                "address": {"street_number": "1", "street_name": "Fictional Farm Rd",
                            "city": "Trenton", "state": "NJ", "zip": "08608"}},
    "dry_goods": {"name": "MSRD-DEMO Shore Restaurant Supply (fictional)", "category": "supplies",
                  "address": {"street_number": "2", "street_name": "Fictional Depot Ave",
                              "city": "Edison", "state": "NJ", "zip": "08817"}},
}
STATE_PATH = Path("data/cache/nessie_sandbox_state.json")
OUT_DIR = Path("data/cache")


def tag(kind: str, key: str) -> str:
    return f"[MSRD-DEMO {SCHEMA_VERSION} {kind} {key}]"


@dataclass(frozen=True)
class SeedRecord:
    account: str        # "checking" | "savings"
    endpoint: str       # one of RECORD_KINDS
    business_kind: str  # card_sales, payroll, ...
    key: str            # unique within business_kind
    date: str
    amount: float
    description: str
    extra: tuple = ()   # (("merchant", "produce"),) or (("payee_account", "savings"),) ...

    @property
    def tag(self) -> str:
        return tag(self.business_kind, self.key)

    def body(self, merchant_ids: dict[str, str], account_ids: dict[str, str]) -> dict:
        extra = dict(self.extra)
        text = f"{self.description} {self.tag}"
        if self.endpoint == "bills":
            body = {"status": extra["status"], "payee": extra["payee"], "nickname": text,
                    "payment_date": self.date, "payment_amount": round(self.amount, 2)}
            if "recurring_date" in extra:
                body["recurring_date"] = extra["recurring_date"]
            return body
        amount = round(self.amount)
        if self.endpoint == "purchases":
            # prod-api PurchaseCreate: purchase_date (transaction_date is rejected as extra).
            return {"merchant_id": merchant_ids[extra["merchant"]], "medium": "balance",
                    "purchase_date": self.date, "status": "completed", "amount": amount,
                    "description": text}
        if self.endpoint == "transfers":
            # prod-api TransferCreate rejects medium and every payee field we tried, so a
            # transfer records only the sending side; the receiving leg is a tagged deposit.
            return {"transaction_date": self.date, "status": "completed", "amount": amount,
                    "description": text}
        return {"medium": "balance", "transaction_date": self.date, "status": "completed",
                "amount": amount, "description": text}


def build_seed_plan(end: date, days: int = 28, seed: int = 7) -> list[SeedRecord]:
    """Deterministic fictional restaurant activity for ``days`` days ending at ``end``.

    Amounts come from the synthetic restaurant generator, re-dated. Deposit/withdrawal/purchase
    amounts are whole dollars because the Nessie spec types them as integers.
    """
    if days < 7:
        raise ValueError("days must be >= 7 so weekly records exist")
    sample = make_sample("restaurant", days=days, seed=seed)
    start = end - timedelta(days=days - 1)
    plan: list[SeedRecord] = []
    pending_var = 0.0
    for i, row in sample.iterrows():
        d = start + timedelta(days=i)
        ds, wd = d.isoformat(), d.weekday()
        plan.append(SeedRecord("checking", "deposits", "card_sales", ds, ds, float(row.revenue),
                               "Daily card settlement"))
        pending_var += float(row.variable_costs)
        if wd in (0, 3):  # Mon/Thu produce, 70% of accrued food cost
            plan.append(SeedRecord("checking", "purchases", "supplier_produce", ds, ds,
                                   0.7 * pending_var, "Produce delivery",
                                   (("merchant", "produce"),)))
            pending_var *= 0.3
        if wd == 1:       # Tue dry goods, the rest
            plan.append(SeedRecord("checking", "purchases", "supplier_dry_goods", ds, ds,
                                   pending_var, "Dry goods and packaging",
                                   (("merchant", "dry_goods"),)))
            pending_var = 0.0
        if wd == 4:
            plan.append(SeedRecord("checking", "withdrawals", "payroll", ds, ds, 4500.0,
                                   "Weekly payroll"))
        if wd == 5:
            plan.append(SeedRecord("checking", "withdrawals", "petty_cash", ds, ds, 60.0,
                                   "Petty cash"))
        if wd == 0:
            plan.append(SeedRecord("checking", "transfers", "reserve_transfer", ds, ds, 500.0,
                                   "Move to cash reserve", (("payee_account", "savings"),)))
            plan.append(SeedRecord("savings", "deposits", "reserve_transfer_in", ds, ds, 500.0,
                                   "Transfer in from operating checking"))
        if d.day == 1:
            plan.append(SeedRecord("checking", "bills", "rent", ds, ds, 4200.0, "Rent",
                                   (("status", "completed"), ("payee", "Fictional Properties LLC"))))
    rng = np.random.default_rng(seed)
    plan.append(SeedRecord("checking", "bills", "linen", start.isoformat(), start.isoformat(),
                           180.0, "Linen service",
                           (("status", "completed"), ("payee", "Fictional Linen Co"))))
    nxt = end + timedelta(days=5)
    plan.append(SeedRecord("checking", "bills", "utilities", nxt.isoformat(), nxt.isoformat(),
                           float(round(600 + rng.uniform(0, 80), 2)), "Electric and gas",
                           (("status", "pending"), ("payee", "Fictional Power & Gas"))))
    first_next = date(end.year + (end.month == 12), end.month % 12 + 1, 1)
    plan.append(SeedRecord("checking", "bills", "rent", "recurring-template",
                           first_next.isoformat(), 4200.0, "Rent (recurring schedule)",
                           (("status", "recurring"), ("payee", "Fictional Properties LLC"),
                            ("recurring_date", 1))))
    return plan


def _existing_tags(items: list[dict]) -> set[str]:
    found = set()
    for item in items:
        for text in (item.get("description"), item.get("nickname")):
            match = TAG_RE.search(text or "")
            if match:
                found.add(f"{match['kind']} {match['key']}")
    return found


def _find(items: list[dict], **match) -> dict | None:
    return next((i for i in items if all(i.get(k) == v for k, v in match.items())), None)


def initialize(client: NessieClient, plan: list[SeedRecord], *, confirm: bool, log=print) -> dict:
    """Create missing sandbox objects. With confirm=False only reads and reports."""
    summary = {"confirm": confirm, "created": {}, "skipped_existing": {}, "would_create": {},
               "unsupported": {}, "unreadable": {}, "ids": {}}

    def bump(bucket, key, n=1):
        summary[bucket][key] = summary[bucket].get(key, 0) + n

    customer = _find(client.list_customers(), first_name=CUSTOMER["first_name"],
                     last_name=CUSTOMER["last_name"])
    if customer is None:
        if not confirm:
            bump("would_create", "customers")
            log("customer missing; dry run stops here (no account ids to inspect)")
            summary["would_create"]["records"] = len(plan)
            return summary
        client.create_customer(CUSTOMER)
        customer = _find(client.list_customers(), first_name=CUSTOMER["first_name"],
                         last_name=CUSTOMER["last_name"])
        if customer is None:
            raise ConnectorError("customer creation returned success but it is not listed")
        bump("created", "customers")
    else:
        bump("skipped_existing", "customers")
    summary["ids"]["customer"] = customer["_id"]

    account_ids = {}
    for role, body in ACCOUNTS.items():
        acct = _find(client.list_accounts(customer["_id"]), nickname=body["nickname"])
        if acct is None:
            if not confirm:
                bump("would_create", "accounts")
                continue
            client.create_account(customer["_id"], body)
            acct = _find(client.list_accounts(customer["_id"]), nickname=body["nickname"])
            if acct is None:
                raise ConnectorError(f"account '{role}' not listed after creation")
            bump("created", "accounts")
        else:
            bump("skipped_existing", "accounts")
        account_ids[role] = acct["_id"]
    summary["ids"]["accounts"] = account_ids

    merchant_ids = {}
    merchants = client.list_merchants()
    for role, body in MERCHANTS.items():
        merch = _find(merchants, name=body["name"])
        if merch is None and confirm:
            created = client.create_merchant(body)
            merch = created if created and created.get("_id") else _find(
                client.list_merchants(), name=body["name"])
            if merch is None:
                raise ConnectorError(f"merchant '{role}' not listed after creation")
            bump("created", "merchants")
        elif merch is None:
            bump("would_create", "merchants")
            continue
        else:
            bump("skipped_existing", "merchants")
        merchant_ids[role] = merch["_id"]
    summary["ids"]["merchants"] = merchant_ids

    existing, unreadable = {}, {}
    for role, aid in account_ids.items():
        for kind in RECORD_KINDS:
            try:
                existing[(role, kind)] = _existing_tags(client.list_records(aid, kind))
            except ConnectorError as exc:
                unreadable[(role, kind)] = str(exc)
    for rec in plan:
        tag_key = f"{rec.business_kind} {rec.key}"
        if (rec.account, rec.endpoint) in unreadable:
            # Cannot verify what exists, so creating could duplicate: never create blind.
            bump("unreadable", rec.endpoint)
            continue
        if rec.account in account_ids and tag_key in existing[(rec.account, rec.endpoint)]:
            bump("skipped_existing", rec.endpoint)
            continue
        if not confirm or rec.account not in account_ids:
            bump("would_create", rec.endpoint)
            continue
        if rec.endpoint in summary["unsupported"]:
            continue
        needs_merchant = rec.endpoint == "purchases" and not merchant_ids
        if needs_merchant:
            summary["unsupported"][rec.endpoint] = "no merchant available"
            continue
        try:
            client.create_record(account_ids[rec.account], rec.endpoint,
                                 rec.body(merchant_ids, account_ids))
            bump("created", rec.endpoint)
        except ConnectorError as exc:
            # Undocumented endpoints (purchases/transfers) may refuse creation: record and move on.
            summary["unsupported"][rec.endpoint] = str(exc)
            log(f"{rec.endpoint}: creation refused, skipping the rest of this kind: {exc}")
    summary["post_requests"] = client.post_count
    return summary


def fetch_all(client: NessieClient) -> tuple[dict, dict, dict, dict]:
    """GET every record for the demo customer.

    Returns (records, account_ids, merchant_names, unreadable) where ``unreadable`` maps
    "account_id/kind" to the server error for lists the API could not return.
    """
    customer = _find(client.list_customers(), first_name=CUSTOMER["first_name"],
                     last_name=CUSTOMER["last_name"])
    if customer is None:
        raise ConnectorError("Demo customer not found; run `init --confirm` first")
    accounts = client.list_accounts(customer["_id"])
    account_ids = {role: a["_id"] for role, body in ACCOUNTS.items()
                   for a in accounts if a.get("nickname") == body["nickname"]}
    records, unreadable = {}, {}
    for aid in account_ids.values():
        records[aid] = {}
        for kind in RECORD_KINDS:
            try:
                records[aid][kind] = client.list_records(aid, kind)
            except ConnectorError as exc:
                records[aid][kind] = []
                unreadable[f"{aid}/{kind}"] = str(exc)
    merchants = {m["_id"]: m.get("name", "") for m in client.list_merchants()}
    return records, account_ids, merchants, unreadable


def round_trip_check(plan: list[SeedRecord], tx) -> dict:
    """Compare what we asked Nessie to store with what GET returned (dates, amounts, coverage)."""
    by_tag = {}
    for _, row in tx.iterrows():
        match = TAG_RE.search(row["description"] or "")
        if not match or (row["source_record_type"] == "transfer" and row["amount"] > 0):
            continue  # the plan describes the paying (checking) leg of each transfer
        by_tag.setdefault(f"{match['kind']} {match['key']}", row)
    missing, date_mismatch, amount_mismatch = [], [], []
    for rec in plan:
        row = by_tag.get(f"{rec.business_kind} {rec.key}")
        if row is None:
            missing.append(rec.tag)
            continue
        if str(row["posted_date"])[:10] != rec.date:
            date_mismatch.append((rec.tag, rec.date, str(row["posted_date"])[:10]))
        expected = round(rec.amount, 2) if rec.endpoint == "bills" else float(round(rec.amount))
        if abs(abs(row["amount"]) - expected) > 0.005:
            amount_mismatch.append((rec.tag, expected, float(row["amount"])))
    return {"planned": len(plan), "found": len(plan) - len(missing), "missing": missing[:10],
            "n_missing": len(missing), "date_mismatches": date_mismatch[:10],
            "n_date_mismatches": len(date_mismatch), "amount_mismatches": amount_mismatch[:10]}


def main() -> None:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="Nessie SANDBOX seeding/import (synthetic data)")
    parser.add_argument("command", choices=["plan", "init", "import"])
    parser.add_argument("--confirm", action="store_true",
                        help="Actually create missing sandbox records (default: dry run)")
    parser.add_argument("--days", type=int, default=28)
    parser.add_argument("--end", default=None, help="Last seeded date YYYY-MM-DD (default: yesterday)")
    args = parser.parse_args()
    load_dotenv(".env")
    end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)
    plan = build_seed_plan(end, args.days)

    if args.command == "plan":
        counts = {}
        for rec in plan:
            counts[rec.endpoint] = counts.get(rec.endpoint, 0) + 1
        print(f"SYNTHETIC seed plan {plan[0].date}..{end} ({args.days} days): {counts}")
        for rec in plan[:6]:
            print(" ", rec.endpoint, rec.date, rec.amount, rec.tag)
        return

    client = NessieClient()
    if args.command == "init":
        if not args.confirm:
            print("DRY RUN: reading the sandbox only. Re-run with --confirm to create records.")
        summary = initialize(client, plan, confirm=args.confirm)
        print(json.dumps(summary, indent=2))
        if args.confirm:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps({
                "written_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                "seed_end": end.isoformat(), "days": args.days, "summary": summary,
                "plan": [asdict(r) for r in plan]}, indent=2, default=str))
            print(f"state -> {STATE_PATH}")
        return

    records, account_ids, merchants, unreadable = fetch_all(client)
    tx, report = nessie_to_canonical(records, org_account_ids=set(account_ids.values()),
                                     merchants=merchants)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tx_path = OUT_DIR / "nessie_transactions_SYNTHETIC.csv"
    tx.to_csv(tx_path, index=False)
    print("DATA ORIGIN: SYNTHETIC_SANDBOX_NESSIE (fictional restaurant; not real business data)")
    print(json.dumps(asdict(report), indent=2, default=str))
    for where, err in unreadable.items():
        print(f"UNREADABLE {where}: {err.splitlines()[0][:160]}")
    statuses = tx.groupby(["source_record_type", "status"]).size().to_dict()
    print("record types x status:", {f"{k[0]}/{k[1]}": v for k, v in statuses.items()})
    seeded_end = end
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text())
        seeded_end = date.fromisoformat(state["seed_end"])
        plan = build_seed_plan(seeded_end, state["days"])
    print("round trip vs seed plan:", json.dumps(round_trip_check(plan, tx), default=str))
    if tx["counts_as_cash"].any():
        daily = transactions_to_daily(tx)
        daily_path = OUT_DIR / "nessie_daily_cash_basis_SYNTHETIC.csv"
        daily.to_csv(daily_path, index=False)
        print(f"daily cash-basis rows: {len(daily)} -> {daily_path}")
        print(daily[["revenue", "variable_costs", "fixed_costs",
                     "external_transfers_net"]].sum().round(2).to_dict())
        print("NOTE: far fewer than the 504 days the leaderboard needs; this demonstrates the "
              "pipeline, not a trainable history.")
    print(f"transactions -> {tx_path}")


def cli() -> None:
    try:
        main()
    except (ConnectorError, ValueError) as exc:
        raise SystemExit(f"error: {exc}")


if __name__ == "__main__":
    cli()
