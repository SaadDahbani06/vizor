"""Adapter contract and shared HTTP hygiene for external data providers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd
import requests


@dataclass(frozen=True)
class DataProvenance:
    provider: str
    dataset_id: str
    source_url: str
    units: str
    frequency: str
    retrieved_at_utc: str
    publication_date_column: str | None = None
    notes: str = ""
    vintage_mode: str = "latest"  # "latest" (revised today) | "point_in_time" (ALFRED vintages)
    restrictions: str = ""
    data_origin: str = "REAL_EXTERNAL"  # or "SYNTHETIC_SANDBOX"


class ExternalDataConnector(Protocol):
    def fetch(self, identifier: str, **kwargs) -> tuple[pd.DataFrame, DataProvenance]: ...


class ConnectorError(RuntimeError):
    """Raised with a message that is guaranteed not to contain credentials."""


def redact(text: str, *secrets: str | None) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


def request_json(session, method: str, url: str, *, secret: str | None, params: dict | None = None,
                 json_body: dict | None = None, timeout: float = 25.0,
                 ok_statuses: tuple[int, ...] = (200, 201)) -> tuple[int, object]:
    """HTTP call whose errors never leak the key.

    Both FRED and Nessie pass the key as a query parameter, and requests' own exception text
    (``raise_for_status``) embeds the full URL. We therefore build messages ourselves.
    """
    try:
        response = session.request(method, url, params=params, json=json_body, timeout=timeout)
    except requests.RequestException as exc:
        raise ConnectorError(f"{method} {url} failed: {type(exc).__name__}") from None
    try:
        body = response.json()
    except ValueError:
        body = response.text
    if response.status_code not in ok_statuses:
        detail = redact(str(body)[:300], secret)
        raise ConnectorError(f"{method} {url} -> HTTP {response.status_code}: {detail}")
    return response.status_code, body
