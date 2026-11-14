"""Offline tests for the Nessie sandbox client, seeding idempotency and transaction conversion."""
import itertools
import re
from datetime import date
from typing import ClassVar

import pandas as pd
import pytest

from decisiondesk.connectors.base import ConnectorError
from decisiondesk.connectors.nessie import NessieClient
from decisiondesk.nessie_sandbox import (
    ACCOUNTS,
    build_seed_plan,
    fetch_all,
    initialize,
    round_trip_check,
)
from decisiondesk.transactions import nessie_to_canonical, transactions_to_daily

KEY = "secret-test-key-123"


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = str(body)

    def json(self):
        return self._body


class FakeNessie:
    """In-memory imitation of behaviour observed on prod-api.nessieisreal.com (2026-11-14):
    strict create schemas (extra fields -> 400), transfers without a payee field, an empty
    transfer list as 404, withdrawals not echoed, and optionally a bills list that 400s."""

    ALLOWED: ClassVar[dict[str, set[str]]] = {
        "purchases": {"merchant_id", "medium", "purchase_date", "status", "amount", "description"},
        "transfers": {"transaction_date", "status", "amount", "description"},
    }

    def __init__(self, refuse=(), unreadable=()):
        self.ids = (f"{i:024x}" for i in itertools.count(1))
        self.customers, self.accounts, self.merchants = [], [], []
        self.records = {k: [] for k in ("deposits", "withdrawals", "purchases", "transfers", "bills")}
        self.refuse = set(refuse)
        self.unreadable = set(unreadable)
        self.transfer_owner = {}
        self.posts = 0

    def request(self, method, url, params=None, json=None, timeout=None):
        assert params and params.get("key") == KEY
        path = re.sub(r"^https?://[^/]+", "", url)
        if method == "POST":
            self.posts += 1
            return self._post(path, json)
        return self._get(path)

    def _created(self, obj):
        return FakeResponse(201, {"code": 201, "message": "Created", "objectCreated": obj})

    def _post(self, path, body):
        if path == "/customers":
            obj = {"_id": next(self.ids), **body}
            self.customers.append(obj)
            return self._created(obj)
        if path == "/merchants":
            obj = {"_id": next(self.ids), **body}
            self.merchants.append(obj)
            return self._created(obj)
        m = re.fullmatch(r"/customers/(\w+)/accounts", path)
        if m:
            obj = {"_id": next(self.ids), "customer_id": m[1], **body}
            self.accounts.append(obj)
            return self._created(obj)
        m = re.fullmatch(r"/accounts/(\w+)/(\w+)", path)
        kind = m[2]
        if kind in self.refuse:
            return FakeResponse(404, {"message": "Not Found"})
        extra = set(body) - self.ALLOWED.get(kind, set(body))
        if extra:
            return FakeResponse(400, f"validation errors: {sorted(extra)} extra fields not permitted")
        obj = {"_id": next(self.ids), **body}
        if kind == "transfers":
            # Observed: transfers are returned with "id" (not "_id") and no payer/payee.
            obj = {"id": obj.pop("_id"), **body}
            self.transfer_owner[obj["id"]] = m[1]
        elif kind == "bills":
            obj["account_id"] = m[1]
        elif kind == "purchases":
            obj["payer_id"] = m[1]
        else:
            obj["payee_id" if kind == "deposits" else "payer_id"] = m[1]
        self.records[kind].append(obj)
        if kind == "withdrawals":
            return FakeResponse(201, "Withdrawal created")  # spec: no object echoed
        return self._created(obj)

    def _get(self, path):
        if path == "/customers":
            return FakeResponse(200, list(self.customers))
        if path == "/merchants":
            return FakeResponse(200, list(self.merchants))
        m = re.fullmatch(r"/customers/(\w+)/accounts", path)
        if m:
            return FakeResponse(200, [a for a in self.accounts if a["customer_id"] == m[1]])
        m = re.fullmatch(r"/accounts/(\w+)/(\w+)", path)
        aid, kind = m[1], m[2]
        if kind == "transfers":
            items = [t for t in self.records[kind] if self.transfer_owner[t["id"]] == aid]
            if not items:
                return FakeResponse(404, "No transfers found for this account")
            return FakeResponse(200, items)
        if kind in self.unreadable:
            return FakeResponse(400, "2 validation errors for Bill\nrecurring_date\n  field required")
        owner = {"deposits": "payee_id", "bills": "account_id"}.get(kind, "payer_id")
        return FakeResponse(200, [r for r in self.records[kind] if r.get(owner) == aid])


