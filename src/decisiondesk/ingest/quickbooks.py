"""QuickBooks Online (Intuit Accounting API) connector: OAuth 2.0, paged queries, normalization.

Endpoints (Intuit OpenID discovery documents, checked 2026-11-15):
    authorize  https://appcenter.intuit.com/connect/oauth2
    token      https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer
    revoke     https://developer.api.intuit.com/v2/oauth2/tokens/revoke
    API        https://sandbox-quickbooks.api.intuit.com  (sandbox)
               https://quickbooks.api.intuit.com          (production)
Scope: com.intuit.quickbooks.accounting

Behaviour:
  * Access tokens are refreshed shortly before expiry and once more on HTTP 401. Intuit rotates
    refresh tokens, so the newest one returned is always saved (with its expiry from
    ``x_refresh_token_expires_in``) to a server-side file with owner-only permissions.
  * Queries page with STARTPOSITION / MAXRESULTS (max 1000) until a short page comes back.
  * HTTP 429 (Intuit's limit: 500 requests/minute and 10/second per company) waits and retries;
    5xx retries with backoff; other errors raise ConnectorError without any token in the message.
  * Records become ledger entries (``ledger.py``):
        Invoice, SalesReceipt                      revenue (total minus sales tax)
        RefundReceipt, CreditMemo                  negative revenue
        Bill, Purchase lines (account-based)       cost, classified by the line's account type:
                                                   Cost of Goods Sold -> variable cost;
                                                   Expense / Other Expense -> fixed cost (unless the
                                                   account name matches a variable-cost keyword);
                                                   balance-sheet accounts -> excluded
        Purchase with Credit=true, VendorCredit    negative cost
        Item-based expense lines                   uncategorized (inventory is not an expense until
                                                   sold); reported, never guessed
    Payments and BillPayments are cash movements against invoices/bills and are not counted again.
  * The Profit and Loss report (accrual) for the same period is fetched for reconciliation.
"""
from __future__ import annotations

import base64
import json
import os
import secrets as _secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import pandas as pd
import requests

from ..connectors.base import ConnectorError, redact
from . import ledger as L

AUTHORIZE_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
API_BASE = {"sandbox": "https://sandbox-quickbooks.api.intuit.com", "production": "https://quickbooks.api.intuit.com"}
SCOPE = "com.intuit.quickbooks.accounting"
PAGE = 1000
MAX_RETRIES = 4
REVENUE_ENTITIES = ("Invoice", "SalesReceipt")
NEGATIVE_REVENUE_ENTITIES = ("RefundReceipt", "CreditMemo")
COST_ENTITIES = ("Bill", "Purchase", "VendorCredit")
VARIABLE_KEYWORDS = ("cost of goods", "cogs", "food", "ingredient", "inventory", "supplies", "packaging",
                     "merchant", "card fee", "processing fee", "freight", "shipping", "materials")
BALANCE_SHEET = {"Bank", "Other Current Asset", "Fixed Asset", "Other Asset", "Accounts Receivable",
                 "Accounts Payable", "Credit Card", "Other Current Liability", "Long Term Liability", "Equity"}


def default_token_path() -> Path:
    return Path(os.getenv("QBO_TOKEN_PATH") or "data/private/quickbooks_tokens.json")


@dataclass
class QBOConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    environment: str = "sandbox"
    minor_version: str = "75"
    token_path: Path = None  # type: ignore[assignment]

    @classmethod
    def from_env(cls) -> QBOConfig:
        missing = [k for k in ("QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REDIRECT_URI") if not os.getenv(k)]
        if missing:
            raise ConnectorError(f"QuickBooks is not configured: set {', '.join(missing)} in .env (see docs/INGESTION.md)")
        env = os.getenv("QBO_ENVIRONMENT", "sandbox").lower()
        if env not in API_BASE:
            raise ConnectorError("QBO_ENVIRONMENT must be 'sandbox' or 'production'")
        return cls(os.environ["QBO_CLIENT_ID"], os.environ["QBO_CLIENT_SECRET"], os.environ["QBO_REDIRECT_URI"],
                   env, os.getenv("QBO_MINOR_VERSION", "75"), default_token_path())

    def __post_init__(self):
        self.token_path = Path(self.token_path or default_token_path())


