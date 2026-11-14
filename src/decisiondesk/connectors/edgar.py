"""Optional SEC companyfacts fetcher for BENCHMARKING, not private daily company data."""
from __future__ import annotations

import os
from datetime import UTC, datetime

import pandas as pd
import requests

from .base import DataProvenance


class EDGARConnector:
    def __init__(self, user_agent: str | None = None):
        self.user_agent = user_agent or os.getenv("SEC_USER_AGENT")

    def fetch(self, identifier: str, **kwargs) -> tuple[pd.DataFrame, DataProvenance]:
        if not self.user_agent or "example.com" in self.user_agent:
            raise RuntimeError("Configure a real SEC_USER_AGENT with contact details before fetching")
        if not identifier.isdecimal():
            raise ValueError("Company identifier must be numeric CIK")
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(identifier):010d}.json"
        response = requests.get(url, headers={"User-Agent": self.user_agent,
                                                "Accept-Encoding": "gzip, deflate"}, timeout=25)
        response.raise_for_status()
        obj = response.json()
        # Return raw structured facts in an explicit column; metric mapping is not complete.
        row = {"cik": obj.get("cik"), "entity_name": obj.get("entityName"),
               "facts": obj.get("facts", {})}
        frame = pd.DataFrame([row])
        return frame, DataProvenance(provider="SEC EDGAR", dataset_id=identifier,
            source_url=url, units="Varies by XBRL fact", frequency="Quarterly/annual filing",
            retrieved_at_utc=datetime.now(UTC).isoformat(),
            notes="Unmapped companyfacts; respect accession, filing date, period, units, taxonomy and rate rules.")
