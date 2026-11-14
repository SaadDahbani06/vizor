"""LIVE, READ-ONLY integration tests against FRED and the Nessie sandbox.

Skipped unless MSRD_LIVE=1. They never write to Nessie and never print credentials.

    MSRD_LIVE=1 pytest -q -m live
"""
import os

import numpy as np
import pandas as pd
import pytest
from dotenv import load_dotenv

from decisiondesk.connectors.base import ConnectorError
from decisiondesk.connectors.fred import FREDConnector
from decisiondesk.connectors.nessie import RECORD_KINDS, NessieClient
from decisiondesk.macro import snapshot
from decisiondesk.nessie_sandbox import fetch_all
from decisiondesk.transactions import TRANSACTION_COLUMNS, nessie_to_canonical

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("MSRD_LIVE") != "1", reason="set MSRD_LIVE=1 for live API tests"),
]
load_dotenv(".env")


@pytest.fixture(scope="module")
def fred_fresh(tmp_path_factory):
    """Fresh download (empty cache dir), so this really hits the API."""
    conn = FREDConnector(cache_dir=tmp_path_factory.mktemp("fred"))
    frame, prov = conn.fetch_vintages("CPIAUCSL", observation_start="2022-01-01")
    return conn, frame, prov


def test_fred_live_series_metadata(fred_fresh):
    conn, _, _ = fred_fresh
    info = conn.series_info("CPIAUCSL")
    assert info["frequency"] == "Monthly" and "Index" in info["units"]


def test_fred_live_vintages_have_revisions_and_plausible_release_lags(fred_fresh):
    _, frame, prov = fred_fresh
    assert prov.vintage_mode == "point_in_time" and len(frame) > frame.observed_at.nunique()
    first = frame.groupby("observed_at").published_at.min()
    lag_days = (first - first.index).dt.days
    # A monthly CPI value cannot be published before its month ends.
    assert (lag_days >= 28).all(), lag_days[lag_days < 28]


def test_fred_live_snapshots_never_use_unpublished_rows(fred_fresh):
    _, frame, _ = fred_fresh
    for asof in pd.date_range("2023-01-05", "2026-10-25", freq="17D"):
        snap = snapshot(frame, asof)
        used = frame[(frame.realtime_start <= asof) & (frame.realtime_end >= asof)]
        assert (used.realtime_start <= asof).all()
        latest = snap.index.max()
        assert frame.loc[frame.observed_at == latest, "published_at"].iloc[0] <= asof


def test_fred_live_invalid_key_is_graceful_and_redacted(tmp_path):
    bad = "0" * 32
    with pytest.raises(ConnectorError) as err:
        FREDConnector(api_key=bad, cache_dir=tmp_path).series_info("CPIAUCSL")
    assert bad not in str(err.value) and "HTTP" in str(err.value)


def test_nessie_live_sandbox_populated_and_readable():
    records, accounts, merchants, unreadable = fetch_all(NessieClient())
    assert set(accounts) == {"checking", "savings"} and len(merchants) >= 2
    checking = records[accounts["checking"]]
    counts = {k: len(checking[k]) for k in RECORD_KINDS}
    assert counts["deposits"] >= 28 and counts["withdrawals"] >= 8
    assert counts["purchases"] >= 12 and counts["transfers"] >= 4
    assert len(records[accounts["savings"]]["deposits"]) >= 4
    # Known server defect: bills list is unreadable. Fail loudly if Nessie fixes it, so we update.
    assert all(k.endswith("/bills") for k in unreadable)


def test_nessie_live_repeated_import_is_stable_and_canonical():
    frames = []
    for _ in range(2):
        records, accounts, merchants, _ = fetch_all(NessieClient())
        tx, report = nessie_to_canonical(records, org_account_ids=set(accounts.values()),
                                         merchants=merchants, retrieved_at_utc="fixed")
        assert report.duplicates_dropped == 0 and tx.transaction_id.is_unique
        frames.append(tx)
    assert list(frames[0].columns) == list(TRANSACTION_COLUMNS)
    pd.testing.assert_frame_equal(frames[0], frames[1])
    assert (frames[0].data_origin == "SYNTHETIC_SANDBOX_NESSIE").all()
    internal = frames[0][frames[0].is_internal_transfer]
    assert np.isclose(internal.amount.sum(), 0.0)


def test_nessie_live_unreachable_host_is_graceful():
    client = NessieClient(base_url="https://nessie.invalid", throttle_seconds=0)
    with pytest.raises(ConnectorError) as err:
        client.list_customers()
    assert client.api_key not in str(err.value)
