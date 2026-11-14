"""Milestone 3.5 offline tests: Kwick365 connector (mocked HTTP), Iowa export importer (fixture in
the official schema; NOT real data), business profile / capability detection, source selection,
and procurement reserve decisions."""
import json

import numpy as np
import pandas as pd
import pytest

from decisiondesk.connectors.base import ConnectorError
from decisiondesk.connectors.iowa_liquor import COLUMNS, export_files, scan_stores, store_daily
from decisiondesk.connectors.kwick365 import Kwick365Connector, contiguous_daily
from decisiondesk.decision.baseline import (
    ForecastBundle,
    TargetForecast,
    build_forecast_bundle,
    make_draws,
)
from decisiondesk.decision.engine import evaluate_actions
from decisiondesk.decision.procurement import (
    UNITS,
    ProcurementConfig,
    procurement_actions,
    simulate_procurement,
)
from decisiondesk.decision.ranking import rank
from decisiondesk.decision.types import Constraint, Objective
from decisiondesk.leaderboard import EvaluationConfig
from decisiondesk.profile import (
    BusinessProfile,
    ColumnMapping,
    apply_mapping,
    capabilities,
    read_upload,
)
from decisiondesk.sources import CATALOG, fred_observation, select_sources


# ------------------------------------------------------------------------------ Kwick365
class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._b, self.text = status, body, json.dumps(body)

    def json(self):
        return self._b


class FakeKwick:
    def __init__(self, rows):
        self.rows, self.calls, self.headers = rows, 0, {}

    def request(self, method, url, params=None, json=None, timeout=None):
        self.calls += 1
        assert method == "GET" and url.endswith("/online-orders-per-store/")
        return FakeResp(200, {"metric": "online-orders-per-store", "geo": params["geo"],
                              "unit": "orders per active restaurant", "value2_unit": "x",
                              "n_unit": "orders", "rows": self.rows, "note": "published only",
                              "source": "https://kwick365.com/methodology/"})


def kwick_rows(days=40, withhold=()):
    out = []
    for i, d in enumerate(pd.date_range("2026-01-01", periods=days)):
        if str(d.date()) in withhold:
            continue
        out.append({"day": str(d.date()), "value": 10 + i % 7, "value2": 11.0, "n_stores": 700,
                    "n": 9000})
    return out


def test_kwick_fetch_provenance_cache_and_cache_first(tmp_path):
    fake = FakeKwick(kwick_rows())
    conn = Kwick365Connector(session=fake, cache_dir=tmp_path)
    frame, prov = conn.fetch("online-orders-per-store", "us")
    assert len(frame) == 40 and prov.data_origin == "REAL_AGGREGATE_INDUSTRY"
    assert prov.vintage_mode == "latest_recomputed" and "KwickPOS" in prov.restrictions
    assert (frame["available_at"] == frame["day"] + pd.Timedelta(days=1)).all()
    assert (~frame["stable"]).sum() == 3  # last three days are recomputed nightly
    cached = Kwick365Connector(session=fake, cache_dir=tmp_path, cache_first=True)
    cached.fetch("online-orders-per-store", "us")
    assert fake.calls == 1  # served from cache
    with pytest.raises(ConnectorError, match="offline"):
        Kwick365Connector(cache_dir=tmp_path, offline=True).fetch("online-orders-per-store", "tx")
    with pytest.raises(ValueError, match="unknown"):
        conn.fetch("revenue-per-store")


def test_kwick_contiguous_segment_never_fills_withheld_days(tmp_path):
    fake = FakeKwick(kwick_rows(60, withhold=("2026-01-20",)))
    frame, _ = Kwick365Connector(session=fake, cache_dir=tmp_path).fetch("online-orders-per-store")
    seg, rep = contiguous_daily(frame)
    assert rep["withheld_days_in_window"] == ["2026-01-20"]
    assert rep["segment"] == ["2026-01-21", "2026-02-26"]  # after the gap, minus 3 unstable days
    assert seg["date"].diff().dropna().dt.days.eq(1).all()


