"""Offline tests: FRED vintage client (mocked HTTP), point-in-time alignment, ablation plumbing."""
import numpy as np
import pandas as pd
import pytest

from decisiondesk.ablation import compare
from decisiondesk.connectors.base import ConnectorError
from decisiondesk.connectors.fred import FREDConnector, normalize_vintages
from decisiondesk.features import WARMUP_DAYS, build_origin_features
from decisiondesk.leaderboard import EvaluationConfig, build_leaderboard
from decisiondesk.macro import MacroSpec, origin_exog, point_in_time_table, snapshot

KEY = "fred-secret-0123456789abcdef0123"

# Toy ALFRED history: Jan value first published 2024-02-13 as 100, revised to 101 on 2024-03-12.
RAW = [
    {"realtime_start": "2023-01-12", "realtime_end": "9999-12-31", "date": "2022-12-01", "value": "88"},
    {"realtime_start": "2023-02-14", "realtime_end": "9999-12-31", "date": "2023-01-01", "value": "90"},
    {"realtime_start": "2024-01-11", "realtime_end": "9999-12-31", "date": "2023-12-01", "value": "99"},
    {"realtime_start": "2024-02-13", "realtime_end": "2024-03-11", "date": "2024-01-01", "value": "100"},
    {"realtime_start": "2024-03-12", "realtime_end": "9999-12-31", "date": "2024-01-01", "value": "101"},
    {"realtime_start": "2024-03-12", "realtime_end": "9999-12-31", "date": "2024-02-01", "value": "."},
]
SPEC = MacroSpec("TOY", "yoy_pct", "test")


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


