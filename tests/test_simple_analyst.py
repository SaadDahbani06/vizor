"""Milestone 5.4: supplier cost shocks, structured briefing alerts, decision checklists, the
multi-turn analyst, and briefings by iMessage without a Messaging page."""
from datetime import date

import numpy as np
import pytest

from decisiondesk import briefing as br
from decisiondesk.agent.agent import DecisionAgent
from decisiondesk.agent.grounding import ungrounded_numbers
from decisiondesk.agent.tools import TOOL_SCHEMAS, BusinessSession, run_tool
from decisiondesk.decision.product_scenario import ProductScenario, baseline, compare, simulate
from decisiondesk.messaging.service import (
    DEFAULT_ALERT_KINDS,
    MessagingService,
    MessagingStore,
    Owner,
    SimulatorGateway,
)

FAST = ("seasonal_naive", "weekday_mean_12w", "historical_mean_28d", "ridge")
AS_OF = date(2026, 11, 14)


@pytest.fixture(scope="module")
def rs():
    return BusinessSession.from_demo("restaurant", models=FAST, n_sims=300)


@pytest.fixture(scope="module")
def ctx(rs):
    return rs.product_context()


@pytest.fixture(scope="module")
def brief(rs):
    return br.daily_briefing(rs, market_as_of=AS_OF)


# ------------------------------------------------------------------ supplier cost shock
def test_zero_ingredient_cost_change_is_exactly_the_baseline(ctx):
    a = simulate(ctx, ProductScenario("tacos", ingredient_cost_change_pct=0.0))
    b = baseline(ctx, 28, "tacos")
    np.testing.assert_array_equal(a.profit, b.profit)
    np.testing.assert_array_equal(a.cash, b.cash)
    assert ProductScenario("tacos", ingredient_cost_change_pct=0.0).is_baseline


def test_supplier_cost_increase_lowers_profit_not_demand(ctx):
    base = baseline(ctx, 28, "tacos")
    up = simulate(ctx, ProductScenario("tacos", ingredient_cost_change_pct=10.0))
    np.testing.assert_array_equal(up.demand, base.demand)       # customers see no change
    np.testing.assert_array_equal(up.sold, base.sold)
    np.testing.assert_allclose(up.revenue, base.revenue)
    c = compare(ctx, up, base)
    assert c["profit"]["change"] < 0 and c["probability_profit_improves"] == 0
    assert c["cash_flow"]["change"] < 0 and c["ending_inventory_value"]["change"] > 0  # stock revalued
    # Only costs move: the profit change is exactly the extra variable (ingredient) cost
    extra_cost = (up.variable.sum(1) - base.variable.sum(1)).mean()
    assert extra_cost > 0 and c["profit"]["change"] == pytest.approx(-extra_cost, rel=1e-6)
    larger = compare(ctx, simulate(ctx, ProductScenario("tacos", ingredient_cost_change_pct=20.0)), base)
    assert larger["profit"]["change"] == pytest.approx(2 * c["profit"]["change"], rel=0.02)  # linear
    assert "supplier ingredient costs +10%" in ProductScenario("tacos", ingredient_cost_change_pct=10).describe()
    with pytest.raises(ValueError):
        ProductScenario("tacos", ingredient_cost_change_pct=500)


def test_tool_accepts_the_cost_shock_and_keeps_it_for_follow_ups(rs):
    out = run_tool(rs, "simulate_product_scenario", {"product": "burritos", "ingredient_cost_change_pct": 8.0})
    assert out["scenario"]["ingredient_cost_change_pct"] == 8.0 and out["operating_profit"]["change"] < 0
    again = run_tool(rs, "simulate_product_scenario", {"product": "burritos", "price_change_pct": 5.0})
    assert again["scenario"]["ingredient_cost_change_pct"] == 8.0  # null keeps the current setting
    names = {t["name"] for t in TOOL_SCHEMAS}
    sim = next(t for t in TOOL_SCHEMAS if t["name"] == "simulate_product_scenario")
    assert "ingredient_cost_change_pct" in sim["input_schema"]["required"]
    assert "get_economic_indicators" not in names
    rs.state.product_scenario = None


# ------------------------------------------------------------------ briefing alerts
def test_alerts_are_few_grounded_labelled_and_actionable(brief):
    assert 0 < len(brief["alerts"]) <= br.MAX_ALERTS
    order = [br.SEVERITY_ORDER[a["severity"]] for a in brief["alerts"]]
    assert order == sorted(order)
    for a in brief["alerts"]:
        assert a["basis"] in ("observed", "forecast", "simulated")
        assert a["title"] and a["detail"] and a["next_step"]
        assert a["page"] in (None, "My Business", "Decision Lab")
    assert ungrounded_numbers(brief["text"], [{k: v for k, v in brief.items() if k != "text"}], []) == []
    kinds = {a["kind"] for a in brief["alerts"]}
    assert "sales_decline" in kinds  # demo: yesterday -31% vs a typical Friday