def client_for(fake):
    return NessieClient(api_key=KEY, base_url="https://fake.test", session=fake,
                        throttle_seconds=0)


PLAN = build_seed_plan(date(2026, 11, 13), days=28)


def test_seed_plan_is_deterministic_with_unique_tags():
    again = build_seed_plan(date(2026, 11, 13), days=28)
    assert PLAN == again
    tags = [r.tag for r in PLAN]
    assert len(tags) == len(set(tags))
    kinds = {r.endpoint for r in PLAN}
    assert kinds == {"deposits", "withdrawals", "purchases", "transfers", "bills"}
    integer_kinds = [r for r in PLAN if r.endpoint != "bills"]
    body = integer_kinds[0].body({"produce": "m1", "dry_goods": "m2"},
                                 {"checking": "a1", "savings": "a2"})
    assert isinstance(body["amount"], int)


def test_dry_run_never_posts():
    fake = FakeNessie()
    summary = initialize(client_for(fake), PLAN, confirm=False, log=lambda *_: None)
    assert fake.posts == 0
    assert summary["would_create"]["customers"] == 1


def test_init_is_idempotent():
    fake = FakeNessie()
    first = initialize(client_for(fake), PLAN, confirm=True, log=lambda *_: None)
    posts_after_first = fake.posts
    assert first["created"]["deposits"] == 28 + 4  # card sales + savings transfer legs
    assert first["created"]["accounts"] == 2
    second = initialize(client_for(fake), PLAN, confirm=True, log=lambda *_: None)
    assert fake.posts == posts_after_first, "second run must not create anything"
    assert second["created"] == {}
    assert second["skipped_existing"]["deposits"] == 32
    # Dry run against a populated sandbox reports nothing to create.
    third = initialize(client_for(fake), PLAN, confirm=False, log=lambda *_: None)
    assert third["would_create"] == {}


def test_refused_endpoint_is_reported_not_fatal():
    fake = FakeNessie(refuse={"purchases"})
    summary = initialize(client_for(fake), PLAN, confirm=True, log=lambda *_: None)
    assert "purchases" in summary["unsupported"]
    assert KEY not in summary["unsupported"]["purchases"]
    assert summary["created"]["deposits"] == 32


def test_import_signs_transfers_and_round_trip():
    fake = FakeNessie()
    initialize(client_for(fake), PLAN, confirm=True, log=lambda *_: None)
    records, account_ids, merchants, unreadable = fetch_all(client_for(fake))
    assert unreadable == {}
    tx, report = nessie_to_canonical(records, org_account_ids=set(account_ids.values()),
                                     merchants=merchants)
    assert (tx.loc[tx.source_record_type == "deposit", "amount"] > 0).all()
    for kind in ("withdrawal", "purchase", "bill", "transfer"):
        assert (tx.loc[tx.source_record_type == kind, "amount"] < 0).all()
    # Internal transfer = outgoing transfer on checking + tagged deposit on savings.
    internal = tx[tx.is_internal_transfer]
    assert len(internal) == 2 * 4
    assert set(internal.source_record_type) == {"transfer", "deposit"}
    assert internal.amount.sum() == 0  # legs net out across the organization
    assert (internal.category == "internal_transfer").all()
    check = round_trip_check(PLAN, tx)
    assert check["n_missing"] == 0 and check["n_date_mismatches"] == 0
    assert not check["amount_mismatches"]
    assert report.uncategorized_rows == 0
    assert (tx.data_origin == "SYNTHETIC_SANDBOX_NESSIE").all()

    daily = transactions_to_daily(tx)
    deposits = tx[(tx.category == "revenue") & tx.counts_as_cash].amount.sum()
    assert daily.revenue.sum() == pytest.approx(deposits)
    assert daily.external_transfers_net.sum() == 0  # internal transfers excluded


