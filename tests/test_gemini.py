"""Gemini analyst tests (MOCKED: a scripted fake client, no network, no key).

The fake implements ``client.models.generate_content`` and returns real ``google.genai.types``
Content/FunctionCall objects, so the provider's manual function-calling loop, history handling
(thought-signature-preserving model turns), function-response IDs, grounding retry, model
fallback and offline fallback run exactly as they would against the API. Live Gemini tests live
in test_live_integrations.py and only run with MSRD_LIVE=1.
"""
from types import SimpleNamespace

import pytest
from google.genai import errors, types

from decisiondesk.agent import gemini as gem
from decisiondesk.agent.agent import DecisionAgent, _resolve_mode
from decisiondesk.agent.tools import TOOL_SCHEMAS, BusinessSession

FAST = ("seasonal_naive", "weekday_mean_12w", "historical_mean_28d", "ridge")


@pytest.fixture(scope="module")
def session():
    return BusinessSession.from_demo("restaurant", models=FAST, n_sims=300)


@pytest.fixture(autouse=True)
def _fresh_budget(monkeypatch):
    monkeypatch.setattr(gem, "BUDGET", gem.CallBudget(1000))


def call(name, args, cid="call_1"):
    fc = types.FunctionCall(name=name, args=args, id=cid)
    content = types.Content(role="model", parts=[types.Part(function_call=fc)])
    return SimpleNamespace(candidates=[SimpleNamespace(content=content, finish_reason="STOP")],
                           function_calls=[fc], text=None)


def say(text):
    content = types.Content(role="model", parts=[types.Part.from_text(text=text)])
    return SimpleNamespace(candidates=[SimpleNamespace(content=content, finish_reason="STOP")],
                           function_calls=None, text=text)


def last_result(contents):
    for c in reversed(contents):
        for p in c.parts or []:
            if p.function_response is not None:
                return p.function_response.response
    raise AssertionError("no function response in history")


def user_texts(contents):
    return [p.text for c in contents if c.role == "user" for p in c.parts or [] if p.text]


