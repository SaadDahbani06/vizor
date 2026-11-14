"""Milestone 5: context-aware number verification, shared iMessage scenario, single poller."""
import json
import os
import subprocess
import sys

import pytest

from decisiondesk.agent.agent import DecisionAgent
from decisiondesk.agent.grounding import check_numbers, nearest_concept, ungrounded_numbers
from decisiondesk.agent.tools import BusinessSession, run_tool
from decisiondesk.messaging.shared import PollerLock, ScenarioStore, scenario_summary

FAST = ("seasonal_naive", "weekday_mean_12w", "historical_mean_28d", "ridge")


@pytest.fixture(scope="module")
def rs():
    return BusinessSession.from_demo("restaurant", models=FAST, n_sims=300)


# ------------------------------------------------------------------ verifier
def _live_like_reply(out, elasticity_text):
    top = next(a for a in out["actions"] if a["action_id"] == out["recommended_action"])
    lo, hi = top["change_90pct_range"]
    return (f"I recommend: {top['label']}.\n\nThis action yields an expected profit increase of "
            f"${top['change_vs_no_action']:,.0f} versus doing nothing (90% interval: ${lo:,.0f} to "
            f"${hi:,.0f}).\n\nUncertainty: if marketing cut demand loss is high (1.5%) or "
            f"{elasticity_text}, raising menu prices by 3% becomes the top action instead.")


def test_number_attached_to_the_wrong_concept_is_caught(rs):
    """Regression for the 2026-11-14 live reply: '-0.6' was called a price elasticity, but 0.6 in
    the tool output is the supplier success probability."""
    outs = [run_tool(rs, "get_assumptions", {}), run_tool(rs, "evaluate_restaurant_actions", {})]
    assert any(a["name"] == "supplier_success_probability" and a["value"] == 0.6
               for a in outs[1]["assumptions"])
    bad = _live_like_reply(outs[1], "price elasticity is low (-0.6)")
    assert ungrounded_numbers(bad, outs, []) == ["-0.6 (no tool value for 'elasticity')"]
    good = _live_like_reply(outs[1], "price elasticity is at its least sensitive bound (-0.4)")
    assert ungrounded_numbers(good, outs, []) == []
    verdicts = {r["text"]: r for r in check_numbers(bad, outs, [])}
    assert verdicts["1.5%"]["verdict"] == "grounded" and verdicts["1.5%"]["concept"] == "demand"


def test_concept_locality_rules():
    t = "Revenue $5,000 (90% interval: $4,000 to $6,000); elasticity -0.8; 28 days."
    pos = t.index("$4,000")
    assert nearest_concept(t, pos, pos + 6, money=True) == "interval"
    pos = t.index("-0.8")
    assert nearest_concept(t, pos, pos + 4) == "elasticity"
    pos = t.index("28")
    assert nearest_concept(t, pos, pos + 2) == "duration"
    t2 = "Tacos $1,198 (+94 units); Burritos $848 (+64 units)"
    pos = t2.index("$848")
    assert nearest_concept(t2, pos, pos + 4, money=True) != "demand"  # money is never a count


def test_value_only_rules_still_apply():
    out = {"profit_change": 957.3, "probability": 0.42}
    assert ungrounded_numbers("Profit rises $957; 42% of paths.", [out], []) == []
    assert ungrounded_numbers("Profit rises $123,456.", [out], []) == ["$123,456"]
    assert ungrounded_numbers("You said 15% so I used 15%.", [out], ["food up 15%"]) == []


QUESTIONS = ["Why did profit drop?", "Which of my five products is most profitable?",
             "What happens if burrito prices increase by 5%?", "Should I purchase more taco ingredients?",
             "Should I buy 200 extra tacos worth of ingredients?", "What if I lower drink prices by 10%?",
             "What if demand for nachos drops 20%?", "What if marketing spending is cut 50%?",
             "What should I do?", "What should I do if my food costs increase 15%?", "forecast revenue",
             "How is cash looking?", "What do current food-price indicators show?", "show assumptions",
             "give me an overview", "cut quesadilla ingredient orders by 40%"]


def test_offline_analyst_answers_product_questions_with_grounded_numbers(rs):
    agent = DecisionAgent(rs, mode="offline")
    tools = {}
    for q in QUESTIONS:
        r = agent.ask(q)
        assert r.grounded, (q, r.ungrounded)
        tools[q] = [c["name"] for c in r.tool_calls]
    assert tools["Why did profit drop?"] == ["explain_profit_change"]
    assert tools["Which of my five products is most profitable?"] == ["get_product_performance"]
    assert tools["What happens if burrito prices increase by 5%?"] == ["simulate_product_scenario"]
    assert tools["Should I purchase more taco ingredients?"] == ["simulate_product_scenario"]
    r = agent.ask("What if taco prices go up 10%?")
    assert "OUTSIDE the owner's approved limits" in r.text


def test_product_questions_on_a_business_without_products_explain_what_is_missing():
    r = BusinessSession.from_demo("retail", models=FAST, n_sims=300)
    reply = DecisionAgent(r, mode="offline").ask("Which product is most profitable?")
    assert "per product" in reply.text and reply.grounded


