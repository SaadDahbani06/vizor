"""Milestone 6: fast answering architecture (verified fast facts -> deterministic engines -> one
short model explanation -> model tool loop only for open questions), circuit breaker and deadline,
read-only tool cache, and proactive iMessage alerts with DETAILS / OPTIONS replies."""
import time
from types import SimpleNamespace

import pytest
from google.genai import errors, types

from decisiondesk.agent import agent as agmod
from decisiondesk.agent import gemini as gem
from decisiondesk.agent.agent import DecisionAgent
from decisiondesk.agent.tools import BusinessSession, run_tool
from decisiondesk.messaging.service import MessagingService, MessagingStore, Owner, SimulatorGateway

FAST = ("seasonal_naive", "weekday_mean_12w", "historical_mean_28d", "ridge")


@pytest.fixture(scope="module")
def rs():
    return BusinessSession.from_demo("restaurant", models=FAST, n_sims=300)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(gem, "BUDGET", gem.CallBudget(1000))
    monkeypatch.setattr(gem, "_BROKEN", {})
    monkeypatch.setenv("MSRD_ROUTING", "fast")
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "")


def say(text):
    content = types.Content(role="model", parts=[types.Part.from_text(text=text)])
    return SimpleNamespace(candidates=[SimpleNamespace(content=content, finish_reason="STOP")],
                           function_calls=None, text=text)


