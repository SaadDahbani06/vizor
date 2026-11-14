"""Capital One Nessie sandbox client. Nessie holds SIMULATED banking records only.

Endpoint notes (checked 2026-11-14 against https://nessieisreal.com/nessie-openapi-spec.yaml and
read-only probes of the configured host):
  * Documented create endpoints: customers, accounts, deposits, withdrawals, bills, merchants.
  * Purchases and transfers are documented only as GET/PUT/DELETE by id. ``/accounts/{id}/purchases``
    and ``/accounts/{id}/transfers`` respond to GET on api.nessieisreal.com but are undocumented;
    creating through them is attempted and reported, never assumed.
  * Deposit / withdrawal ``amount`` is typed as an integer; bills use a float ``payment_amount``.
  * GET on a non-existent account's deposits returns 200 [] (ids are not validated on reads),
    and an empty transfer list is a 404 with a message, which we treat as [].

Live behaviour of https://prod-api.nessieisreal.com with a valid 32-hex key (2026-11-14):
  * GET returns 200 [] for ANY key; only writes validate the key (401 "Invalid API key").
  * Create schemas are strict (extra fields -> 400). Purchases take purchase_date, not
    transaction_date. Transfers accept only transaction_date/status/amount/description: no payee
    field is accepted, so the receiving account cannot be recorded.
  * Transfers are returned with "id" (not "_id") and without payer_id/payee_id.
  * Historical transaction_date / purchase_date values are stored and returned as sent.
  * GET /accounts/{id}/bills and /customers/{id}/bills return 400 ("validation errors for Bill:
    recurring_date, upcoming_payment_date field required") once a one-time bill exists, i.e. the
    server cannot serialize bills it accepted. Bill ids are not echoed on create.
"""
from __future__ import annotations

import os
import time

import requests

from .base import ConnectorError, request_json

DEFAULT_BASE_URL = "https://api.nessieisreal.com"


class NessieClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, *,
                 session=None, throttle_seconds: float = 0.15):
        self.api_key = (api_key or os.getenv("NESSIE_API_KEY") or "").strip()
        if not self.api_key:
            raise ConnectorError("NESSIE_API_KEY not configured; the offline demo does not need it")
        self.base_url = (base_url or os.getenv("NESSIE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.session = session or requests.Session()
        self.throttle_seconds = throttle_seconds
        self.post_count = 0

    # -- low level ---------------------------------------------------------------------------
    def _get_list(self, path: str) -> list[dict]:
        url = f"{self.base_url}{path}"
        try:
            _, body = request_json(self.session, "GET", url, secret=self.api_key,
                                   params={"key": self.api_key}, ok_statuses=(200,))
        except ConnectorError as exc:
            if "HTTP 404" in str(exc) and "No " in str(exc) and "found" in str(exc):
                return []
            raise
        if not isinstance(body, list):
            raise ConnectorError(f"GET {url}: expected a JSON list, got {type(body).__name__}")
        return body

    def _post(self, path: str, body: dict) -> dict | None:
        """POST and return the created object when the API echoes it (``objectCreated``)."""
        url = f"{self.base_url}{path}"
        _, resp = request_json(self.session, "POST", url, secret=self.api_key,
                               params={"key": self.api_key}, json_body=body,
                               ok_statuses=(200, 201))
        self.post_count += 1
        if self.throttle_seconds:
            time.sleep(self.throttle_seconds)
        if isinstance(resp, dict):
            return resp.get("objectCreated") or (resp if "_id" in resp else None)
        return None  # spec documents e.g. the plain string "Withdrawal created"

    # -- resources ---------------------------------------------------------------------------
    def list_customers(self) -> list[dict]:
        return self._get_list("/customers")

    def create_customer(self, body: dict) -> dict | None:
        return self._post("/customers", body)

    def list_accounts(self, customer_id: str) -> list[dict]:
        return self._get_list(f"/customers/{customer_id}/accounts")

    def create_account(self, customer_id: str, body: dict) -> dict | None:
        return self._post(f"/customers/{customer_id}/accounts", body)

    def list_merchants(self) -> list[dict]:
        return self._get_list("/merchants")

    def create_merchant(self, body: dict) -> dict | None:
        return self._post("/merchants", body)

    def list_records(self, account_id: str, kind: str) -> list[dict]:
        if kind not in RECORD_KINDS:
            raise ValueError(f"kind must be one of {RECORD_KINDS}")
        return self._get_list(f"/accounts/{account_id}/{kind}")

    def create_record(self, account_id: str, kind: str, body: dict) -> dict | None:
        if kind not in RECORD_KINDS:
            raise ValueError(f"kind must be one of {RECORD_KINDS}")
        return self._post(f"/accounts/{account_id}/{kind}", body)


RECORD_KINDS = ("deposits", "withdrawals", "purchases", "transfers", "bills")
