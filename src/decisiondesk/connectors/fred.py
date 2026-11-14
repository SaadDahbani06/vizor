"""FRED / ALFRED client with point-in-time (vintage) support. Data are REAL economic observations.

* ``fetch``           latest-revised observations (fine for display, NOT for backtests).
* ``fetch_vintages``  every real-time period of every observation (ALFRED), i.e. what value was
                      published and when. Columns: observed_at, value, realtime_start,
                      realtime_end, published_at (first release date of that observation).
* ``series_info``     title, units, frequency, seasonal adjustment and notes (copyright text).

Raw API payloads are cached under data/cache/fred/ (gitignored) WITHOUT the API key, so
experiments can be re-run offline with ``offline=True``. Normalized features are built separately
in ``decisiondesk.macro``.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests

from .base import ConnectorError, DataProvenance, request_json

API = "https://api.stlouisfed.org/fred"
REALTIME_MIN, REALTIME_MAX = "1776-07-04", "9999-12-31"
PAGE_LIMIT = 100_000
FRED_NOTICE = ("This product uses the FRED(R) API but is not endorsed or certified by the "
               "Federal Reserve Bank of St. Louis.")


class FREDConnector:
    def __init__(self, api_key: str | None = None, *, session=None,
                 cache_dir: str | Path = "data/cache/fred", offline: bool = False):
        self.api_key = (api_key or os.getenv("FRED_API_KEY") or "").strip()
        self.session = session or requests.Session()
        self.cache_dir = Path(cache_dir)
        self.offline = offline

    def _require_key(self) -> None:
        if not self.api_key:
            raise ConnectorError("FRED_API_KEY not configured; the offline demo runs without it")

    def _get(self, endpoint: str, params: dict) -> dict:
        self._require_key()
        _, body = request_json(self.session, "GET", f"{API}/{endpoint}", secret=self.api_key,
                               params={**params, "api_key": self.api_key, "file_type": "json"},
                               ok_statuses=(200,))
        if not isinstance(body, dict):
            raise ConnectorError(f"FRED {endpoint}: unexpected response type")
        return body

    def _cached(self, name: str, fetch) -> tuple[dict, str]:
        path = self.cache_dir / f"{name}.json"
        if self.offline:
            if not path.exists():
                raise ConnectorError(f"offline mode: no cached FRED payload at {path}")
            cached = json.loads(path.read_text())
            return cached["payload"], cached["retrieved_at_utc"]
        payload = fetch()
        retrieved = datetime.now(UTC).isoformat(timespec="seconds")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"retrieved_at_utc": retrieved, "payload": payload}))
        return payload, retrieved

    def series_info(self, series_id: str) -> dict:
        payload, retrieved = self._cached(
            f"{series_id}_info", lambda: self._get("series", {"series_id": series_id}))
        items = payload.get("seriess") or []
        if not items:
            raise ConnectorError(f"FRED series {series_id} not found")
        info = dict(items[0])
        info["retrieved_at_utc"] = retrieved
        return info

    def fetch(self, identifier: str, **kwargs) -> tuple[pd.DataFrame, DataProvenance]:
        """Latest-vintage observations. Do not use for historical backtests."""
        params = {"series_id": identifier}
        params.update({k: v for k, v in kwargs.items()
                       if k in {"observation_start", "observation_end"}})
        body = self._get("series/observations", params)
        frame = pd.DataFrame(body.get("observations", []))
        if frame.empty:
            raise ConnectorError("FRED returned no observations")
        frame["date"] = pd.to_datetime(frame["date"])
        frame["value"] = pd.to_numeric(frame["value"].replace(".", None), errors="coerce")
        info = self.series_info(identifier)
        return frame, self._provenance(identifier, info, "latest", info["retrieved_at_utc"])

    def fetch_vintages(self, series_id: str, observation_start: str = "2021-01-01",
                       observation_end: str | None = None) -> tuple[pd.DataFrame, DataProvenance]:
        """All published values with their real-time validity windows (ALFRED)."""
        def download() -> dict:
            rows, offset = [], 0
            while True:
                params = {"series_id": series_id, "realtime_start": REALTIME_MIN,
                          "realtime_end": REALTIME_MAX, "observation_start": observation_start,
                          "output_type": 1, "limit": PAGE_LIMIT, "offset": offset,
                          "sort_order": "asc"}
                if observation_end:
                    params["observation_end"] = observation_end
                body = self._get("series/observations", params)
                page = body.get("observations", [])
                rows.extend(page)
                offset += len(page)
                if not page or offset >= int(body.get("count", 0)):
                    return {"count": len(rows), "observations": rows}

        name = f"{series_id}_vintages_{observation_start}_{observation_end or 'open'}"
        payload, retrieved = self._cached(name, download)
        frame = normalize_vintages(payload["observations"])
        if frame.empty:
            raise ConnectorError(f"FRED returned no vintage rows for {series_id}")
        frame.insert(0, "series_id", series_id)
        frame["retrieved_at_utc"] = retrieved
        info = self.series_info(series_id)
        frame["units"] = info.get("units", "")
        frame["frequency"] = info.get("frequency", "")
        return frame, self._provenance(series_id, info, "point_in_time", retrieved)

    def _provenance(self, series_id: str, info: dict, mode: str, retrieved: str) -> DataProvenance:
        notes = (info.get("notes") or "").strip()
        copyright_text = " ".join(s for s in notes.split(". ") if "opyright" in s)[:300]
        return DataProvenance(
            provider="FRED/ALFRED (Federal Reserve Bank of St. Louis)", dataset_id=series_id,
            source_url=f"https://fred.stlouisfed.org/series/{series_id}",
            units=info.get("units", "unknown"), frequency=info.get("frequency", "unknown"),
            retrieved_at_utc=retrieved, publication_date_column="published_at",
            notes=f"{info.get('title', '')}; {info.get('seasonal_adjustment', '')}",
            vintage_mode=mode,
            restrictions=(copyright_text or "See FRED API terms of use; ") + " " + FRED_NOTICE)


def normalize_vintages(observations: list[dict]) -> pd.DataFrame:
    """Raw ALFRED rows -> typed frame with first-release date per observation."""
    frame = pd.DataFrame(observations, columns=["realtime_start", "realtime_end", "date", "value"])
    if frame.empty:
        return frame.rename(columns={"date": "observed_at"})
    frame = frame.rename(columns={"date": "observed_at"})
    frame["observed_at"] = pd.to_datetime(frame["observed_at"])
    frame["realtime_start"] = pd.to_datetime(frame["realtime_start"])
    # 9999-12-31 is outside pandas' Timestamp range: map the open end to Timestamp.max.
    frame["realtime_end"] = pd.to_datetime(
        frame["realtime_end"].replace(REALTIME_MAX, "2262-04-11"))
    frame["value"] = pd.to_numeric(frame["value"].replace(".", None), errors="coerce")
    frame = frame.dropna(subset=["value"]).sort_values(["observed_at", "realtime_start"])
    frame["published_at"] = frame.groupby("observed_at")["realtime_start"].transform("min")
    return frame.reset_index(drop=True)