class FakeModels:
    """Each script step is a response, an exception, or a callable(contents) -> response."""

    def __init__(self, script):
        self.script, self.requests = list(script), []

    def generate_content(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": list(contents), "config": config})
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step(contents) if callable(step) else step


def fake_client(*script):
    return SimpleNamespace(models=FakeModels(script))


def profit_text(contents):
    r = last_result(contents)["result"]
    return say(f"With food costs up {r['scenario']['food_cost_change_pct']:g}%, expected 28-day "
               f"profit with no action is ${r['baseline_expected_profit']:,.0f}. "
               f"Top option: {r['recommended_action']}.")


EVAL = "evaluate_restaurant_actions"


# ------------------------------------------------------------------ configuration
def test_mode_resolution(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    assert _resolve_mode(None) == "gemini"
    monkeypatch.setenv("MSRD_LLM_PROVIDER", "offline")
    assert _resolve_mode(None) == "offline"
    monkeypatch.setenv("MSRD_LLM_PROVIDER", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    assert _resolve_mode(None) == "offline"
    assert _resolve_mode(fake_client()) == "gemini"


def test_model_and_tool_configuration(session, monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "")
    agent = DecisionAgent(session, client=fake_client(), channel="imessage")
    g = agent.gemini
    assert agent.mode == "gemini" and agent.label == "Gemini analyst"
    assert g.model == gem.DEFAULT_MODEL == "gemini-3.8-flash"
    decls = g.config.tools[0].function_declarations
    assert len(decls) == len(TOOL_SCHEMAS) == 14
    assert "get_economic_indicators" not in {t["name"] for t in TOOL_SCHEMAS}  # no macro UI (5.4)
    assert {d.name for d in decls} == {t["name"] for t in TOOL_SCHEMAS}
    assert g.config.automatic_function_calling.disable is True
    assert "600 characters" in g.config.system_instruction  # iMessage channel note
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.7-flash")
    assert DecisionAgent(session, client=fake_client()).gemini.model == "gemini-3.7-flash"


# ------------------------------------------------------------------ function calling
def test_function_call_executes_real_tool_and_answer_is_grounded(session):
    client = fake_client(call(EVAL, {"food_cost_change_pct": 8}, "fc_1"), profit_text)
    agent = DecisionAgent(session, client=client)
    reply = agent.ask("What should I do if food costs rise 8%?")
    assert reply.mode == "gemini" and reply.grounded and not reply.retried
    assert [c["name"] for c in reply.tool_calls] == [EVAL]
    assert reply.tool_calls[0]["output"]["run_id"]
    assert session.state.food_cost_change_pct == 8  # scenario state updated by the real tool
    second = client.models.requests[1]["contents"]
    assert second[1].role == "model" and second[1].parts[0].function_call.name == EVAL
    fr = second[2].parts[0].function_response
    assert fr.id == "fc_1" and fr.name == EVAL and "baseline_expected_profit" in fr.response["result"]
    assert f"{reply.tool_calls[0]['output']['baseline_expected_profit']:,.0f}" in reply.text


def test_multi_turn_memory_and_scenario_update(session):
    def second_turn(contents):
        texts = user_texts(contents)
        assert texts[0].startswith("What should I do if food costs rise 8%")  # memory kept
        return call(EVAL, {"food_cost_change_pct": 12}, "fc_2")

    client = fake_client(call(EVAL, {"food_cost_change_pct": 8}, "fc_1"), profit_text,
                         second_turn, profit_text)
    agent = DecisionAgent(session, client=client)
    r1 = agent.ask("What should I do if food costs rise 8%?")
    r2 = agent.ask("And if it is 12% instead?")
    assert r1.grounded and r2.grounded and session.state.food_cost_change_pct == 12
    assert "12%" in r2.text
    assert len(agent.gemini.contents) == 8  # user, call, response, answer x 2
    assert len(agent.tool_outputs) == 2


def test_tool_errors_are_returned_to_the_model(session):
    def recover(contents):
        assert "error" in last_result(contents)
        return say("That tool is not available; I can compare restaurant actions instead.")

    client = fake_client(call("no_such_tool", {}), recover)
    reply = DecisionAgent(session, client=client).ask("Run the magic tool")
    assert reply.tool_calls[0]["error"] and reply.grounded


# ------------------------------------------------------------------ numerical verification
def test_ungrounded_numbers_trigger_one_regeneration(session):
    client = fake_client(call(EVAL, {"food_cost_change_pct": 8}),
                         say("Expected profit is $987,654 next month."), profit_text)
    reply = DecisionAgent(session, client=client).ask("What if food costs rise 8%?")
    assert reply.retried and reply.grounded and "987,654" not in reply.text
    note = user_texts(client.models.requests[2]["contents"])[-1]
    assert "Automatic grounding check" in note and "987,654" in note


def test_persistently_ungrounded_reply_is_flagged(session):
    client = fake_client(say("Revenue will be $123,456."), say("Revenue will be $123,456."))
    reply = DecisionAgent(session, client=client).ask("How is revenue?")
    assert not reply.grounded and "Unverified numbers" in reply.text and "$123,456" in reply.ungrounded


# ------------------------------------------------------------------ failures and fallback
def api_error(code, status):
    return errors.APIError(code, {"error": {"code": code, "status": status, "message": "x"}})


def test_api_failure_falls_back_to_offline_analyst(session, monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "gemini-3.7-flash")
    client = fake_client(api_error(503, "UNAVAILABLE"), api_error(503, "UNAVAILABLE"))
    agent = DecisionAgent(session, client=client)
    reply = agent.ask("What should I do if food costs rise 10%?")
    assert reply.mode == "offline" and reply.grounded and reply.tool_calls
    assert reply.text.startswith("(Gemini unavailable: Gemini API error 503")
    assert agent.last_fallback and [r["model"] for r in client.models.requests] == [
        "gemini-3.8-flash", "gemini-3.7-flash"]


def test_capacity_error_switches_model_before_first_turn_only(session, monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "gemini-3.7-flash")
    client = fake_client(api_error(504, "DEADLINE_EXCEEDED"), say("Hello."),
                         api_error(503, "UNAVAILABLE"))
    agent = DecisionAgent(session, client=client)
    assert agent.ask("hi").mode == "gemini" and agent.gemini.model == "gemini-3.7-flash"
    assert agent.gemini.switched_from == "gemini-3.8-flash"
    r2 = agent.ask("again")  # history exists: no model switch, offline fallback instead
    assert r2.mode == "offline" and client.models.requests[-1]["model"] == "gemini-3.7-flash"


def test_auth_error_does_not_try_other_models(session):
    client = fake_client(api_error(400, "INVALID_ARGUMENT"))
    reply = DecisionAgent(session, client=client).ask("hi")
    assert reply.mode == "offline" and len(client.models.requests) == 1
    assert "400" in reply.fallback_reason


def test_transport_errors_and_local_budget(session, monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "")
    reply = DecisionAgent(session, client=fake_client(TimeoutError())).ask("hi")
    assert reply.mode == "offline" and "transport" in reply.fallback_reason
    monkeypatch.setattr(gem, "BUDGET", gem.CallBudget(0))
    client = fake_client(say("never reached"))
    reply = DecisionAgent(session, client=client).ask("hi")
    assert "budget" in reply.fallback_reason and client.models.requests == []


def test_empty_candidate_is_reported_not_invented(session):
    empty = SimpleNamespace(candidates=[SimpleNamespace(content=None, finish_reason="SAFETY")],
                            function_calls=None, text=None)
    reply = DecisionAgent(session, client=fake_client(empty)).ask("hi")
    assert "no answer" in reply.text and "SAFETY" in reply.text
