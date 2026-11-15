"""Milestone 5.3: Local Market Intelligence (verified public menu prices) and the Daily Owner
Briefing / action checklist."""
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from decisiondesk import market as mk
from decisiondesk.agent.agent import DecisionAgent
from decisiondesk.agent.grounding import ungrounded_numbers
from decisiondesk.agent.tools import BusinessSession, run_tool
from decisiondesk.briefing import daily_briefing

FAST = ("seasonal_naive", "weekday_mean_12w", "historical_mean_28d", "ridge")
AS_OF = date(2026, 11, 14)
APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")


@pytest.fixture(scope="module")
def m():
    return mk.load_market()


@pytest.fixture(scope="module")
def rs():
    return BusinessSession.from_demo("restaurant", models=FAST, n_sims=300)


# ------------------------------------------------------------------ dataset integrity
def test_every_price_is_sourced_dated_and_from_an_official_menu(m):
    p = m.prices
    assert len(p) >= 20 and p["restaurant"].nunique() >= 6
    assert p["source_url"].str.startswith("https://").all()
    assert (p["source_type"] == "official restaurant website").all()
    assert all(isinstance(d, date) for d in p["verified_date"])
    assert set(p["match_level"]) <= {"exact", "constructed", "similar", "unmatched"}
    rep = p[p["representative"]]
    assert not rep.duplicated(["restaurant", "matches_product"]).any()  # one row per restaurant/product


def test_research_log_separates_unverified_restaurants(m):
    log = m.log
    unverified = log[~log["price_status"].str.startswith("verified")]
    assert {"Granny's Tacos", "Cuantos Tacos", "Torchy's Tacos"} <= set(unverified["restaurant"])
    assert (unverified["used_in_comparisons"] == "no").all()
    assert not set(unverified["restaurant"]) & set(m.prices["restaurant"])  # never priced from guesses


def test_market_data_is_public_and_separate_from_private_records():
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    inside = Path(mk.MARKET_DIR).resolve().relative_to(repo).parts   # judge the path inside the repo only
    assert inside == ("data", "market") and "private" not in inside
    import inspect

    from decisiondesk.decision import product_scenario
    src = inspect.getsource(product_scenario)
    assert "import market" not in src and "decisiondesk.market" not in src  # never feeds the simulation


# ------------------------------------------------------------------ comparability
def test_single_tacos_are_normalized_to_a_plate_of_three(m):
    c = mk.compare("tacos", 12.75, market=m, as_of=AS_OF)
    by = {x["restaurant"]: x for x in c["comparables"]}
    assert by["El Chilito"]["comparable_price"] == pytest.approx(3 * 3.80)
    assert by["Taco Joint"]["comparable_price"] == pytest.approx(3 * 4.95)
    excluded = {x["item"]: x["excluded_because"] for x in c["excluded"]}
    assert any("different serving" in v for k, v in excluded.items() if "Taco Dinner" in k)
    assert any("Taco Plate" in k for k in excluded)  # 2 tacos + sides is not a plate of three


def test_peer_statistics_position_and_warnings(m):
    c = mk.compare("tacos", 12.75, market=m, as_of=AS_OF)
    prices = sorted(x["comparable_price"] for x in c["comparables"])
    assert c["peer_count"] == len(prices) >= 3
    assert c["peer_median"] == pytest.approx(np.median(prices), abs=0.01)
    assert c["peer_min"] == prices[0] and c["peer_max"] == prices[-1]
    assert c["position"] == "within the comparable range"
    assert c["peers_cheaper"] + c["peers_more_expensive"] <= c["peer_count"]
    assert any("Small sample" in w for w in c["warnings"])
    low = mk.compare("tacos", 5.00, market=m, as_of=AS_OF)
    assert low["position"] == "below the comparable range"


def test_too_few_comparables_is_reported_not_guessed(m):
    c = mk.compare("burritos", 13.25, market=m, as_of=AS_OF)
    assert c["peer_count"] == 0 and not c["sufficient"] and "peer_median" not in c
    assert any("not enough" in w for w in c["warnings"])


def test_similar_servings_only_on_request_and_filters_apply(m):
    base = mk.compare("tacos", 12.75, market=m, as_of=AS_OF)
    wider = mk.compare("tacos", 12.75, market=m, as_of=AS_OF, include_similar=True)
    assert wider["peer_count"] == base["peer_count"] + 1  # Guero's taquito
    shops = mk.compare("tacos", 12.75, market=m, as_of=AS_OF, concepts=["counter-service taqueria"])
    assert {x["concept"] for x in shops["comparables"]} == {"counter-service taqueria"}


def test_stale_prices_are_flagged(m):
    later = mk.compare("tacos", 12.75, market=m, as_of=date(2027, 3, 1))
    assert all(x["stale"] for x in later["comparables"])
    assert any("older than" in w for w in later["warnings"])
    assert mk.freshness(m, date(2027, 3, 1))["stale"]