class TokenStore:
    """Tokens live in one server-side JSON file readable only by its owner (never in the UI or logs)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict | None:
        return json.loads(self.path.read_text()) if self.path.exists() else None

    def save(self, tokens: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(tokens))
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        tmp.replace(self.path)


def authorization_url(cfg: QBOConfig, state: str | None = None) -> tuple[str, str]:
    """URL the owner opens to connect a QuickBooks company. Returns (url, state)."""
    state = state or _secrets.token_urlsafe(16)
    q = {"client_id": cfg.client_id, "response_type": "code", "scope": SCOPE, "redirect_uri": cfg.redirect_uri,
         "state": state}
    return f"{AUTHORIZE_URL}?{urlencode(q)}", state


def parse_redirect(url: str, expected_state: str | None = None) -> tuple[str, str]:
    """(code, realm_id) from the URL Intuit redirects to after the owner approves."""
    q = parse_qs(urlparse(url).query)
    if "error" in q:
        raise ConnectorError(f"QuickBooks authorization was not granted: {q['error'][0]}")
    if expected_state is not None and q.get("state", [None])[0] != expected_state:
        raise ConnectorError("QuickBooks authorization state did not match; start the connection again.")
    try:
        return q["code"][0], q["realmId"][0]
    except KeyError:
        raise ConnectorError("The redirect URL has no 'code' and 'realmId'; paste the full URL.") from None


class QuickBooksClient:
    def __init__(self, cfg: QBOConfig, *, session=None, sleep=time.sleep, clock=time.time):
        self.cfg = cfg
        self.http = session or requests.Session()
        self.store = TokenStore(cfg.token_path)
        self.sleep, self.clock = sleep, clock
        self.requests_made = 0

    # ------------------------------------------------------------------ OAuth
    def _basic(self) -> str:
        return "Basic " + base64.b64encode(f"{self.cfg.client_id}:{self.cfg.client_secret}".encode()).decode()

    def _token_request(self, data: dict) -> dict:
        try:
            r = self.http.post(TOKEN_URL, data=data, timeout=30,
                               headers={"Authorization": self._basic(), "Accept": "application/json"})
        except requests.RequestException as exc:
            raise ConnectorError(f"QuickBooks token request failed: {type(exc).__name__}") from None
        body = _json(r)
        if r.status_code != 200:
            err = body.get("error") if isinstance(body, dict) else None
            if err == "invalid_grant":
                raise ConnectorError("QuickBooks authorization has expired or was revoked; connect again.")
            raise ConnectorError(f"QuickBooks token request -> HTTP {r.status_code}: "
                                 f"{redact(str(err or body)[:200], self.cfg.client_secret)}")
        now = self.clock()
        return {"access_token": body["access_token"], "refresh_token": body["refresh_token"],
                "expires_at": now + float(body.get("expires_in", 3600)),
                "refresh_expires_at": now + float(body["x_refresh_token_expires_in"])
                if body.get("x_refresh_token_expires_in") else None}

    def exchange_code(self, code: str, realm_id: str) -> dict:
        tokens = self._token_request({"grant_type": "authorization_code", "code": code,
                                      "redirect_uri": self.cfg.redirect_uri})
        tokens["realm_id"] = realm_id
        tokens["environment"] = self.cfg.environment
        self.store.save(tokens)
        return {"realm_id": realm_id, "connected": True}

    def _tokens(self) -> dict:
        t = self.store.load()
        if not t or not t.get("refresh_token") or not t.get("realm_id"):
            raise ConnectorError("QuickBooks is not connected yet: run "
                                 "`python -m decisiondesk.ingest.quickbooks connect` (see docs/INGESTION.md).")
        if t.get("environment", self.cfg.environment) != self.cfg.environment:
            raise ConnectorError("Stored QuickBooks tokens are for a different environment; connect again.")
        return t

    def refresh(self) -> dict:
        t = self._tokens()
        new = self._token_request({"grant_type": "refresh_token", "refresh_token": t["refresh_token"]})
        t.update(new)  # Intuit rotates refresh tokens: always keep the newest
        self.store.save(t)
        return t

    def status(self) -> dict:
        t = self.store.load()
        if not t:
            return {"connected": False}
        now = self.clock()
        return {"connected": True, "environment": t.get("environment"), "realm_id_last4": str(t.get("realm_id"))[-4:],
                "access_token_valid": t.get("expires_at", 0) > now,
                "refresh_expires_in_days": None if not t.get("refresh_expires_at")
                else round((t["refresh_expires_at"] - now) / 86400, 1)}

    # ------------------------------------------------------------------ HTTP
    def _get(self, path: str, params: dict) -> dict:
        t = self._tokens()
        if t.get("expires_at", 0) - 60 <= self.clock():
            t = self.refresh()
        url = f"{API_BASE[self.cfg.environment]}/v3/company/{t['realm_id']}/{path}"
        params = {**params, "minorversion": self.cfg.minor_version}
        refreshed = False
        for attempt in range(MAX_RETRIES + 1):
            try:
                r = self.http.get(url, params=params, timeout=60,
                                  headers={"Authorization": f"Bearer {t['access_token']}", "Accept": "application/json"})
            except requests.RequestException as exc:
                if attempt < MAX_RETRIES:
                    self.sleep(2 ** attempt)
                    continue
                raise ConnectorError(f"QuickBooks request failed: {type(exc).__name__}") from None
            self.requests_made += 1
            if r.status_code == 401 and not refreshed:
                t, refreshed = self.refresh(), True
                continue
            if r.status_code == 429 and attempt < MAX_RETRIES:
                self.sleep(float(r.headers.get("Retry-After") or 60))  # Intuit advises waiting 60 s
                continue
            if r.status_code >= 500 and attempt < MAX_RETRIES:
                self.sleep(2 ** attempt)
                continue
            body = _json(r)
            if r.status_code != 200:
                raise ConnectorError(f"QuickBooks {path} -> HTTP {r.status_code}: {_fault(body)}")
            return body
        raise ConnectorError(f"QuickBooks {path}: gave up after {MAX_RETRIES} retries (rate limit or server error)")

    def query(self, entity: str, where: str = "") -> list[dict]:
        rows, start = [], 1
        while True:
            q = f"SELECT * FROM {entity}{(' WHERE ' + where) if where else ''} STARTPOSITION {start} MAXRESULTS {PAGE}"
            page = self._get("query", {"query": q}).get("QueryResponse", {}).get(entity, [])
            rows += page
            if len(page) < PAGE:
                return rows
            start += PAGE

    def profit_and_loss(self, start: str, end: str) -> dict:
        return self._get("reports/ProfitAndLoss", {"start_date": start, "end_date": end,
                                                  "accounting_method": "Accrual"})

    def company_currency(self) -> str | None:
        prefs = self._get("preferences", {}).get("Preferences", {})
        return (prefs.get("CurrencyPrefs", {}).get("HomeCurrency") or {}).get("value")

    # ------------------------------------------------------------------ pull
    def pull(self, start: str, end: str, *, existing: pd.DataFrame | None = None,
             variable_keywords: tuple[str, ...] = VARIABLE_KEYWORDS) -> tuple[pd.DataFrame, L.LedgerReport]:
        report = L.LedgerReport(source=f"quickbooks:{self.cfg.environment}")
        where = f"TxnDate >= '{start}' AND TxnDate <= '{end}'"
        try:
            home = self.company_currency()
        except ConnectorError:
            home = None
        accounts = {a["Id"]: a for a in self.query("Account")}
        records = {e: self.query(e, where) for e in (*REVENUE_ENTITIES, *NEGATIVE_REVENUE_ENTITIES, *COST_ENTITIES)}
        entries = normalize(records, accounts, report, environment=self.cfg.environment,
                            variable_keywords=variable_keywords, home_currency=home)
        led = L.merge(existing, L.to_frame(entries), report)
        if home:
            report.currency = home
        try:
            reference = pnl_totals(self.profit_and_loss(start, end))
        except ConnectorError as exc:
            reference = None
            report.warnings.append(f"Profit and Loss report unavailable for reconciliation: {exc}")
        report.n_entries = len(led)
        if reference is not None:
            report.reconciliation = {"reference": reference}
        return led, report


# ---------------------------------------------------------------------- normalization
def _json(r):
    try:
        return r.json()
    except ValueError:
        return {}


def _fault(body) -> str:
    try:
        errs = body.get("Fault", {}).get("Error", [])
        return "; ".join(f"{e.get('Message', '')} {e.get('Detail', '')}".strip() for e in errs)[:300] or "error"
    except AttributeError:
        return "error"


def _money(x) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _classify(acct: dict | None, keywords) -> tuple[str, str | None]:
    if acct is None:
        return "uncategorized", None
    t = acct.get("AccountType")
    name = (acct.get("Name") or "").lower()
    if t == "Cost of Goods Sold":
        return "variable_cost", t
    if t in ("Expense", "Other Expense"):
        return ("variable_cost" if any(k in name for k in keywords) else "fixed_cost"), t
    if t in BALANCE_SHEET:
        return "excluded", t
    if t in ("Income", "Other Income"):
        return "excluded", t  # income accounts on a bill/purchase line: not a cost, reported as excluded
    return "uncategorized", t


def normalize(records: dict[str, list[dict]], accounts: dict[str, dict], report: L.LedgerReport, *,
              environment: str = "sandbox", variable_keywords=VARIABLE_KEYWORDS,
              home_currency: str | None = None) -> list[dict]:
    """QuickBooks entities -> ledger entries. Never fills in a missing amount or date.

    QuickBooks omits CurrencyRef when multicurrency is off; such records are in the company's home
    currency. If the home currency could not be read either, records keep currency "UNKNOWN" (never
    a guessed code) and the report says so."""
    origin = f"QuickBooks Online {environment}"
    src = "quickbooks"
    out, unknown_cur = [], 0
    for entity, rows in records.items():
        for rec in rows:
            report.n_records_read += 1
            rid, date = rec.get("Id"), rec.get("TxnDate")
            cur = (rec.get("CurrencyRef") or {}).get("value") or home_currency
            if not cur:
                cur, unknown_cur = "UNKNOWN", unknown_cur + 1
            if not rid or not date:
                report.skip("missing id or date")
                continue
            sync = rec.get("SyncToken")
            party = ((rec.get("CustomerRef") or rec.get("VendorRef") or rec.get("EntityRef") or {}).get("name"))
            if entity in REVENUE_ENTITIES + NEGATIVE_REVENUE_ENTITIES:
                total = _money(rec.get("TotalAmt"))
                if total is None:
                    report.skip(f"{entity} without TotalAmt")
                    continue
                tax = _money((rec.get("TxnTaxDetail") or {}).get("TotalTax")) or 0.0
                sign = -1 if entity in NEGATIVE_REVENUE_ENTITIES else 1
                out.append(L.entry(src, entity, rid, 0, txn_date=date, amount=sign * (total - tax), currency=cur,
                                   category="revenue", sync_token=sync, counterparty=party,
                                   description=rec.get("DocNumber"), data_origin=origin))
                continue
            negative = entity == "VendorCredit" or (entity == "Purchase" and rec.get("Credit") is True)
            for i, line in enumerate(rec.get("Line") or []):
                amt = _money(line.get("Amount"))
                if amt is None:
                    report.skip(f"{entity} line without Amount")
                    continue
                dt = line.get("DetailType")
                if dt == "AccountBasedExpenseLineDetail":
                    ref = (line.get("AccountBasedExpenseLineDetail") or {}).get("AccountRef") or {}
                    acct = accounts.get(ref.get("value"))
                    cat, atype = _classify(acct, variable_keywords)
                    if acct is None:
                        report.skip("expense line with unknown account")
                    name = (acct or {}).get("Name") or ref.get("name")
                elif dt == "ItemBasedExpenseLineDetail":
                    cat, atype, name, ref = "uncategorized", "Item", ((line.get("ItemBasedExpenseLineDetail") or {})
                                                                     .get("ItemRef") or {}).get("name"), {}
                else:
                    continue  # subtotal / description-only lines carry no separate amount
                out.append(L.entry(src, entity, rid, line.get("Id") or i, txn_date=date,
                                   amount=-amt if negative else amt, currency=cur, category=cat, sync_token=sync,
                                   account_id=ref.get("value"), account_name=name, account_type=atype,
                                   counterparty=party, description=line.get("Description"), data_origin=origin))
    if unknown_cur:
        report.warnings.append(f"{unknown_cur} record(s) have no currency and the company's home currency could not "
                               "be read; they are marked UNKNOWN and left out of totals in another currency.")
    return out


def pnl_totals(report_json: dict) -> dict[str, float]:
    """Total Income and total costs (COGS + Expenses + Other Expenses) from Intuit's ProfitAndLoss
    report JSON. Costs are compared in total because Vizor's variable/fixed split is by keyword
    while the report groups by account type."""
    groups = {}

    def walk(rows):
        for row in rows or []:
            g = row.get("group")
            summ = (row.get("Summary") or {}).get("ColData") or []
            if g and summ:
                val = _money(summ[-1].get("value"))
                if val is not None:
                    groups[g] = val
            walk((row.get("Rows") or {}).get("Row"))
    walk((report_json.get("Rows") or {}).get("Row"))
    return {"revenue": groups.get("Income", 0.0),
            "total_cost": groups.get("COGS", 0.0) + groups.get("Expenses", 0.0) + groups.get("OtherExpenses", 0.0)}


# ---------------------------------------------------------------------- CLI
def main() -> None:  # pragma: no cover - interactive helper
    import argparse

    from dotenv import load_dotenv
    load_dotenv(".env")
    p = argparse.ArgumentParser(description="Vizor QuickBooks Online connector")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("connect", help="authorize a QuickBooks company (sandbox by default)")
    sub.add_parser("status", help="show connection status (no secrets)")
    pull = sub.add_parser("pull", help="download and normalize transactions")
    pull.add_argument("--start", required=True)
    pull.add_argument("--end", required=True)
    args = p.parse_args()
    cfg = QBOConfig.from_env()
    client = QuickBooksClient(cfg)
    if args.cmd == "connect":
        url, state = authorization_url(cfg)
        print("1. Open this URL, sign in with your Intuit developer account and pick the sandbox company:\n")
        print(url)
        redirected = input("\n2. Paste the full URL you were redirected to: ").strip()
        code, realm = parse_redirect(redirected, state)
        print(client.exchange_code(code, realm))
    elif args.cmd == "status":
        print(client.status())
    else:
        led, rep = client.pull(args.start, args.end)
        daily = L.ledger_to_daily(led, rep, currency=rep.currency)
        if rep.reconciliation and "reference" in rep.reconciliation:
            L.reconcile(rep, rep.reconciliation["reference"])
        print(json.dumps({k: v for k, v in rep.to_dict().items()}, indent=1, default=str))
        print(f"{len(daily)} days, revenue {daily['revenue'].sum():,.2f} {rep.currency}")


if __name__ == "__main__":  # pragma: no cover
    main()