# ------------------------------------------------------------------------------ Iowa fixture
def write_iowa_fixture(path, seed=0):
    """Rows in the OFFICIAL dataset-1051 column layout. Test fixture only, not real data."""
    rng = np.random.default_rng(seed)
    rows, inv = [], 0
    for d in pd.date_range("2022-01-03", "2025-12-29", freq="D"):
        if d.day_of_week == 0:  # store 100 orders every Monday
            for _ in range(3):
                inv += 1
                rows.append({"invoice_id": f"INV-{inv}", "ordered_on": d.date(), "store_no": "100",
                                 "store_name": "FIXTURE MARKET", "store_address": "1 TEST ST",
                                 "store_city": "AMES", "sales_bottles": 12,
                                 "sales_dollars": float(rng.normal(400, 40)), "sales_liters": 9.0})
        if d.day_of_week == 2 and d.year == 2025:  # store 200: short history, moved address
            inv += 1
            rows.append({"invoice_id": f"INV-{inv}", "ordered_on": d.date(), "store_no": "200",
                             "store_name": "FIXTURE SHOP", "store_address": f"{d.month} NEW RD",
                             "store_city": "DES MOINES", "sales_bottles": 6, "sales_dollars": 150.0,
                             "sales_liters": 4.5})
    frame = pd.DataFrame(rows)
    for c in COLUMNS:
        if c not in frame:
            frame[c] = None
    frame = pd.concat([frame, frame.iloc[[0]]])  # one duplicated invoice line
    frame[list(COLUMNS)].to_csv(path, index=False)


def test_iowa_store_daily_identity_and_suitability(tmp_path):
    d = tmp_path / "iowa"
    d.mkdir()
    write_iowa_fixture(d / "part1.csv")
    files = export_files(d)
    daily, rep, prov = store_daily(files, "100")
    assert prov.data_origin == "REAL_INDIVIDUAL_STORE_PURCHASES" and "CC BY 4.0" in prov.restrictions
    assert "not customer sales" in prov.notes
    assert daily["date"].diff().dropna().dt.days.eq(1).all()
    assert rep.longest_zero_run_days == 6 and rep.suitable, rep.reasons
    mondays = daily[~daily["no_invoice"]]
    assert (mondays["date"].dt.day_of_week == 0).all()
    assert (daily.loc[daily["no_invoice"], "purchase_dollars"] == 0).all()
    assert mondays["invoice_lines"].max() == 3  # duplicate invoice line dropped
    _, rep2, _ = store_daily(files, "200")
    assert not rep2.suitable and any("identity" in r for r in rep2.reasons)
    assert any("history" in r for r in rep2.reasons)
    summary = scan_stores(files)
    assert summary.index[0] == "100"


def test_iowa_rejects_non_official_schema(tmp_path):
    d = tmp_path / "bad"
    d.mkdir()
    pd.DataFrame({"date": ["2025-01-01"], "sales": [1]}).to_csv(d / "x.csv", index=False)
    with pytest.raises(ValueError, match="not the official"):
        store_daily(export_files(d), "1")


def test_procurement_pipeline_end_to_end_on_fixture(tmp_path):
    d = tmp_path / "iowa"
    d.mkdir()
    write_iowa_fixture(d / "p.csv")
    daily, _, _ = store_daily(export_files(d), "100")
    hist = daily[["date", "purchase_dollars"]]
    bundle = build_forecast_bundle(hist, ("purchase_dollars",), dataset="fixture",
                                   data_label="FIXTURE", cfg=EvaluationConfig(holdout_folds=3),
                                   models=("seasonal_naive", "weekday_mean_12w", "ridge"))
    actions, levels = procurement_actions(bundle, hist, ProcurementConfig())
    assert levels["p50"] <= levels["p80"] <= levels["p95"]
    cap = Constraint("cash available", "metric:reserved_cash:mean", "max", levels["p80"], "USD")
    outs = evaluate_actions(actions, lambda a, dr: simulate_procurement(bundle, a, dr),
                            make_draws(bundle, 1000), (cap,), UNITS)
    p95 = next(o for o in outs if o.action.action_id == "reserve_p95")
    assert not p95.feasible
    rec = rank(outs, Objective.MIN_CASH_SHORTAGE_RISK, horizon_days=28, evidence={})
    assert rec.recommended_action_id in (None, "reserve_p80", "reserve_p50", "keep_last_budget")