def test_alert_list_is_capped_and_cost_share_is_checked(rs, monkeypatch):
    monkeypatch.setattr(br, "COST_RATIO_PTS", -100.0)   # force the cost alert
    monkeypatch.setattr(br, "RISK_WATCH", 0.0)          # force stock-out alerts
    b = br.daily_briefing(rs, with_action=False)
    assert len(b["alerts"]) <= br.MAX_ALERTS and b["alerts_suppressed"] >= 0
    share = b["ingredient_cost_share"]
    assert set(share) == {"last_7_days_pct", "previous_28_days_pct", "change_points"}
    r = rs.product_context().history.recon
    last7 = r.tail(7)
    assert share["last_7_days_pct"] == pytest.approx(last7["food_cost"].sum() / last7["revenue"].sum() * 100, abs=0.05)
    worst = min(br.SEVERITY_ORDER[a["severity"]] for a in b["alerts"])
    monkeypatch.setattr(br, "MAX_ALERTS", 1)
    one = br.daily_briefing(rs, with_action=False)
    assert len(one["alerts"]) == 1 and br.SEVERITY_ORDER[one["alerts"][0]["severity"]] == worst
    assert one["alerts_suppressed"] == len(b["alerts"]) + b["alerts_suppressed"] - 1


def test_text_message_version_is_short_and_grounded(brief):
    sms = br.briefing_sms(brief)
    assert len(sms) < 900 and "STOP" in sms and "needs your approval" in sms
    assert ungrounded_numbers(sms, [{k: v for k, v in brief.items() if k != "text"}], []) == []


# ------------------------------------------------------------------ decision checklist
def test_checklist_has_every_part_and_needs_approval(ctx):
    sc = ProductScenario("tacos", price_change_pct=5.0)
    c = compare(ctx, simulate(ctx, sc), baseline(ctx, 28, "tacos"))
    a = br.decision_checklist(ctx, sc, c, [])
    assert a["requires_approval"] and a["action"] == "Tacos price +5%"
    for k in ("why", "impact", "uncertainty", "assumptions", "risks", "verify"):
        assert a[k], k
    lo, hi = a["impact"]["range_90"]
    assert lo <= a["impact"]["profit_change"] <= hi
    assert any("elasticity" in x for x in a["assumptions"])
    assert any("assumption" in v for v in a["verify"])
    cost = ProductScenario("tacos", ingredient_cost_change_pct=8.0)
    cc = compare(ctx, simulate(ctx, cost), baseline(ctx, 28, "tacos"))
    b = br.decision_checklist(ctx, cost, cc, [])
    assert any("supplier invoices" in v for v in b["verify"])
    assert not any("elasticity" in x for x in b["assumptions"])  # price sensitivity not relevant
    assert any("loss" in r for r in b["risks"])


def test_briefing_action_is_a_checklist(brief):
    a = brief["action"]
    assert a["requires_approval"] and a["why"] and a["uncertainty"] and a["verify"]
    assert "Why it might help" in brief["text"]


# ------------------------------------------------------------------ multi-turn analyst
def test_follow_ups_reuse_the_product_and_compare(rs):
    a = DecisionAgent(rs, mode="offline")
    r1 = a.ask("What if I raise burrito prices 5%?")
    r2 = a.ask("What about 3% instead?")
    assert r2.tool_calls[0]["input"]["product"] == "burritos"
    assert r2.tool_calls[0]["input"]["price_change_pct"] == 3.0 and r2.grounded
    r3 = a.ask("Which is safer?")
    assert r3.tool_calls == [] and "Burritos price +5%" in r3.text and "Burritos price +3%" in r3.text
    assert "Safer:" in r3.text and r3.grounded
    r4 = a.ask("Why would a price increase lower demand?")
    assert "assumed price sensitivity" in r4.text and r4.grounded
    assert r1.grounded


