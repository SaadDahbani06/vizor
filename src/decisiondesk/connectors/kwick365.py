"""Kwick365 — The Independent Restaurant Index (https://kwick365.com). REAL AGGREGATE data.

Verified 2026-11-14 from the site's /terms/ and /methodology/ pages and its JSON API:
  * Endpoint: GET https://kwick365.com/api/v1/reading/<reading>/?geo=<geo>
    returns {metric, geo, unit, value2_unit, n_unit, rows:[{day, value, value2, n_stores, n}],
    note, source}. Open and unauthenticated; returns only published cells (withheld days are
    absent). The API returns at most the latest 366 days (a `range` parameter is ignored).
  * Geographies: "us", states (e.g. "tx", "ga", "fl", "ca") and metros that clear thresholds.
  * Sample: independent restaurants running KwickPOS (about 1,900, concentrated in TX, GA, FL, CA,
    NY). NOT a probability sample of American restaurants; levels do not generalise.
  * A day publishes only with >= 30 restaurants and >= 500 orders in that geography.
  * Survivorship: history is recomputed nightly over TODAY's surviving restaurants, and the last
    three days are recomputed every night. There are no vintages, so past values are not what was
    published at the time (``vintage_mode = "latest_recomputed"``).
  * Terms: free to quote/republish/build on with a link; please carry the date and sample size and
    describe the sample as "independent restaurants running KwickPOS". Be gentle with the API
    (one small server): this client caches and makes one request per reading/geo per run.
  * Figures are benchmarks of what already happened, not forecasts, and not individual-restaurant
    data. Citation: "Kwick365 — The Independent Restaurant Index, https://kwick365.com".
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests

from .base import ConnectorError, DataProvenance, request_json

API = "https://kwick365.com/api/v1/reading"
USER_AGENT = "MainStreetRiskDesk/0.1 (research; contact via kwick365 terms)"
UNSTABLE_TRAILING_DAYS = 3  # recomputed nightly per the methodology
READINGS = ("online-ticket", "online-tip-rate", "online-items-per-order", "online-orders-per-store",
            "instore-ticket", "instore-orders-per-store", "instore-active-stores",
            "network-active-stores", "network-rating-median", "network-rating-45-share")
RESTRICTIONS = ("Kwick365 terms: free to cite/republish with a link; carry the date and sample "
                "size; describe the sample as independent restaurants running KwickPOS, not as "
                "American restaurants. Survivorship-recomputed history; no vintages.")


class Kwick365Connector:
    def __init__(self, *, session=None, cache_dir: str | Path = "data/cache/kwick365",
                 offline: bool = False, cache_first: bool = False):
        self.session = session or requests.Session()
        if hasattr(self.session, "headers"):
            self.session.headers.update({"User-Agent": USER_AGENT})
        self.cache_dir = Path(cache_dir)
        self.offline = offline
        self.cache_first = cache_first  # use a cached payload if present, else one live request

    def _payload(self, reading: str, geo: str) -> tuple[dict, str]:
        path = self.cache_dir / f"{reading}_{geo}.json"
        if self.offline or (self.cache_first and path.exists()):
            if not path.exists():
                raise ConnectorError(f"offline mode: no cached Kwick365 payload at {path}")
            cached = json.loads(path.read_text())
            return cached["payload"], cached["retrieved_at_utc"]
        _, body = request_json(self.session, "GET", f"{API}/{reading}/", secret=None,
                               params={"geo": geo}, ok_statuses=(200,))
        if not isinstance(body, dict) or "rows" not in body:
            raise ConnectorError(f"Kwick365 {reading}/{geo}: unexpected response")
        retrieved = datetime.now(UTC).isoformat(timespec="seconds")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"retrieved_at_utc": retrieved, "payload": body}))
        return body, retrieved

    def fetch(self, identifier: str, geo: str = "us", **_) -> tuple[pd.DataFrame, DataProvenance]:
        if identifier not in READINGS:
            raise ValueError(f"unknown Kwick365 reading {identifier!r}; known: {READINGS}")
        body, retrieved = self._payload(identifier, geo)
        frame = pd.DataFrame(body["rows"], columns=["day", "value", "value2", "n_stores", "n"])
        if frame.empty:
            raise ConnectorError(f"Kwick365 {identifier}/{geo} returned no published days")
        frame["day"] = pd.to_datetime(frame["day"])
        frame = frame.sort_values("day").reset_index(drop=True)
        frame["available_at"] = frame["day"] + pd.Timedelta(days=1)  # published nightly
        frame["stable"] = frame["day"] <= frame["day"].max() - pd.Timedelta(
            days=UNSTABLE_TRAILING_DAYS)
        prov = DataProvenance(
            provider="Kwick365 (KwickPOS independent-restaurant network)",
            dataset_id=f"{identifier}:{geo}",
            source_url=f"https://kwick365.com/reading/{identifier}/",
            units=str(body.get("unit", "")), frequency="daily (published days only)",
            retrieved_at_utc=retrieved, publication_date_column="available_at",
            notes=f"geo={geo}; second value: {body.get('value2_unit', '')}; "
                  f"n = {body.get('n_unit', '')}; {body.get('note', '')}",
            vintage_mode="latest_recomputed", restrictions=RESTRICTIONS,
            data_origin="REAL_AGGREGATE_INDUSTRY")
        return frame, prov


def contiguous_daily(frame: pd.DataFrame, value_col: str = "value") -> tuple[pd.DataFrame, dict]:
    """Longest gap-free run of STABLE published days (withheld days are never filled).

    Returns (frame with columns date/<value_col>/n_stores/n, report).
    """
    stable = frame[frame["stable"]].reset_index(drop=True)
    gaps = stable["day"].diff().dt.days.fillna(1)
    run_id = (gaps != 1).cumsum()
    best = run_id.value_counts().idxmax()
    seg = stable[run_id == best].rename(columns={"day": "date"}).reset_index(drop=True)
    missing = pd.date_range(stable["day"].min(), stable["day"].max()).difference(stable["day"])
    report = {"published_days": len(frame), "unstable_trailing_days_dropped": int((~frame["stable"]).sum()),
              "withheld_days_in_window": [str(d.date()) for d in missing],
              "segment": [str(seg["date"].iloc[0].date()), str(seg["date"].iloc[-1].date())],
              "segment_days": len(seg)}
    return seg[["date", value_col, "n_stores", "n"]], report
