"""Ingestion tests (Milestone 7): QuickBooks Online, Square, transaction files, the shared ledger.

Every HTTP call here is answered by an in-memory fake. These tests show that the connectors
behave correctly against the documented API shapes (paging, token refresh, rate limits,
normalization, de-duplication, reconciliation). They are *functional* tests: they say nothing
about forecast accuracy, and they are not a substitute for a run against a real sandbox company
(see ``test_live_sandbox_*`` at the bottom, skipped unless credentials are configured).
"""
from __future__ import annotations

import json
import os
import stat
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
import pytest

from decisiondesk.connectors.base import ConnectorError
from decisiondesk.ingest import files as F
from decisiondesk.ingest import ledger as L
from decisiondesk.ingest import quickbooks as Q
from decisiondesk.ingest import square as SQ

SECRET = "client-secret-DO-NOT-LEAK"
ACCESS1, ACCESS2 = "access-token-one-SECRET", "access-token-two-SECRET"
REFRESH1, REFRESH2 = "refresh-token-one-SECRET", "refresh-token-two-SECRET"


# ------------------------------------------------------------------------------ fakes
class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeHTTP:
    """Records calls; ``handler(method, url, kwargs)`` returns a Resp."""

    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def _do(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.handler(method, url, kw)

    def get(self, url, **kw):
        return self._do("GET", url, **kw)

    def post(self, url, **kw):
        return self._do("POST", url, **kw)

    def request(self, method, url, **kw):
        return self._do(method, url, **kw)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def qbo_cfg(tmp_path, env="sandbox"):
    return Q.QBOConfig("client-id", SECRET, "https://localhost/callback", env, "75", tmp_path / "tok.json")


def token_body(access, refresh, expires_in=3600):
    return {"access_token": access, "refresh_token": refresh, "expires_in": expires_in,
            "x_refresh_token_expires_in": 8_640_000, "token_type": "bearer"}


ACCOUNTS = [
    {"Id": "1", "Name": "Cost of Goods Sold", "AccountType": "Cost of Goods Sold"},
    {"Id": "2", "Name": "Rent or Lease", "AccountType": "Expense"},
    {"Id": "3", "Name": "Food Supplies", "AccountType": "Expense"},
    {"Id": "4", "Name": "Checking", "AccountType": "Bank"},
    {"Id": "5", "Name": "Utilities", "AccountType": "Expense"},
]
ACCT = {a["Id"]: a for a in ACCOUNTS}


def acct_line(amount, acct_id, line_id="1"):
    return {"Id": line_id, "Amount": amount, "DetailType": "AccountBasedExpenseLineDetail",
            "AccountBasedExpenseLineDetail": {"AccountRef": {"value": acct_id}}}


def sample_records():
    return {
        "Invoice": [{"Id": "101", "SyncToken": "0", "TxnDate": "2026-01-05", "TotalAmt": 108.25,
                     "TxnTaxDetail": {"TotalTax": 8.25}, "CurrencyRef": {"value": "USD"},
                     "CustomerRef": {"name": "Catering client"}}],
        "SalesReceipt": [{"Id": "201", "SyncToken": "2", "TxnDate": "2026-01-05", "TotalAmt": 50.0,
                          "CurrencyRef": {"value": "USD"}}],
        "RefundReceipt": [{"Id": "301", "SyncToken": "0", "TxnDate": "2026-01-05", "TotalAmt": 10.0,
                           "CurrencyRef": {"value": "USD"}}],
        "CreditMemo": [{"Id": "401", "SyncToken": "0", "TxnDate": "2026-01-05", "TotalAmt": 5.0,
                        "CurrencyRef": {"value": "USD"}}],
        "Bill": [{"Id": "501", "SyncToken": "0", "TxnDate": "2026-01-05", "CurrencyRef": {"value": "USD"},
                  "Line": [acct_line(40.0, "1", "1"), acct_line(1000.0, "2", "2"),
                           {"Id": "3", "Amount": 1040.0, "DetailType": "SubTotalLineDetail"}]}],
        "Purchase": [{"Id": "601", "SyncToken": "0", "TxnDate": "2026-01-06", "CurrencyRef": {"value": "USD"},
                      "Line": [acct_line(30.0, "3"),
                               {"Id": "2", "Amount": 70.0, "DetailType": "ItemBasedExpenseLineDetail",
                                "ItemBasedExpenseLineDetail": {"ItemRef": {"name": "Tortillas"}}},
                               acct_line(500.0, "4", "3")]},
                     {"Id": "602", "SyncToken": "0", "TxnDate": "2026-01-06", "Credit": True,
                      "CurrencyRef": {"value": "USD"}, "Line": [acct_line(12.0, "3")]}],
        "VendorCredit": [{"Id": "701", "SyncToken": "0", "TxnDate": "2026-01-06", "CurrencyRef": {"value": "USD"},
                          "Line": [acct_line(8.0, "1")]}],
    }


def pnl_report(income, cogs, expenses, other=None):
    def group(name, value):
        return {"group": name, "Summary": {"ColData": [{"value": f"Total {name}"}, {"value": str(value)}]}}
    rows = [group("Income", income), group("COGS", cogs),
            {"group": "GrossProfit", "Summary": {"ColData": [{"value": "Gross"}, {"value": str(income - cogs)}]}},
            group("Expenses", expenses)]
    if other is not None:
        rows.append(group("OtherExpenses", other))
    return {"Header": {"ReportName": "ProfitAndLoss"}, "Rows": {"Row": rows}}


# ------------------------------------------------------------------------------ QuickBooks: OAuth
def test_authorization_url_and_redirect_parsing(tmp_path):
    url, state = Q.authorization_url(qbo_cfg(tmp_path))
    q = parse_qs(urlparse(url).query)
    assert url.startswith(Q.AUTHORIZE_URL)
    assert q["scope"] == [Q.SCOPE] and q["response_type"] == ["code"] and q["state"] == [state]
    assert SECRET not in url
    code, realm = Q.parse_redirect(f"https://localhost/callback?code=abc&state={state}&realmId=9130", state)
    assert (code, realm) == ("abc", "9130")
    with pytest.raises(ConnectorError, match="state did not match"):
        Q.parse_redirect("https://localhost/callback?code=abc&state=forged&realmId=1", state)
    with pytest.raises(ConnectorError, match="not granted"):
        Q.parse_redirect("https://localhost/callback?error=access_denied", state)


def test_code_exchange_saves_tokens_owner_only_and_status_hides_them(tmp_path):
    http = FakeHTTP(lambda m, u, kw: Resp(200, token_body(ACCESS1, REFRESH1)))
    c = Q.QuickBooksClient(qbo_cfg(tmp_path), session=http, clock=Clock())
    c.exchange_code("auth-code", "123456789")
    _, url, kw = http.calls[0]
    assert url == Q.TOKEN_URL and kw["data"]["grant_type"] == "authorization_code"
    assert kw["headers"]["Authorization"].startswith("Basic ")
    path = tmp_path / "tok.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    st = c.status()
    assert st["connected"] and st["realm_id_last4"] == "6789"
    assert ACCESS1 not in json.dumps(st) and REFRESH1 not in json.dumps(st) and "123456789" not in json.dumps(st)


def test_refresh_rotates_and_persists_newest_refresh_token(tmp_path):
    clock = Clock()
    tokens = iter([token_body(ACCESS1, REFRESH1), token_body(ACCESS2, REFRESH2)])
    seen_auth = []

    def handler(m, u, kw):
        if u == Q.TOKEN_URL:
            return Resp(200, next(tokens))
        seen_auth.append(kw["headers"]["Authorization"])
        return Resp(200, {"QueryResponse": {"Account": []}})
    c = Q.QuickBooksClient(qbo_cfg(tmp_path), session=FakeHTTP(handler), clock=clock)
    c.exchange_code("code", "42")
    clock.t += 3600  # access token expired
    c.query("Account")
    stored = json.loads((tmp_path / "tok.json").read_text())
    assert stored["refresh_token"] == REFRESH2 and stored["access_token"] == ACCESS2
    assert seen_auth == [f"Bearer {ACCESS2}"]


def test_401_triggers_one_refresh_then_succeeds(tmp_path):
    clock = Clock()
    tokens = iter([token_body(ACCESS1, REFRESH1), token_body(ACCESS2, REFRESH2)])
    api = iter([Resp(401, {"Fault": {"Error": [{"Message": "AuthenticationFailed"}]}}),
                Resp(200, {"QueryResponse": {"Invoice": [{"Id": "1"}]}})])

    def handler(m, u, kw):
        return Resp(200, next(tokens)) if u == Q.TOKEN_URL else next(api)
    c = Q.QuickBooksClient(qbo_cfg(tmp_path), session=FakeHTTP(handler), clock=clock)
    c.exchange_code("code", "42")
    assert c.query("Invoice") == [{"Id": "1"}]


def test_expired_refresh_token_asks_to_reconnect_without_leaking(tmp_path):
    clock = Clock()
    answers = iter([Resp(200, token_body(ACCESS1, REFRESH1)), Resp(400, {"error": "invalid_grant"})])
    c = Q.QuickBooksClient(qbo_cfg(tmp_path), session=FakeHTTP(lambda m, u, kw: next(answers)), clock=clock)
    c.exchange_code("code", "42")
    clock.t += 7200
    with pytest.raises(ConnectorError) as exc:
        c.query("Invoice")
    assert "connect again" in str(exc.value)
    for secret in (SECRET, ACCESS1, REFRESH1):
        assert secret not in str(exc.value)


def test_token_endpoint_errors_redact_client_secret(tmp_path):
    http = FakeHTTP(lambda m, u, kw: Resp(500, {"error": f"server echoed {SECRET}"}))
    c = Q.QuickBooksClient(qbo_cfg(tmp_path), session=http, clock=Clock())
    with pytest.raises(ConnectorError) as exc:
        c.exchange_code("code", "42")
    assert SECRET not in str(exc.value)


def test_not_connected_and_environment_mismatch(tmp_path):
    c = Q.QuickBooksClient(qbo_cfg(tmp_path), session=FakeHTTP(lambda *a: Resp()), clock=Clock())
    with pytest.raises(ConnectorError, match="not connected"):
        c.query("Invoice")
    Q.TokenStore(tmp_path / "tok.json").save({"refresh_token": "r", "realm_id": "1", "environment": "sandbox",
                                              "access_token": "a", "expires_at": 2e9})
    prod = Q.QuickBooksClient(qbo_cfg(tmp_path, "production"), session=FakeHTTP(lambda *a: Resp()), clock=Clock())
    with pytest.raises(ConnectorError, match="different environment"):
        prod.query("Invoice")


def test_config_from_env_requires_credentials(monkeypatch):
    for k in ("QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REDIRECT_URI"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ConnectorError, match="QBO_CLIENT_ID"):
        Q.QBOConfig.from_env()


# ------------------------------------------------------------------------------ QuickBooks: HTTP behaviour
def connected_client(tmp_path, api_handler, clock=None):
    clock = clock or Clock()
    Q.TokenStore(tmp_path / "tok.json").save({"access_token": ACCESS1, "refresh_token": REFRESH1, "realm_id": "42",
                                              "environment": "sandbox", "expires_at": clock.t + 3600})
    sleeps = []
    c = Q.QuickBooksClient(qbo_cfg(tmp_path), session=FakeHTTP(api_handler), clock=clock, sleep=sleeps.append)
    return c, sleeps


def test_query_pages_until_a_short_page(tmp_path, monkeypatch):
    monkeypatch.setattr(Q, "PAGE", 2)
    queries = []

    def handler(m, u, kw):
        q = kw["params"]["query"]
        queries.append(q)
        start = int(q.split("STARTPOSITION ")[1].split()[0])
        rows = [{"Id": str(i)} for i in range(start, min(start + 2, 6))]  # 5 records in total
        assert kw["params"]["minorversion"] == "75"
        assert u == "https://sandbox-quickbooks.api.intuit.com/v3/company/42/query"
        return Resp(200, {"QueryResponse": {"Invoice": rows}})
    c, _ = connected_client(tmp_path, handler)
    rows = c.query("Invoice", "TxnDate >= '2026-01-01'")
    assert [r["Id"] for r in rows] == ["1", "2", "3", "4", "5"]
    assert len(queries) == 3 and "MAXRESULTS 2" in queries[0] and "WHERE TxnDate" in queries[0]


def test_rate_limit_waits_then_retries(tmp_path):
    answers = iter([Resp(429, {}, {}), Resp(429, {}, {"Retry-After": "3"}),
                    Resp(200, {"QueryResponse": {"Bill": [{"Id": "9"}]}})])
    c, sleeps = connected_client(tmp_path, lambda m, u, kw: next(answers))
    assert c.query("Bill") == [{"Id": "9"}]
    assert sleeps == [60.0, 3.0]  # Intuit's documented 60 s wait, then the server's Retry-After


def test_server_errors_back_off_then_give_up(tmp_path):
    c, sleeps = connected_client(tmp_path, lambda m, u, kw: Resp(503, {}))
    with pytest.raises(ConnectorError, match="gave up|HTTP 503"):
        c.query("Bill")
    assert sleeps == [1, 2, 4, 8]


def test_api_errors_report_fault_without_tokens(tmp_path):
    body = {"Fault": {"Error": [{"Message": "Invalid query", "Detail": "QueryParserError"}]}}
    c, _ = connected_client(tmp_path, lambda m, u, kw: Resp(400, body))
    with pytest.raises(ConnectorError) as exc:
        c.query("Bill")
    assert "Invalid query" in str(exc.value) and ACCESS1 not in str(exc.value)


# ------------------------------------------------------------------------------ QuickBooks: normalization
def test_normalize_maps_every_entity_correctly():
    rep = L.LedgerReport("quickbooks:sandbox")
    led = L.to_frame(Q.normalize(sample_records(), ACCT, rep, home_currency="USD"))
    by = {(r.source_entity, r.source_id, r.source_line): r for r in led.itertuples()}
    assert by[("Invoice", "101", "0")].amount == pytest.approx(100.0)          # tax excluded
    assert by[("Invoice", "101", "0")].category == "revenue"
    assert by[("SalesReceipt", "201", "0")].amount == 50.0
    assert by[("RefundReceipt", "301", "0")].amount == -10.0                   # refunds reduce revenue
    assert by[("CreditMemo", "401", "0")].amount == -5.0
    assert by[("Bill", "501", "1")].category == "variable_cost"                # COGS account
    assert by[("Bill", "501", "2")].category == "fixed_cost"                   # Rent
    assert ("Bill", "501", "3") not in by                                      # subtotal line skipped
    assert by[("Purchase", "601", "1")].category == "variable_cost"            # "Food Supplies" keyword
    assert by[("Purchase", "601", "2")].category == "uncategorized"            # item line: never guessed
    assert by[("Purchase", "601", "3")].category == "excluded"                 # bank account (transfer)
    assert by[("Purchase", "602", "1")].amount == -12.0                        # credit card refund
    assert by[("VendorCredit", "701", "1")].amount == -8.0
    assert set(led["currency"]) == {"USD"}
    assert by[("Invoice", "101", "0")].counterparty == "Catering client"
    assert all(e.startswith("quickbooks:") for e in led["entry_id"])


def test_missing_values_are_skipped_and_counted_never_filled():
    recs = {"Invoice": [{"Id": "1", "TxnDate": "2026-01-01"},                       # no TotalAmt
                        {"TxnDate": "2026-01-01", "TotalAmt": 5},                    # no Id
                        {"Id": "3", "TotalAmt": 5}],                                 # no date
            "Bill": [{"Id": "4", "TxnDate": "2026-01-01", "Line": [{"DetailType": "AccountBasedExpenseLineDetail"}]}]}
    rep = L.LedgerReport("quickbooks:sandbox")
    assert Q.normalize(recs, ACCT, rep, home_currency="USD") == []
    assert rep.skipped == {"Invoice without TotalAmt": 1, "missing id or date": 2, "Bill line without Amount": 1}


def test_currency_comes_from_record_or_home_currency_never_guessed():
    recs = {"SalesReceipt": [{"Id": "1", "TxnDate": "2026-01-01", "TotalAmt": 10}]}
    rep = L.LedgerReport("q")
    assert Q.normalize(recs, ACCT, rep, home_currency="CAD")[0]["currency"] == "CAD"
    rep2 = L.LedgerReport("q")
    led = L.to_frame(Q.normalize(recs, ACCT, rep2))
    assert led["currency"].iloc[0] == "UNKNOWN" and rep2.warnings
    with pytest.raises(ValueError, match="currency of these records is unknown"):
        L.ledger_to_daily(led, rep2)


def test_mixed_currencies_are_not_added_together():
    recs = {"SalesReceipt": [{"Id": "1", "TxnDate": "2026-01-01", "TotalAmt": 100, "CurrencyRef": {"value": "USD"}},
                             {"Id": "2", "TxnDate": "2026-01-01", "TotalAmt": 900, "CurrencyRef": {"value": "EUR"}}]}
    rep = L.LedgerReport("q")
    daily = L.ledger_to_daily(L.to_frame(Q.normalize(recs, ACCT, rep)), rep, currency="USD")
    assert daily["revenue"].sum() == 100
    assert rep.other_currencies == {"EUR": 1} and any("never converted" in w for w in rep.warnings)


def test_reingesting_is_a_no_op_and_newer_revision_replaces_all_old_lines():
    rep = L.LedgerReport("q")
    first = L.to_frame(Q.normalize(sample_records(), ACCT, rep, home_currency="USD"))
    again = L.merge(first, L.to_frame(Q.normalize(sample_records(), ACCT, L.LedgerReport("q"),
                                                  home_currency="USD")), rep)
    assert len(again) == len(first) and rep.duplicates_dropped == len(first)
    # The bill is edited in QuickBooks: SyncToken 1, rent line removed, COGS line changed.
    edited = {"Bill": [{"Id": "501", "SyncToken": "1", "TxnDate": "2026-01-05", "CurrencyRef": {"value": "USD"},
                        "Line": [acct_line(45.0, "1", "1")]}]}
    rep2 = L.LedgerReport("q")
    merged = L.merge(again, L.to_frame(Q.normalize(edited, ACCT, rep2, home_currency="USD")), rep2)
    bill = merged[merged["source_id"] == "501"]
    assert list(bill["amount"]) == [45.0]          # the deleted rent line is gone, not double counted
    assert rep2.replaced_by_newer_revision == 2


def test_pnl_reconciliation_matches_and_flags_differences():
    rec = sample_records()
    rep = L.LedgerReport("q")
    led = L.to_frame(Q.normalize(rec, ACCT, rep, home_currency="USD"))
    L.ledger_to_daily(led, rep, currency="USD")
    # Ledger: revenue 100 + 50 - 10 - 5 = 135; costs: 40 + 1000 + 30 - 12 - 8 = 1050
    ref = Q.pnl_totals(pnl_report(135.0, 32.0, 1018.0))
    out = L.reconcile(rep, ref)
    assert out["all_match"], out
    rep2 = L.LedgerReport("q")
    L.ledger_to_daily(led, rep2, currency="USD")
    out2 = L.reconcile(rep2, Q.pnl_totals(pnl_report(135.0, 102.0, 1018.0)))  # P&L counts the item line as COGS
    assert not out2["all_match"] and out2["categories"]["total_cost"]["difference"] == -70.0
    assert any("differ" in w for w in rep2.warnings)


def test_pull_end_to_end_with_mocked_api(tmp_path):
    rec = sample_records()

    def handler(m, u, kw):
        if u.endswith("/preferences"):
            return Resp(200, {"Preferences": {"CurrencyPrefs": {"HomeCurrency": {"value": "USD"}}}})
        if u.endswith("/reports/ProfitAndLoss"):
            assert kw["params"]["accounting_method"] == "Accrual"
            return Resp(200, pnl_report(135.0, 32.0, 1018.0))
        entity = kw["params"]["query"].split("FROM ")[1].split()[0]
        rows = ACCOUNTS if entity == "Account" else rec.get(entity, [])
        return Resp(200, {"QueryResponse": {entity: rows}})
    c, _ = connected_client(tmp_path, handler)
    led, rep = c.pull("2026-01-01", "2026-01-31")
    daily = L.ledger_to_daily(led, rep, currency=rep.currency)
    L.reconcile(rep, rep.reconciliation["reference"])
    assert rep.currency == "USD" and rep.reconciliation["all_match"]
    assert list(daily["date"].dt.strftime("%Y-%m-%d")) == ["2026-01-05", "2026-01-06"]
    assert daily["revenue"].tolist() == [135.0, 0.0]
    assert daily["variable_costs"].tolist() == [40.0, 10.0] and daily["fixed_costs"].tolist() == [1000.0, 0.0]
    assert rep.totals["uncategorized"] == 70.0 and rep.totals["excluded"] == 500.0


def test_negative_day_is_refused_not_clipped():
    recs = {"SalesReceipt": [{"Id": "1", "TxnDate": "2026-01-01", "TotalAmt": 10}],
            "RefundReceipt": [{"Id": "2", "TxnDate": "2026-01-02", "TotalAmt": 30}]}
    rep = L.LedgerReport("q")
    with pytest.raises(ValueError, match="more refunds or credits"):
        L.ledger_to_daily(L.to_frame(Q.normalize(recs, ACCT, rep, home_currency="USD")), rep)


# ------------------------------------------------------------------------------ Square
def money(cents, cur="USD"):
    return {"amount": cents, "currency": cur}


def test_square_sales_net_of_tax_and_tips_refunds_scaled_and_local_day():
    orders = [{"id": "o1", "state": "COMPLETED", "closed_at": "2026-03-02T03:30:00Z",  # 21:30 Mar 1 in Chicago
               "total_money": money(11800), "total_tax_money": money(800), "total_tip_money": money(1000),
               "line_items": [{"quantity": "2"}]},
              {"id": "o2", "state": "COMPLETED", "closed_at": "2026-03-02T18:00:00Z", "total_money": money(5000)},
              {"id": "o3", "state": "OPEN", "closed_at": "2026-03-02T18:00:00Z", "total_money": money(999)}]
    refunds = [{"id": "r1", "status": "COMPLETED", "order_id": "o1", "created_at": "2026-03-02T19:00:00Z",
                "amount_money": money(11800)},
               {"id": "r2", "status": "COMPLETED", "order_id": "elsewhere", "created_at": "2026-03-02T19:00:00Z",
                "amount_money": money(500)},
               {"id": "r3", "status": "PENDING", "order_id": "o2", "created_at": "2026-03-02T19:00:00Z",
                "amount_money": money(5000)}]
    rep = L.LedgerReport("square:sandbox")
    led = L.to_frame(SQ.normalize(orders, refunds, rep, tz="America/Chicago"))
    e = {r.entry_id: r for r in led.itertuples()}
    assert e["square:Order:o1:0"].amount == 100.0                              # 118 - 8 tax - 10 tip
    assert str(e["square:Order:o1:0"].txn_date.date()) == "2026-03-01"         # local business day
    assert e["square:Refund:r1:0"].amount == pytest.approx(-100.0)             # refund on the same net basis
    assert e["square:Refund:r2:0"].amount == -5.0                              # unscaled, and reported
    assert rep.skipped == {"order not completed": 1, "refund not completed": 1}
    assert any("full amount" in w for w in rep.warnings)


def test_square_pull_pages_and_reports_sales_only():
    pages = {None: {"orders": [{"id": "a", "state": "COMPLETED", "closed_at": "2026-03-01T15:00:00Z",
                                "total_money": money(1000)}], "cursor": "c2"},
             "c2": {"orders": [{"id": "b", "state": "COMPLETED", "closed_at": "2026-03-01T16:00:00Z",
                                "total_money": money(2000)}]}}

    def handler(m, u, kw):
        assert kw["headers"]["Square-Version"]
        if u.endswith("/v2/orders/search"):
            return Resp(200, pages[kw["json"].get("cursor")])
        if u.endswith("/v2/refunds"):
            return Resp(200, {"refunds": []})
        return Resp(200, {"location": {"timezone": "America/New_York"}})
    c = SQ.SquareClient(SQ.SquareConfig("sq-token-SECRET", "L1"), session=FakeHTTP(handler), sleep=lambda s: None)
    led, rep = c.pull("2026-03-01", "2026-03-31")
    assert led["amount"].sum() == 30.0
    assert any("sales only" in w for w in rep.warnings)
    daily = L.ledger_to_daily(led, rep)
    assert daily["variable_costs"].sum() == 0 and daily["fixed_costs"].sum() == 0   # no costs invented


def test_square_401_message_has_no_token():
    c = SQ.SquareClient(SQ.SquareConfig("sq-token-SECRET", "L1"), session=FakeHTTP(lambda *a: Resp(401, {})),
                        sleep=lambda s: None)
    with pytest.raises(ConnectorError) as exc:
        c.timezone()
    assert "sq-token-SECRET" not in str(exc.value)


def test_square_minor_units():
    assert SQ._amount(money(1234))[0] == 12.34
    assert SQ._amount(money(1234, "JPY"))[0] == 1234


# ------------------------------------------------------------------------------ transaction files
def bank_csv(sep=","):
    return (f"Date{sep}Description{sep}Category{sep}Amount\n"
            f"01/02/2026{sep}Square payout{sep}Sales{sep}1200.00\n"
            f"01/02/2026{sep}Sysco{sep}Food{sep}-300.00\n"
            f"01/02/2026{sep}Sysco{sep}Food{sep}-300.00\n"
            f"01/03/2026{sep}Landlord{sep}Rent{sep}(2000.00)\n"
            f"01/03/2026{sep}Transfer to savings{sep}Transfer{sep}-500\n"
            f"01/04/2026{sep}Square payout{sep}Sales{sep}\"1,500.00\"\n").encode()


def test_file_import_with_mapping_and_owner_confirmed_categories():
    df = F.read_table(bank_csv(), "bank.csv")
    m = F.FileMapping(**{k: v for k, v in F.suggest_mapping(df.columns).items() if k != "money_in" and k != "money_out"},
                      date_format="%m/%d/%Y")
    assert (m.date, m.amount, m.category) == ("Date", "Amount", "Category")
    labels = F.label_directions(df, m)
    sug = {f"{r.label}|{r.direction}": r.suggested for r in labels.itertuples()}
    assert sug == {"Sales|in": "revenue", "Food|out": "variable_cost", "Rent|out": "fixed_cost",
                   "Transfer|out": "excluded"}
    imp = F.import_transactions(df, m, sug, filename="bank.csv")
    assert len(imp.ledger) == 6                       # the two identical Sysco rows are both real
    daily = L.ledger_to_daily(imp.ledger, imp.report, currency="USD")
    assert daily["revenue"].sum() == 2700 and daily["variable_costs"].sum() == 600
    assert daily["fixed_costs"].sum() == 2000 and imp.report.totals["excluded"] == 500
    # Re-uploading the same export adds nothing.
    again = F.import_transactions(df, m, sug, filename="bank.csv", existing=imp.ledger)
    assert len(again.ledger) == 6 and again.report.duplicates_dropped == 6


def test_unconfirmed_labels_stay_uncategorized():
    df = F.read_table(bank_csv(), "bank.csv")
    m = F.FileMapping(date="Date", amount="Amount", category="Category")
    imp = F.import_transactions(df, m, {"Sales|in": "revenue"}, filename="bank.csv")
    L.ledger_to_daily(imp.ledger, imp.report, currency="USD")
    assert imp.report.totals["uncategorized"] == 3100.0
    assert any("could not be classified" in w for w in imp.report.warnings)


def test_debit_credit_columns_and_semicolon_csv():
    data = b"Posted;Memo;Debit;Credit\n2026-01-02;Deposit;;900\n2026-01-02;Rent;1000;\n"
    df = F.read_table(data, "export.csv")
    m = F.FileMapping(date="Posted", money_in="Credit", money_out="Debit", description="Memo")
    assert F.signed_amounts(df, m).tolist() == [900.0, -1000.0]
    imp = F.import_transactions(df, m, {"Deposit|in": "revenue", "Rent|out": "fixed_cost"}, filename="export.csv")
    daily = L.ledger_to_daily(imp.ledger, imp.report)
    assert daily.iloc[0][["revenue", "fixed_costs"]].tolist() == [900.0, 1000.0]


def test_file_errors_name_the_column_and_rows():
    df = pd.DataFrame({"Date": ["2026-01-01", "not a date", "2026-01-03"], "Amount": ["5", "x", "7"]})
    with pytest.raises(ValueError, match=r"'Amount'.*rows 3"):
        F.signed_amounts(df, F.FileMapping(date="Date", amount="Amount"))
    with pytest.raises(ValueError, match=r"'Date'.*rows 3"):
        F._parse_dates(df["Date"], "Date", "%Y-%m-%d")
    with pytest.raises(ValueError, match="amount column"):
        F.FileMapping(date="Date").validate(df.columns)
    with pytest.raises(ValueError, match="not in the file"):
        F.FileMapping(date="Date", amount="Total").validate(df.columns)
    with pytest.raises(ValueError, match=".csv or .xlsx"):
        F.read_table(b"x", "data.pdf")
    with pytest.raises(ValueError, match="no rows"):
        F.read_table(b"Date,Amount\n", "empty.csv")


def test_excel_files_are_read(tmp_path):
    pytest.importorskip("openpyxl")
    p = tmp_path / "t.xlsx"
    pd.DataFrame({"Date": ["2026-01-01"], "Amount": [10.5]}).to_excel(p, index=False)
    assert F.read_table(p.read_bytes(), "t.xlsx")["Amount"].iloc[0] == 10.5


# ------------------------------------------------------------------------------ into the engine
def long_file(days):
    rng = np.random.default_rng(3)
    rows = []
    for d in pd.date_range("2025-01-01", periods=days):
        rows.append({"Date": d.date().isoformat(), "Amount": round(900 + rng.normal(0, 60), 2), "Category": "Sales"})
        rows.append({"Date": d.date().isoformat(), "Amount": -round(320 + rng.normal(0, 20), 2), "Category": "Food"})
        if d.day == 1:
            rows.append({"Date": d.date().isoformat(), "Amount": -3000.0, "Category": "Rent"})
    return pd.DataFrame(rows)


def ledger_session(days, **kw):
    from decisiondesk.agent.tools import BusinessSession
    df = long_file(days)
    m = F.FileMapping(date="Date", amount="Amount", category="Category")
    imp = F.import_transactions(df, m, {"Sales|in": "revenue", "Food|out": "variable_cost", "Rent|out": "fixed_cost"},
                                filename="t.csv")
    daily = L.ledger_to_daily(imp.ledger, imp.report, currency="USD")
    return BusinessSession.from_ledger(daily, imp.report, business_name="Test Cafe", data_label="UPLOADED TRANSACTIONS",
                                       models=("seasonal_naive", "weekday_mean_12w", "ridge"), **kw)


def test_short_history_refuses_forecasts_but_reports_records(tmp_path):
    from decisiondesk.agent.tools import run_tool
    s = ledger_session(120, leaderboard_cache_dir=None)
    k = run_tool(s, "get_key_numbers", {})
    assert k["data_label"] == "UPLOADED TRANSACTIONS"
    assert k["cash"]["balance"] is None                       # no opening balance -> no invented cash
    assert run_tool(s, "assess_cash_risk", {"starting_cash": None})["available"] is False
    with pytest.raises(ValueError, match="504"):
        run_tool(s, "forecast_business", {"target": "revenue"})
    assert s.metadata["ingestion"]["source"] == "file:t.csv"


def test_uploaded_data_never_writes_model_cache_to_disk(tmp_path, monkeypatch):
    import decisiondesk.leaderboard as LB
    monkeypatch.setattr(LB, "CACHE_DIR", tmp_path / "lb")
    s = ledger_session(560)
    s.board("revenue")
    assert not (tmp_path / "lb").exists()


def test_app_shows_provenance_and_missing_data_warnings(monkeypatch):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    from decisiondesk.agent.agent import DecisionAgent
    monkeypatch.setenv("MSRD_FAST", "1")
    s = ledger_session(120)
    app = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")
    at = AppTest.from_file(app, default_timeout=180)
    at.session_state["session"], at.session_state["agent"] = s, DecisionAgent(s)
    at.session_state["business_key"] = "tx:t.csv"
    at.session_state["nav"], at.session_state["seg_mb_section"] = "My Business", "Outlook"
    at.run()
    assert not at.exception
    header = " ".join(m.value for m in at.markdown if "brand-name" in m.value)
    assert "Your data · uploaded transactions" in header and "Sample Data" not in header
    assert any("Not enough history to forecast" in w.value for w in at.warning)


# ------------------------------------------------------------------------------ live sandboxes (opt-in)
@pytest.mark.skipif(not (os.getenv("QBO_CLIENT_ID") and os.getenv("QBO_LIVE_TEST") == "1"),
                    reason="set QBO_* credentials, connect once, and QBO_LIVE_TEST=1 to run against the sandbox")
def test_live_sandbox_quickbooks_pull():  # pragma: no cover - needs network + credentials
    c = Q.QuickBooksClient(Q.QBOConfig.from_env())
    led, rep = c.pull("2024-01-01", pd.Timestamp.today().strftime("%Y-%m-%d"))
    assert rep.n_records_read > 0 and len(led) > 0


@pytest.mark.skipif(not (os.getenv("SQUARE_ACCESS_TOKEN") and os.getenv("SQUARE_LIVE_TEST") == "1"),
                    reason="set SQUARE_* credentials and SQUARE_LIVE_TEST=1 to run against the sandbox")
def test_live_sandbox_square_pull():  # pragma: no cover
    c = SQ.SquareClient(SQ.SquareConfig.from_env())
    led, rep = c.pull("2024-01-01", pd.Timestamp.today().strftime("%Y-%m-%d"))
    assert rep.source.startswith("square:") and len(led) == rep.n_entries


def test_app_quickbooks_import_switches_business_and_shows_provenance(tmp_path, monkeypatch):
    """Clicking Import in the QuickBooks tab (pull mocked) must load the data, go to the Overview and
    label it as sandbox data. Regression: changing the page after the navigation widget was drawn
    raised StreamlitAPIException in a real browser."""
    from pathlib import Path

    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("MSRD_FAST", "1")
    monkeypatch.setenv("QBO_CLIENT_ID", "id")
    monkeypatch.setenv("QBO_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("QBO_REDIRECT_URI", "https://localhost/cb")
    monkeypatch.setenv("QBO_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("QBO_TOKEN_PATH", str(tmp_path / "tok.json"))
    Q.TokenStore(tmp_path / "tok.json").save({"access_token": ACCESS1, "refresh_token": REFRESH1, "realm_id": "98765",
                                              "environment": "sandbox", "expires_at": 4e9, "refresh_expires_at": 4e9})
    df = long_file(90)
    imp = F.import_transactions(df, F.FileMapping(date="Date", amount="Amount", category="Category"),
                                {"Sales|in": "revenue", "Food|out": "variable_cost", "Rent|out": "fixed_cost"},
                                filename="qbo.csv")

    def fake_pull(self, start, end, existing=None):
        rep = L.LedgerReport("quickbooks:sandbox", n_records_read=len(imp.ledger), n_entries=len(imp.ledger))
        rep.currency = "USD"
        rev = float(imp.ledger.loc[imp.ledger.category == "revenue", "amount"].sum())
        rep.reconciliation = {"reference": {"revenue": rev * 1.05}}   # P&L 5% higher: must be flagged
        return imp.ledger, rep
    monkeypatch.setattr(Q.QuickBooksClient, "pull", fake_pull)
    app = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")
    at = AppTest.from_file(app, default_timeout=180)
    at.session_state["view"], at.session_state["seg_settings"] = "settings", "Data"
    at.run()
    page = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert "…8765" in page and "98765" not in page and ACCESS1 not in page
    at.button(key="qbo_import").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["nav"] == "Overview" and at.session_state["view"] is None
    header = " ".join(m.value for m in at.markdown if "brand-name" in m.value)
    assert "QuickBooks sandbox · test company" in header
    rep = at.session_state["ingest_report"]
    assert rep["reconciliation"]["all_match"] is False and any("differ" in w for w in rep["warnings"])