# ------------------------------------------------------------------ shared scenario
def test_scenario_store_versions_and_summary(tmp_path):
    store = ScenarioStore(tmp_path / "s.json")
    assert store.get("restaurant") is None
    r1 = store.publish("restaurant", {"food_cost_change_pct": 12.0, "starting_cash": 5000.0})
    r2 = store.publish("restaurant", {"food_cost_change_pct": 8.0})
    assert (r1["version"], r2["version"]) == (1, 2) and "food costs +8%" in r2["summary"]
    assert scenario_summary(None).startswith("default")
    store.clear("restaurant")
    assert store.get("restaurant") is None


def test_dashboard_and_worker_services_use_the_same_published_scenario(tmp_path, rs):
    from decisiondesk.messaging.service import (
        MessagingService,
        MessagingStore,
        Owner,
        SimulatorGateway,
    )
    store = ScenarioStore(tmp_path / "pub.json")
    rs.state.food_cost_change_pct = 30.0           # dashboard scenario NOT published
    try:
        dash_svc = MessagingService(SimulatorGateway(), MessagingStore(tmp_path / "a.json"),
                                    lambda k: rs, agent_mode="offline", scenarios=store)
        worker_base = BusinessSession.from_demo("restaurant", models=FAST, n_sims=300)
        worker_svc = MessagingService(SimulatorGateway(), MessagingStore(tmp_path / "b.json"),
                                      lambda k: worker_base, agent_mode="offline", scenarios=store)
        o = Owner("+15550100000")
        a, b = dash_svc.agent_for(o), worker_svc.agent_for(o)
        # nothing published: both use the default scenario, never a silent copy of the dashboard
        assert a.session.state.food_cost_change_pct == b.session.state.food_cost_change_pct == 0
        assert a.session.state.max_price_increase == 0.05
        store.publish("restaurant", {**json.loads(json.dumps(rs.state.__dict__))})
        a, b = dash_svc.agent_for(o), worker_svc.agent_for(o)
        assert a.session.state.food_cost_change_pct == b.session.state.food_cost_change_pct == 30
        assert dash_svc.scenario_source("restaurant")["version"] == 1
        assert rs.state is not a.session.state  # copies, not shared mutable state
        ra = a.ask("What should I do?")
        rb = b.ask("What should I do?")
        assert ra.tool_calls[0]["output"]["scenario"] == rb.tool_calls[0]["output"]["scenario"]
    finally:
        rs.state.food_cost_change_pct = 0.0


def test_reply_evidence_records_scenario_tools_and_analyst(tmp_path, rs):
    from decisiondesk.messaging.service import MessagingService, MessagingStore, SimulatorGateway
    store = ScenarioStore(tmp_path / "pub.json")
    store.publish("restaurant", {"food_cost_change_pct": 12.0})
    svc = MessagingService(SimulatorGateway(), MessagingStore(tmp_path / "m.json"), lambda k: rs,
                           agent_mode="offline", scenarios=store)
    svc.sync_recipients()
    svc.gateway.simulate_inbound("What should I do?")
    [rec] = svc.poll_once()
    ev = rec["evidence"]
    assert ev["analyst"] == "offline" and ev["scenario_version"] == 1 and "food costs +12%" in ev["scenario"]
    assert ev["tools"] == ["evaluate_restaurant_actions"] and ev["model"] is None


# ------------------------------------------------------------------ single poller
def test_poller_lock_allows_one_holder(tmp_path):
    path = tmp_path / "poller.lock"
    a, b = PollerLock("worker", path, ttl_s=60), PollerLock("dashboard", path, ttl_s=30)
    assert a.acquire() and a.held
    assert not b.acquire() and b.holder()["owner"] == "worker"
    assert a.heartbeat()
    a.release()
    assert a.holder() is None and b.acquire() and b.held
    b.release()


def test_stale_or_dead_lock_is_taken_over(tmp_path):
    path = tmp_path / "poller.lock"
    path.write_text(json.dumps({"owner": "worker", "pid": 999999, "token": "x", "heartbeat": 0,
                                "since": "then", "ttl_s": 3600}))
    assert PollerLock("dashboard", path).acquire()      # dead PID
    path.write_text(json.dumps({"owner": "dashboard", "pid": os.getpid(), "token": "y",
                                "heartbeat": 0, "since": "then", "ttl_s": 30}))
    assert PollerLock("worker", path).acquire()         # live PID, expired heartbeat


def test_worker_refuses_to_start_while_another_poller_runs(tmp_path):
    lock_path = tmp_path / "poller.lock"
    held = PollerLock("dashboard", lock_path, ttl_s=60)
    assert held.acquire()
    env = {**os.environ, "MSRD_POLLER_LOCK": str(lock_path), "PHOTON_API_KEY": "",
           "PHOTON_PROJECT_ID": "", "MSRD_MESSAGING_STATE": str(tmp_path / "state.json")}
    p = subprocess.run([sys.executable, "-m", "decisiondesk.messaging.worker", "run", "--once"],
                       capture_output=True, text=True, env=env, timeout=120, check=False)
    assert p.returncode != 0 and "Another messaging poller is active" in p.stderr
    assert "dashboard" in p.stderr
    held.release()