def test_chains_are_excluded_by_default(tmp_path, m):
    p = m.prices.copy()
    row = p.iloc[0].copy()
    row["restaurant"], row["ownership"], row["price"] = "Big Chain Tacos", "national chain", 2.00
    pd.concat([p, row.to_frame().T]).to_csv(tmp_path / mk.PRICE_FILE, index=False)
    m.log.to_csv(tmp_path / mk.LOG_FILE, index=False)
    m2 = mk.load_market(tmp_path)
    assert "Big Chain Tacos" not in {x["restaurant"] for x in mk.compare("tacos", 12.75, market=m2, as_of=AS_OF)["comparables"]}
    assert "Big Chain Tacos" in {x["restaurant"] for x in mk.compare("tacos", 12.75, market=m2, as_of=AS_OF,
                                                                     include_chains=True)["comparables"]}


def test_bad_rows_are_rejected(tmp_path, m):
    p = m.prices.copy()
    p.loc[0, "source_url"] = ""
    p.to_csv(tmp_path / mk.PRICE_FILE, index=False)
    with pytest.raises(ValueError, match="source_url"):
        mk.load_market(tmp_path)


def test_market_never_recommends_a_price(m):
    c = mk.compare("nachos", 10.75, market=m, as_of=AS_OF)
    text = " ".join(map(str, c.values())).lower()
    assert "you should raise" not in text and "recommend" not in text.replace("not a reason", "")
    assert "do not show how your customers would react" in c["not_a_recommendation"]


# ------------------------------------------------------------------ briefing
def test_briefing_numbers_match_the_records(rs):
    b = daily_briefing(rs, market_as_of=AS_OF)
    h = rs.history
    last = h.iloc[-1]
    assert b["date"] == str(last["date"].date())
    assert b["yesterday"]["revenue"] == pytest.approx(last["revenue"], abs=0.01)
    past = h[h["date"].isin([last["date"] - pd.Timedelta(days=7 * k) for k in range(1, 5)])]
    assert b["yesterday"]["revenue_typical"] == pytest.approx(past["revenue"].mean(), abs=0.01)
    s = rs.product_context().history.sales
    yd = s[s["date"] == last["date"]]
    assert sum(p["units_sold"] for p in b["products"]) == yd["units_sold"].sum()


def test_briefing_flags_mix_shifts_not_just_quiet_days(rs):
    b = daily_briefing(rs, market_as_of=AS_OF)
    g = b["yesterday"]["guests_change_pct"]
    for p in b["underperforming"]:
        assert p["change_pct"] <= -15 and p["change_pct"] - g <= -10
    flagged = {p["product"] for p in b["underperforming"]}
    for p in b["products"]:
        if p["product"] not in flagged and p["units_typical"] >= 10 and p["change_pct"] is not None:
            assert p["change_pct"] > -15 or p["change_pct"] - g > -10


def test_briefing_text_is_grounded_and_action_needs_approval(rs):
    b = daily_briefing(rs, market_as_of=AS_OF)
    assert ungrounded_numbers(b["text"], [{k: v for k, v in b.items() if k != "text"}], []) == []
    a = b["action"]
    assert a["requires_approval"] and "Needs your approval" in b["text"]
    assert a["verify"] and any("assumption" in v for v in a["verify"])
    if a.get("impact"):
        lo, hi = a["impact"]["range_90"]
        assert lo <= a["impact"]["profit_change"] <= hi
    assert "not causal" in b["note"]


def test_briefing_works_without_product_records():
    r = BusinessSession.from_demo("retail", models=FAST, n_sims=300)
    b = daily_briefing(r)
    assert b["products"] == [] and b["action"] is None and b["volume_label"] == "Units sold"
    assert ungrounded_numbers(b["text"], [{k: v for k, v in b.items() if k != "text"}], []) == []


# ------------------------------------------------------------------ analyst and UI
def test_analyst_routes_market_and_briefing_questions(rs):
    a = DecisionAgent(rs, mode="offline")
    r = a.ask("How do my taco prices compare with nearby restaurants?")
    assert [c["name"] for c in r.tool_calls] == ["get_local_market_prices"] and r.grounded
    assert "verified comparables" in r.text and "not a reason" in r.text
    r = a.ask("What needs attention today?")
    assert [c["name"] for c in r.tool_calls] == ["get_daily_briefing"] and r.grounded
    out = run_tool(rs, "get_local_market_prices", {"product": "burrito"})
    assert out["available"] and not out["sufficient"]


def test_local_market_page_opens_the_decision_lab(monkeypatch):
    monkeypatch.setenv("MSRD_FAST", "1")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert any("Business attention" in m.value for m in at.markdown)
    at.radio(key="nav").set_value("Local Market").run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "4 nearby restaurants charge" in text and "middle price $14.93" in text
    assert "not a reason on their own to change a price" in text
    at.button(key="lm_go_Lower").click().run()
    assert at.radio(key="nav").value == "Decision Lab"
    assert at.radio(key="seg_sl_mode").value == "Change a price"
    assert at.slider(key="sl_price").value == -5.0 and at.selectbox(key="sl_product").value == "Tacos"
    assert not at.exception
