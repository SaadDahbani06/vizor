"""Square connector (Orders + Refunds APIs): sales only.

    POST {base}/v2/orders/search   completed orders closed in the period (cursor paging)
    GET  {base}/v2/refunds         completed payment refunds in the period (cursor paging)
    base: https://connect.squareupsandbox.com (sandbox) | https://connect.squareup.com (production)

Sales are recorded **net of sales tax and tips** (tax is owed to the state, tips belong to staff):
    net sale = total_money - total_tax_money - total_tip_money   (on the order's closed_at date)
Refunds are negative revenue on the refund date. A refund's amount includes tax and tip, so when
its order was downloaded the refund is scaled to the same net basis; otherwise the full refunded
amount is used and the report says so.

Square has no cost data. Gross or net **sales are not profit**: profit, margin and cash analytics
need costs from another source (QuickBooks, an accounting export or a cost file); until then the
app shows them as unavailable.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import pandas as pd
import requests

from ..connectors.base import ConnectorError
from . import ledger as L

BASE = {"sandbox": "https://connect.squareupsandbox.com", "production": "https://connect.squareup.com"}
MINOR_UNITS = {"JPY": 0, "KRW": 0}  # every other supported currency uses 2 decimal places
MAX_RETRIES = 4


@dataclass
class SquareConfig:
    access_token: str
    location_id: str
    environment: str = "sandbox"
    version: str = "2025-01-23"

    @classmethod
    def from_env(cls) -> SquareConfig:
        missing = [k for k in ("SQUARE_ACCESS_TOKEN", "SQUARE_LOCATION_ID") if not os.getenv(k)]
        if missing:
            raise ConnectorError(f"Square is not configured: set {', '.join(missing)} in .env (see docs/INGESTION.md)")
        env = os.getenv("SQUARE_ENVIRONMENT", "sandbox").lower()
        if env not in BASE:
            raise ConnectorError("SQUARE_ENVIRONMENT must be 'sandbox' or 'production'")
        return cls(os.environ["SQUARE_ACCESS_TOKEN"], os.environ["SQUARE_LOCATION_ID"], env,
                   os.getenv("SQUARE_VERSION", "2025-01-23"))


def _amount(money: dict | None) -> tuple[float | None, str | None]:
    if not money or money.get("amount") is None:
        return None, None
    cur = money.get("currency") or "UNKNOWN"  # Square always sends one; never guess
    return money["amount"] / (10 ** MINOR_UNITS.get(cur, 2)), cur


class SquareClient:
    def __init__(self, cfg: SquareConfig, *, session=None, sleep=time.sleep):
        self.cfg, self.http, self.sleep = cfg, session or requests.Session(), sleep

    def _call(self, method: str, path: str, *, params=None, body=None) -> dict:
        url = BASE[self.cfg.environment] + path
        headers = {"Authorization": f"Bearer {self.cfg.access_token}", "Square-Version": self.cfg.version,
                   "Content-Type": "application/json"}
        for attempt in range(MAX_RETRIES + 1):
            try:
                r = self.http.request(method, url, params=params, json=body, headers=headers, timeout=60)
            except requests.RequestException as exc:
                if attempt < MAX_RETRIES:
                    self.sleep(2 ** attempt)
                    continue
                raise ConnectorError(f"Square request failed: {type(exc).__name__}") from None
            if r.status_code == 429 and attempt < MAX_RETRIES:
                self.sleep(float(r.headers.get("Retry-After") or 2 ** attempt))
                continue
            if r.status_code >= 500 and attempt < MAX_RETRIES:
                self.sleep(2 ** attempt)
                continue
            try:
                data = r.json()
            except ValueError:
                data = {}
            if r.status_code == 401:
                raise ConnectorError("Square rejected the access token (401); check SQUARE_ACCESS_TOKEN.")
            if r.status_code != 200:
                errs = "; ".join(f"{e.get('code')}: {e.get('detail')}" for e in data.get("errors", []))[:300]
                raise ConnectorError(f"Square {path} -> HTTP {r.status_code}: {errs or 'error'}")
            return data
        raise ConnectorError(f"Square {path}: gave up after {MAX_RETRIES} retries")

    def orders(self, start: str, end: str) -> list[dict]:
        out, cursor = [], None
        while True:
            body = {"location_ids": [self.cfg.location_id], "limit": 500, "query": {
                "filter": {"state_filter": {"states": ["COMPLETED"]},
                           "date_time_filter": {"closed_at": {"start_at": f"{start}T00:00:00Z",
                                                              "end_at": f"{end}T23:59:59Z"}}},
                "sort": {"sort_field": "CLOSED_AT", "sort_order": "ASC"}}}
            if cursor:
                body["cursor"] = cursor
            data = self._call("POST", "/v2/orders/search", body=body)
            out += data.get("orders", [])
            cursor = data.get("cursor")
            if not cursor:
                return out

    def refunds(self, start: str, end: str) -> list[dict]:
        out, cursor = [], None
        while True:
            params = {"begin_time": f"{start}T00:00:00Z", "end_time": f"{end}T23:59:59Z",
                      "location_id": self.cfg.location_id, "status": "COMPLETED", "sort_order": "ASC"}
            if cursor:
                params["cursor"] = cursor
            data = self._call("GET", "/v2/refunds", params=params)
            out += data.get("refunds", [])
            cursor = data.get("cursor")
            if not cursor:
                return out

    def timezone(self) -> str:
        loc = self._call("GET", f"/v2/locations/{self.cfg.location_id}").get("location", {})
        return loc.get("timezone") or "UTC"

    def pull(self, start: str, end: str, *, existing: pd.DataFrame | None = None) -> tuple[pd.DataFrame, L.LedgerReport]:
        report = L.LedgerReport(source=f"square:{self.cfg.environment}")
        tz = self.timezone()
        entries = normalize(self.orders(start, end), self.refunds(start, end), report,
                            environment=self.cfg.environment, tz=tz)
        led = L.merge(existing, L.to_frame(entries), report)
        report.n_entries = len(led)
        report.warnings.append("Square provides sales only: profit, margin and cash need cost data from another source.")
        return led, report


def _local_day(ts: str, tz: str) -> pd.Timestamp:
    """Business day in the location's time zone (Square timestamps are UTC)."""
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    return t.tz_convert(tz).tz_localize(None).normalize()