def test_follow_up_keeps_the_direction_and_the_lever(rs):
    a = DecisionAgent(rs, mode="offline")
    a.ask("What if supplier costs rise 8%?", {"page": "Decision Lab", "product": "nachos"})
    r = a.ask("And if it is 12% instead?")
    inp = r.tool_calls[0]["input"]
    assert inp["product"] == "nachos" and inp["ingredient_cost_change_pct"] == 12.0 and inp["price_change_pct"] == 0
    a.ask("What if I cut taco prices 5%?")
    r = a.ask("What about 3% instead?")
    assert r.tool_calls[0]["input"]["price_change_pct"] == -3.0  # still a cut


def test_food_cost_follow_up_reruns_the_business_engine(rs):
    a = DecisionAgent(rs, mode="offline")
    a.ask("What should I do if my food costs increase 8%?")
    r = a.ask("And if the increase is 12% instead?")
    assert r.tool_calls[0]["name"] == "evaluate_restaurant_actions"
    assert r.tool_calls[0]["input"]["food_cost_change_pct"] == 12.0 and r.grounded


def test_page_context_and_briefing_questions(rs):
    a = DecisionAgent(rs, mode="offline")
    r = a.ask("What if I raise the price 4%?", {"page": "Decision Lab", "product": "Quesadillas"})
    assert r.tool_calls[0]["input"]["product"] == "quesadillas"
    r = a.ask("Why did revenue fall yesterday?")
    assert [c["name"] for c in r.tool_calls] == ["get_daily_briefing"] and r.grounded
    assert "not the cause" in r.text
    r = a.ask("What should I focus on tomorrow?")
    assert "What to focus on" in r.text and "Needs your approval" in r.text and r.grounded
    r = a.ask("What is inflation doing?")
    assert r.tool_calls == [] and "not part of" in r.text and r.grounded


def test_dashboard_context_reaches_the_language_model_as_text_only():
    from decisiondesk.agent.agent import _with_context
    q = _with_context("What about 3% instead?", {"page": "Decision Lab", "product": "Tacos"})
    assert q.endswith("What about 3% instead?") and "Decision Lab" in q and "not a number source" in q
    assert _with_context("hi", None) == "hi"


# ------------------------------------------------------------------ briefing by iMessage
def _svc(tmp_path, rs):
    gw = SimulatorGateway()
    svc = MessagingService(gw, MessagingStore(tmp_path / "sim.json"), lambda k: rs, agent_mode="offline")
    svc.sync_recipients()
    gw.simulate_inbound("hi")   # a shared line can only reply in a conversation the owner started
    svc.poll_once()
    return svc, gw, SimulatorGateway.DEMO_HANDLE


def test_on_demand_briefing_needs_confirmation(tmp_path, rs, brief):
    svc, gw, h = _svc(tmp_path, rs)
    with pytest.raises(PermissionError):
        svc.send_briefing(h, brief)
    rec = svc.send_briefing(h, brief, confirmed=True)
    assert rec["kind"] == "briefing" and rec["simulated"] and gw.sent[-1]["text"].startswith("Good morning")
    assert rec["evidence"]["briefing_date"] == brief["date"]


def test_scheduled_briefing_needs_opt_in_respects_stop_and_sends_once(tmp_path, rs, brief):
    svc, gw, h = _svc(tmp_path, rs)
    n0 = len(gw.sent)
    assert svc.send_briefing(h, brief, scheduled=True) is None          # no consent
    svc.set_alert_consent(h, True)
    assert svc.send_briefing(h, brief, scheduled=True) is None          # consent, but no preference
    o = svc.store.owner(h)
    assert "daily_briefing" not in o.alert_prefs and "daily_briefing" not in DEFAULT_ALERT_KINDS
    o.alert_prefs.append("daily_briefing")
    assert svc.send_briefing(h, brief, scheduled=True)["kind"] == "briefing"
    assert svc.send_briefing(h, brief, scheduled=True) is None          # once per day
    assert len(gw.sent) == n0 + 1
    gw.simulate_inbound("STOP")
    svc.poll_once()
    assert svc.send_briefing(h, brief | {"date": "2026-11-14"}, scheduled=True) is None
    assert svc.send_briefing(h, brief, confirmed=True) is None          # STOP beats a click too


def test_new_owners_default_to_no_economic_or_briefing_messages():
    o = Owner("+15550100001")
    assert "economic_release" not in o.alert_prefs and "daily_briefing" not in o.alert_prefs
    assert not o.alerts_opt_in


def test_alert_scan_skips_national_indicators_by_default(rs, monkeypatch):
    from decisiondesk.messaging import risk_signals
    called = []

    def spy(s, name, args):
        called.append(name)
        return run_tool(s, name, args)
    monkeypatch.setattr(risk_signals, "run_tool", spy)
    risk_signals.scan(rs)
    assert called and "get_economic_indicators" not in called