def test_duplicates_and_bill_status_handling():
    acct = "a" * 24
    dep = {"_id": "d1", "transaction_date": "2026-11-01", "status": "completed", "amount": 100,
           "description": "sale [MSRD-DEMO v1 card_sales 2026-11-01]", "medium": "balance"}
    records = {acct: {
        "deposits": [dep, dict(dep)],  # API returned the same record twice
        "bills": [
            {"_id": "b1", "status": "completed", "payee": "X", "payment_amount": 50.5,
             "payment_date": "2026-11-02", "nickname": "rent"},
            {"_id": "b2", "status": "recurring", "payee": "X", "payment_amount": 50.5,
             "payment_date": "2026-12-01", "nickname": "rent schedule"},
            {"_id": "b3", "status": "cancelled", "payee": "Y", "payment_amount": 9,
             "payment_date": "2026-11-03", "nickname": "cancelled"},
        ],
        "transfers": [{"_id": "t1", "payer_id": acct, "payee_id": "external" + "0" * 16,
                       "amount": 30, "transaction_date": "2026-11-02", "status": "completed"}],
    }}
    tx, report = nessie_to_canonical(records, org_account_ids={acct})
    assert report.duplicates_dropped == 1
    assert tx.counts_as_cash.sum() == 3  # deposit, completed bill, transfer
    ext = tx[tx.source_record_type == "transfer"].iloc[0]
    assert ext.amount == -30 and not ext.is_internal_transfer
    assert ext.category == "external_transfer"
    daily = transactions_to_daily(tx)
    assert daily.revenue.sum() == 100 and daily.fixed_costs.sum() == pytest.approx(50.5)
    assert daily.external_transfers_net.sum() == -30
    assert len(daily) == 2  # 2026-11-01..02; non-cash rows don't extend the range


def test_errors_never_leak_key():
    class Boom:
        def request(self, method, url, params=None, json=None, timeout=None):
            return FakeResponse(500, f"internal error for key={params['key']}")

    client = NessieClient(api_key=KEY, base_url="https://fake.test", session=Boom(),
                          throttle_seconds=0)
    with pytest.raises(ConnectorError) as err:
        client.list_customers()
    assert KEY not in str(err.value) and "HTTP 500" in str(err.value)


def test_missing_key_is_clear(monkeypatch):
    monkeypatch.delenv("NESSIE_API_KEY", raising=False)
    with pytest.raises(ConnectorError, match="not configured"):
        NessieClient(api_key="")


def test_accounts_config_has_integer_balances():
    for body in ACCOUNTS.values():
        assert isinstance(body["balance"], int) and isinstance(body["rewards"], int)
    assert pd.Series([b["type"] for b in ACCOUNTS.values()]).isin(
        ["Checking", "Savings", "Credit Card"]).all()


def test_unreadable_list_blocks_blind_creation_and_is_reported():
    fake = FakeNessie(unreadable={"bills"})
    first = initialize(client_for(fake), PLAN, confirm=True, log=lambda *_: None)
    assert "bills" not in first["created"] and first["unreadable"]["bills"] == 4
    assert fake.records["bills"] == []  # nothing created when existence cannot be checked
    posts = fake.posts
    second = initialize(client_for(fake), PLAN, confirm=True, log=lambda *_: None)
    assert fake.posts == posts and second["created"] == {}
    _, _, _, unreadable = fetch_all(client_for(fake))
    assert any(k.endswith("/bills") for k in unreadable)
    assert all(KEY not in v for v in unreadable.values())


def test_request_bodies_match_observed_prod_schema():
    ids = ({"produce": "m1", "dry_goods": "m2"}, {"checking": "a1", "savings": "a2"})
    for rec in PLAN:
        body = rec.body(*ids)
        allowed = FakeNessie.ALLOWED.get(rec.endpoint)
        if allowed:
            assert set(body) <= allowed, (rec.endpoint, set(body) - allowed)


def test_records_without_underscore_id_are_not_merged():
    acct = "c" * 24
    base = {"transaction_date": "2026-11-02", "status": "completed", "amount": 500}
    records = {acct: {"transfers": [
        {"id": "t-1", **base, "description": "a"},
        {"id": "t-2", **base, "description": "b"},
        {**base, "description": "no id at all"},
        {**base, "description": "no id at all"},  # exact repeat: a true duplicate
    ]}}
    tx, report = nessie_to_canonical(records, org_account_ids={acct})
    assert len(tx) == 3 and report.duplicates_dropped == 1
    assert {"t-1", "t-2"} <= set(tx.source_record_id)
    assert (tx.amount == -500).all()