class FakeModels:
    def __init__(self, script):
        self.script, self.requests = list(script), []

    def generate_content(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": list(contents), "config": config})
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def fake_client(*script):
    return SimpleNamespace(models=FakeModels(script))


def api_error(code, status):
    return errors.APIError(code, {"error": {"code": code, "status": status, "message": "x"}})


# ------------------------------------------------------------------ fast facts
@pytest.mark.parametrize("q,needle", [
    ("How much revenue did we make yesterday?", "Yesterday"), ("Which product sold the most?", "Tacos"),
    ("How much cash do we have?", "Cash on hand"), ("What's our current taco price?", "$12.75"),
    ("How are we doing this month?", "This month so far"), ("What bills are due?", "bills are due")])
def test_simple_questions_are_answered_instantly_from_records(rs, q, needle):
    r = DecisionAgent(rs, mode="offline").ask(q)
    assert r.path == "fast" and [c["name"] for c in r.tool_calls] == ["get_key_numbers"]
    assert needle in r.text and r.grounded and r.elapsed_s < 2


def test_fast_numbers_match_the_records(rs):
    k = run_tool(rs, "get_key_numbers", {})
    h = rs.history
    assert k["yesterday"]["revenue"] == pytest.approx(h["revenue"].iloc[-1], abs=0.01)
    assert k["cash"]["balance"] == pytest.approx(h["cash_balance"].iloc[-1], abs=1)
    assert {p["product"] for p in k["products_last_28_days"]} == {"Tacos", "Burritos", "Quesadillas", "Nachos", "Drinks"}


def test_scenarios_and_follow_ups_still_use_the_engines(rs):
    a = DecisionAgent(rs, mode="offline")
    assert a.ask("What if taco prices rise by 5%?").tool_calls[0]["name"] == "simulate_product_scenario"
    r = a.ask("What about 3% instead?")
    assert r.path == "engine" and r.tool_calls[0]["input"]["price_change_pct"] == 3.0
    assert a.ask("How much cash will we have next month?").path != "fast"  # outlook -> cash simulation


def test_read_only_tools_are_cached_scenarios_are_not(rs):
    a, b = run_tool(rs, "get_key_numbers", {}), run_tool(rs, "get_key_numbers", {})
    assert a is b and a["run_id"] == b["run_id"]
    x = run_tool(rs, "simulate_product_scenario", {"product": "tacos", "price_change_pct": 2.0})
    y = run_tool(rs, "simulate_product_scenario", {"product": "tacos", "price_change_pct": 2.0})
    assert x["run_id"] != y["run_id"]
    rs.state.product_scenario = None


# ------------------------------------------------------------------ language model use
def test_fast_question_makes_no_model_request(rs):
    client = fake_client()
    r = DecisionAgent(rs, client=client).ask("How much cash do we have?")
    assert r.path == "fast" and client.models.requests == []


def test_engine_answer_is_explained_in_one_request_without_tools(rs):
    client = fake_client(say("Raising taco prices looks positive overall, but it is an estimate and a loss is "
                             "possible. It needs your approval."))
    r = DecisionAgent(rs, client=client).ask("What happens if taco prices rise by 5%?")
    assert r.path == "engine+gemini" and r.mode == "gemini" and r.grounded
    assert len(client.models.requests) == 1 and client.models.requests[0]["config"].tools is None
    assert r.tool_calls[0]["name"] == "simulate_product_scenario"
    rs.state.product_scenario = None


def test_unverified_explanation_falls_back_to_the_calculation(rs):
    client = fake_client(say("Profit will rise by $9,999 for sure."))
    r = DecisionAgent(rs, client=client).ask("What happens if taco prices rise by 5%?")
    assert r.path == "engine" and "unverified" in r.fallback_reason and r.grounded
    assert "$9,999" not in r.text and "Tacos price +5%" in r.text
    rs.state.product_scenario = None


def test_open_question_uses_the_model_tool_loop(rs):
    client = fake_client(say("Labor and ingredients are your largest costs; I would look at scheduling first."))
    r = DecisionAgent(rs, client=client).ask("Write a short note on where I could save money.")
    assert r.path == "gemini" and client.models.requests[0]["config"].tools is not None


def test_circuit_breaker_skips_a_failing_model(rs):
    client = fake_client(api_error(503, "UNAVAILABLE"))
    a = DecisionAgent(rs, client=client)
    r1 = a.ask("What happens if taco prices rise by 5%?")
    assert r1.path == "engine" and len(client.models.requests) == 1  # explanation failed; answer kept
    t0 = time.monotonic()
    r2 = a.ask("What about 3% instead?")
    assert r2.path == "engine" and len(client.models.requests) == 1  # breaker open: no second request
    assert time.monotonic() - t0 < 5
    rs.state.product_scenario = None


def test_outage_on_open_question_gives_key_numbers_not_a_menu(rs):
    client = fake_client(api_error(429, "RESOURCE_EXHAUSTED"))
    r = DecisionAgent(rs, client=client).ask("Write a two-sentence update for my business partner.")
    assert r.mode == "offline" and "key numbers" in r.text and "This month so far" in r.text
    assert "I can answer:" not in r.text and r.grounded


def test_deadline_stops_the_tool_loop(rs, monkeypatch):
    monkeypatch.setattr(agmod, "ANSWER_DEADLINE_S", -1.0)
    client = fake_client(say("never used"))
    r = DecisionAgent(rs, client=client).ask("Write a short note on where I could save money.")
    assert r.mode == "offline" and "time limit" in (r.fallback_reason or "") and client.models.requests == []


# ------------------------------------------------------------------ proactive iMessage
def _briefing(level="decision_needed", kind="low_stock"):
    return {"date": "2026-11-13", "stock_watch": [{"what": "Tortillas", "days_of_supply": 1.5,
                                                   "next_delivery_in_days": 3}],
            "alerts": [{"kind": kind, "severity": "high", "level": level, "product": "Tacos",
                        "title": "Tortillas may run out before the next delivery",
                        "detail": "Enough for about 1.5 days at the expected pace; the next delivery is in 3 days.",
                        "next_step": "Think about a small extra order (try it in the Decision Lab first).",
                        "basis": "forecast", "page": "Decision Lab"},
                       {"kind": "bills", "severity": "low", "level": "routine", "product": None,
                        "title": "3 bills due this week", "detail": "$4,978 in total.", "next_step": "",
                        "basis": "observed", "page": "My Business"}],
            "bills_due_7d_total": 4978}


def _svc(tmp_path, rs):
    gw = SimulatorGateway()
    svc = MessagingService(gw, MessagingStore(tmp_path / "s.json"), lambda k: rs, agent_mode="offline")
    svc.sync_recipients()
    gw.simulate_inbound("hi")
    svc.poll_once()
    return svc, gw, SimulatorGateway.DEMO_HANDLE


def test_decision_needed_alerts_need_opt_in_and_are_sent_once(tmp_path, rs):
    svc, gw, h = _svc(tmp_path, rs)
    assert svc.send_attention(h, _briefing()) == []  # no alert opt-in
    svc.set_alert_consent(h, True)
    sent = svc.send_attention(h, _briefing())
    assert len(sent) == 1 and sent[0]["kind"] == "attention:decision_needed"  # routine bill not texted
    text = gw.sent[-1]["text"]
    assert text.startswith("Decision needed: Tortillas") and "DETAILS" in text and "OPTIONS" in text
    assert "Nothing is ordered or changed automatically" in text
    assert svc.send_attention(h, _briefing()) == []  # once per alert per day


def test_important_alerts_only_at_the_important_level(tmp_path, rs):
    svc, _gw, h = _svc(tmp_path, rs)
    svc.set_alert_consent(h, True)
    assert svc.send_attention(h, _briefing("important", "stockout_risk")) == []
    svc.store.owner(h).notify_level = "important"
    assert len(svc.send_attention(h, _briefing("important", "stockout_risk"))) == 1


def test_details_and_options_replies_follow_the_last_alert_and_survive_restart(tmp_path, rs):
    svc, gw, h = _svc(tmp_path, rs)
    svc.set_alert_consent(h, True)
    svc.send_attention(h, _briefing())
    restarted = MessagingService(gw, MessagingStore(tmp_path / "s.json"), lambda k: rs, agent_mode="offline")
    assert restarted.store.owner(h).last_alert["kind"] == "low_stock"  # persisted
    gw.simulate_inbound("DETAILS")
    [rec] = restarted.poll_once()
    assert rec["kind"] == "details" and "1.5 days" in rec["text"] and "OPTIONS" in rec["text"]
    gw.simulate_inbound("OPTIONS")
    [rec] = restarted.poll_once()
    assert rec["kind"].startswith("reply:") and "Tacos" in rec["text"] and "**" not in rec["text"]
    assert not rec["text"].startswith("Offline analyst")


def test_stop_blocks_proactive_alerts(tmp_path, rs):
    svc, gw, h = _svc(tmp_path, rs)  # gw: simulated phone
    svc.set_alert_consent(h, True)
    gw.simulate_inbound("STOP")
    svc.poll_once()
    assert svc.send_attention(h, _briefing()) == []


def test_old_state_files_load_and_drop_retired_alert_types(tmp_path):
    import json
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"owners": {"+15550100000": {"handle": "+15550100000", "authorized": True,
                                                         "alert_prefs": ["cash_shortfall", "economic_release"],
                                                         "retired_field": 1}}}))
    o = MessagingStore(p).owner("+15550100000")
    assert o.alert_prefs == ["cash_shortfall"] and o.notify_level == "decision_needed" and o.last_alert is None
    assert isinstance(o, Owner)


def test_briefing_alerts_carry_a_notification_level(rs):
    from decisiondesk.briefing import LEVELS, daily_briefing
    b = daily_briefing(rs, with_action=False)
    assert all(a["level"] in ("routine", "important", "decision_needed") for a in b["alerts"])
    assert LEVELS["low_stock"] == "decision_needed" and LEVELS["sales_decline"] == "routine"