def normalize(orders: list[dict], refunds: list[dict], report: L.LedgerReport, *,
              environment: str = "sandbox", tz: str = "UTC") -> list[dict]:
    origin = f"Square {environment}"
    out, net_ratio = [], {}
    for o in orders:
        report.n_records_read += 1
        total, cur = _amount(o.get("total_money"))
        if total is None or not o.get("closed_at") or not o.get("id"):
            report.skip("order without total, id or closed_at")
            continue
        if o.get("state") != "COMPLETED":
            report.skip("order not completed")
            continue
        tax = _amount(o.get("total_tax_money"))[0] or 0.0
        tip = _amount(o.get("total_tip_money"))[0] or 0.0
        net = total - tax - tip
        net_ratio[o["id"]] = (net / total) if total else 1.0
        units = sum(float(li.get("quantity") or 0) for li in o.get("line_items") or [])
        out.append(L.entry("square", "Order", o["id"], 0, txn_date=_local_day(o["closed_at"], tz), amount=net, currency=cur,
                           category="revenue", sync_token=str(o.get("version")) if o.get("version") is not None else None,
                           description=f"{units:g} items" if units else None, data_origin=origin))
    unscaled = 0
    for r in refunds:
        report.n_records_read += 1
        amt, cur = _amount(r.get("amount_money"))
        if amt is None or not r.get("created_at") or not r.get("id"):
            report.skip("refund without amount, id or created_at")
            continue
        if r.get("status") != "COMPLETED":
            report.skip("refund not completed")
            continue
        ratio = net_ratio.get(r.get("order_id"))
        if ratio is None:
            unscaled += 1
            ratio = 1.0
        out.append(L.entry("square", "Refund", r["id"], 0, txn_date=_local_day(r["created_at"], tz),
                           amount=-amt * ratio, currency=cur, category="revenue",
                           description=f"refund of payment {r.get('payment_id')}", data_origin=origin))
    if unscaled:
        report.warnings.append(f"{unscaled} refund(s) belong to orders outside this download and are subtracted at "
                               "their full amount, which may include tax and tips.")
    return out