def test_procurement_shortage_matches_hand_calculation():
    dates = pd.date_range("2026-01-01", periods=28)
    bundle = ForecastBundle("t", "T", pd.Timestamp("2025-12-31"), 28, {"purchase_dollars":
        TargetForecast("purchase_dollars", "m", dates, np.full(28, 100.0), np.zeros((4, 28)))})
    hist = pd.DataFrame({"date": pd.date_range("2025-12-04", periods=28),
                         "purchase_dollars": 90.0})
    actions, _ = procurement_actions(bundle, hist, ProcurementConfig())
    outs = evaluate_actions(actions, lambda a, dr: simulate_procurement(bundle, a, dr),
                            make_draws(bundle, 200), (), UNITS)
    keep = next(o for o in outs if o.action.is_baseline)  # budget 2,520 < spend 2,800
    assert keep.metrics["cash_shortage"].mean == 1.0
    assert keep.metrics["expected_shortfall"].mean == pytest.approx(280.0)
    p50 = next(o for o in outs if o.action.action_id == "reserve_p50")
    assert p50.metrics["cash_shortage"].mean == 0.0 and p50.metrics["idle_cash"].mean == 0.0


# ------------------------------------------------------------------------------ profile
def test_profile_requires_confirmation_and_valid_geography():
    with pytest.raises(ValueError, match="state"):
        BusinessProfile("x", "restaurant", "ZZ", "max_expected_profit")
    p = BusinessProfile("x", "restaurant", "tx", "max_expected_profit")
    with pytest.raises(ValueError, match="confirm"):
        p.require_confirmation()


def test_mapping_and_capabilities_never_invent_metrics(tmp_path):
    raw = pd.DataFrame({"Day": pd.date_range("2024-01-01", periods=600).astype(str),
                        "Purchases ($)": 100.0, "Store": "A1"})
    xlsx = tmp_path / "upload.xlsx"
    raw.to_excel(xlsx, index=False)
    frame, meta = apply_mapping(read_upload(xlsx), ColumnMapping(
        {"Day": "date", "Purchases ($)": "purchase_dollars", "Store": "business_id"}))
    assert meta["business_id"] == ["A1"] and "revenue" not in frame
    cap = capabilities(frame, BusinessProfile("x", "retail", "IA", "min_cash_shortage_risk",
                                              confirmed_by_user=True))
    assert "procurement_forecast" in cap.supported
    assert "procurement_budget_decisions" in cap.supported
    assert "inventory_policy_decisions" in cap.unsupported
    assert "revenue_forecast" in cap.unsupported
    assert any("not customer demand" in n for n in cap.notes)
    with pytest.raises(ValueError, match="unknown roles"):
        ColumnMapping({"Day": "date", "x": "profit"})
    with pytest.raises(ValueError, match="exactly one"):
        ColumnMapping({"Day": "date", "a": "revenue", "b": "revenue"})


def test_short_history_blocks_forecasting_analyses():
    frame = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=100), "revenue": 1.0,
                          "variable_costs": 1.0, "fixed_costs": 1.0})
    cap = capabilities(frame, BusinessProfile("x", "restaurant", "TX", "max_expected_profit"))
    assert "revenue_forecast" in cap.unsupported and "504" in cap.unsupported["revenue_forecast"]
    assert "restaurant_financial_decisions" in cap.supported


# ------------------------------------------------------------------------------ sources
def test_source_selection_status_is_literal():
    keys = {s.key: s for s in CATALOG}
    assert keys["nessie"].data_origin == "SIMULATED"
    assert keys["sec_edgar"].status == "documented_not_implemented"
    chosen = select_sources("restaurant", ("benchmark", "cost_inflation", "transaction_import_demo"))
    usable = {s["key"] for s in chosen if s["usable_now"]}
    assert usable == {"fred", "kwick365"}  # Nessie is selected for import demo but not usable
    assert "iowa_liquor" not in {s["key"] for s in chosen}  # wrong industry


def test_fred_observation_is_point_in_time():
    v = pd.DataFrame({
        "observed_at": pd.to_datetime(["2026-08-01", "2026-09-01", "2026-09-01"]),
        "realtime_start": pd.to_datetime(["2026-09-11", "2026-10-15", "2026-11-12"]),
        "realtime_end": pd.to_datetime(["2262-04-11", "2026-11-11", "2262-04-11"]),
        "value": [100.0, 101.0, 102.0], "retrieved_at_utc": "r"})
    v["published_at"] = v.groupby("observed_at")["realtime_start"].transform("min")
    o = fred_observation(v, "X", pd.Timestamp("2026-10-20"), "idx", "US", "terms")
    assert o.observed_at == "2026-09-01" and o.value == 101.0 and o.available_at == "2026-10-15"
    assert o.freshness_days == 49 and o.vintage_mode == "point_in_time"
    assert fred_observation(v, "X", pd.Timestamp("2026-09-01"), "idx", "US", "t") is None