class FakeFRED:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def request(self, method, url, params=None, json=None, timeout=None):
        self.calls.append((url, dict(params)))
        if url.endswith("/series"):
            return FakeResponse(200, {"seriess": [{"id": "TOY", "title": "Toy index",
                                                    "units": "Index", "frequency": "Monthly",
                                                    "seasonal_adjustment": "SA", "notes": ""}]})
        page = self.pages[params["offset"] // 2] if params["offset"] // 2 < len(self.pages) else []
        return FakeResponse(200, {"count": sum(len(p) for p in self.pages), "observations": page})


def test_vintage_fetch_paginates_caches_and_records_provenance(tmp_path):
    fake = FakeFRED([RAW[:2], RAW[2:4], RAW[4:]])
    conn = FREDConnector(api_key=KEY, session=fake, cache_dir=tmp_path)
    import decisiondesk.connectors.fred as fred_mod
    old = fred_mod.PAGE_LIMIT
    fred_mod.PAGE_LIMIT = 2
    try:
        frame, prov = conn.fetch_vintages("TOY", observation_start="2023-01-01")
    finally:
        fred_mod.PAGE_LIMIT = old
    obs_calls = [p for u, p in fake.calls if u.endswith("observations")]
    assert [p["offset"] for p in obs_calls] == [0, 2, 4]
    assert obs_calls[0]["realtime_start"] == "1776-07-04" and obs_calls[0]["output_type"] == 1
    assert len(frame) == len(RAW) - 1  # "." missing value dropped
    jan = frame[frame.observed_at == "2024-01-01"]
    assert (jan.published_at == pd.Timestamp("2024-02-13")).all()
    assert prov.vintage_mode == "point_in_time" and prov.units == "Index"
    assert "not endorsed" in prov.restrictions
    for path in tmp_path.iterdir():
        assert KEY not in path.read_text(), "cache must not contain the API key"
    # Offline re-use of the cache makes no HTTP calls.
    offline = FREDConnector(api_key="", session=None, cache_dir=tmp_path, offline=True)
    again, _ = offline.fetch_vintages("TOY", observation_start="2023-01-01")
    pd.testing.assert_frame_equal(frame.drop(columns="retrieved_at_utc"),
                                  again.drop(columns="retrieved_at_utc"))


def test_errors_never_leak_key(tmp_path):
    class Boom:
        def request(self, method, url, params=None, json=None, timeout=None):
            return FakeResponse(400, f"Bad Request. api_key={params['api_key']} invalid")

    with pytest.raises(ConnectorError) as err:
        FREDConnector(api_key=KEY, session=Boom(), cache_dir=tmp_path).series_info("TOY")
    assert KEY not in str(err.value)


def test_missing_key_and_offline_cache_errors(tmp_path, monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(ConnectorError, match="not configured"):
        FREDConnector(api_key="", cache_dir=tmp_path).series_info("X")
    with pytest.raises(ConnectorError, match="offline"):
        FREDConnector(cache_dir=tmp_path, offline=True).series_info("X")


def test_snapshot_respects_publication_and_revision_dates():
    v = normalize_vintages(RAW)
    before = snapshot(v, "2024-02-12")   # Jan not yet published
    assert before.index.max() == pd.Timestamp("2023-12-01")
    first = snapshot(v, "2024-02-13")    # release day
    assert first[pd.Timestamp("2024-01-01")] == 100
    revised = snapshot(v, "2024-03-20")
    assert revised[pd.Timestamp("2024-01-01")] == 101


def test_yoy_uses_same_vintage_and_future_revisions_do_not_leak():
    v = normalize_vintages(RAW)
    table = point_in_time_table(v, pd.to_datetime(["2024-02-20", "2024-03-20"]), SPEC)
    assert table.loc["2024-02-20", "x_toy_yoy_pct"] == pytest.approx(100 * (100 / 90 - 1))
    assert table.loc["2024-03-20", "x_toy_yoy_pct"] == pytest.approx(100 * (101 / 90 - 1))
    # Add a far-future revision: features at earlier as-of dates must not change.
    extra = RAW + [{"realtime_start": "2030-01-01", "realtime_end": "9999-12-31",
                    "date": "2024-01-01", "value": "500"}]
    v2 = normalize_vintages(extra)
    v2.loc[(v2.observed_at == "2024-01-01") & (v2.value == 101), "realtime_end"] = \
        pd.Timestamp("2029-12-31")
    table2 = point_in_time_table(v2, pd.to_datetime(["2024-02-20", "2024-03-20"]), SPEC)
    pd.testing.assert_frame_equal(table, table2)


def test_origin_exog_alignment_uses_previous_day():
    v = normalize_vintages(RAW)
    dates = pd.Series(pd.date_range("2023-11-20", periods=WARMUP_DAYS + 40, freq="D"))
    exog = origin_exog(dates, {"TOY": v}, (SPEC,))
    assert len(exog) == len(dates) + 1 and exog.iloc[0].isna().all()
    release = int(np.flatnonzero(dates == pd.Timestamp("2024-02-13"))[0])
    # Origin whose last observed day is the day before release: Jan not yet known.
    assert exog.loc[release, "x_toy_age_days"] == (pd.Timestamp("2024-02-12")
                                                   - pd.Timestamp("2023-12-01")).days
    # Origin release+1 has observed the release day.
    assert exog.loc[release + 1, "x_toy_yoy_pct"] == pytest.approx(100 * (100 / 90 - 1))


def test_exog_columns_validated():
    y = np.arange(200, dtype=float)
    dates = pd.date_range("2024-01-01", periods=230)
    bad = pd.DataFrame({"cpi": np.ones(201)})
    with pytest.raises(ValueError, match="x_"):
        build_origin_features(y, dates, np.array([150]), 7, bad)


def test_positive_control_exog_signal_is_detected():
    """Plumbing check: when the target truly depends on information known at the origin,
    the exogenous arm must beat internal-only on validation. Synthetic by construction."""
    rng = np.random.default_rng(0)
    n = 600
    dates = pd.date_range("2024-01-01", periods=n)
    level = np.repeat(rng.normal(0, 300, n // 28 + 2), 28)[:n + 28]  # regime per 28-day block
    pattern = 1000 + 200 * (dates.day_of_week >= 4)
    y = pattern + level[28:n + 28] + rng.normal(0, 40, n)  # regime for block k announced a block early
    history = pd.DataFrame({"date": dates, "revenue": y})
    # Known at origin o: the announced level for day o (what the business was told in advance).
    exog = pd.DataFrame({"x_announced": np.append(level[28:n + 28], level[n + 27])})
    cfg = EvaluationConfig(holdout_folds=3)
    internal = build_leaderboard(history, "revenue", dataset="pc", data_label="SYNTHETIC",
                                 cfg=cfg, models=("ridge",))
    macro = build_leaderboard(history, "revenue", dataset="pc", data_label="SYNTHETIC",
                              cfg=cfg, models=("ridge",), exog=exog, feature_set="exog")
    _, decision = compare(internal, macro, 0.02)
    assert decision["validation_gain_of_fred_arm"] > 0.05
    assert decision["adopt_fred_features"]
    assert macro.meta["exogenous_columns"] == ["x_announced"]


def test_ablation_cli_missing_key_exits_cleanly(monkeypatch, tmp_path):
    import sys

    from decisiondesk import ablation

    monkeypatch.chdir(tmp_path)  # no .env and no FRED cache here
    monkeypatch.setenv("FRED_API_KEY", "")
    monkeypatch.setattr(sys, "argv", ["ablation", "--target", "revenue"])
    with pytest.raises(SystemExit) as exit_info:
        ablation.cli()
    assert "FRED_API_KEY not configured" in str(exit_info.value)


def test_unpublished_month_is_skipped_not_imputed():
    """October 2025 was never published (federal shutdown): YoY for Oct 2026 is impossible, so the
    newest observation with a real comparison month is used, from the same vintage."""
    from decisiondesk.macro import _features_from_snapshot

    idx = pd.date_range("2024-09-01", "2026-10-01", freq="MS").drop(pd.Timestamp("2025-10-01"))
    snap = pd.Series(np.arange(len(idx), dtype=float) + 100, index=idx)
    feats = _features_from_snapshot(snap, pd.Timestamp("2026-11-20"), SPEC)
    sep26, sep25 = snap[pd.Timestamp("2026-09-01")], snap[pd.Timestamp("2025-09-01")]
    assert feats["x_toy_yoy_pct"] == pytest.approx(100 * (sep26 / sep25 - 1))
    assert feats["x_toy_age_days"] == (pd.Timestamp("2026-11-20") - pd.Timestamp("2026-10-01")).days
