"""End-to-end product tests (offline): data -> tools -> decisions -> agent -> UI, monitoring,
and the Nessie banking source. External services are faked; no credentials are needed."""
import json
import os
import types
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from test_fred_macro import RAW as FRED_RAW
from test_fred_macro import FakeFRED
from test_nessie import FakeNessie, client_for

from decisiondesk.agent.agent import DecisionAgent
from decisiondesk.agent.grounding import ungrounded_numbers
from decisiondesk.agent.tools import TOOL_SCHEMAS, BusinessSession, run_tool
from decisiondesk.banking import import_banking
from decisiondesk.connectors.fred import FREDConnector
from decisiondesk.leaderboard import forecast_selected
from decisiondesk.monitor import MonitorStore, TelegramNotifier, run_checks
from decisiondesk.nessie_sandbox import build_seed_plan, initialize
from decisiondesk.profile import BusinessProfile, ColumnMapping
from decisiondesk.synthetic_v2 import load_business
from decisiondesk.transactions import TRANSACTION_COLUMNS

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")
FAST = ("seasonal_naive", "weekday_mean_12w", "historical_mean_28d", "ridge")
FOOD = {"food_cost_change_pct": None, "elasticity_mode": None, "elasticity_low": None,
        "elasticity_high": None, "max_price_increase": None, "starting_cash": None,
        "objective": "max_expected_profit"}
INV = {"purchase_budget_usd": None, "storage_units": None, "max_stockout_probability": None,
       "objective": "max_expected_profit"}


@pytest.fixture(scope="module")
def restaurant():
    return BusinessSession.from_demo("restaurant", models=FAST, n_sims=500)


@pytest.fixture(scope="module")
def retail():
    return BusinessSession.from_demo("retail", models=FAST, n_sims=500)


# ------------------------------------------------------------------ Phase 1 data
@pytest.mark.parametrize("key", ["restaurant", "retail"])
def test_v2_dataset_is_consistent_and_not_trivial(key):
    ds = load_business(key)
    d = ds.daily
    assert ds.metadata["data_origin"] == "SYNTHETIC" and ds.metadata["state"] in ("TX", "OH")
    assert d["date"].diff().dropna().dt.days.eq(1).all() and len(d) == 1096
    ledger = ds.metadata["opening_cash"]["amount"] + (d.cash_in - d.cash_out).cumsum()
    np.testing.assert_allclose(d.cash_balance, ledger, atol=0.05)
    assert (d.loc[d.closed, "revenue"] == 0).all() and (d.inventory_on_hand >= 0).all()
    assert set(ds.bills.category) >= {"rent", "utilities", "payroll", "insurance"}
    assert (ds.bills.loc[ds.bills.category == "rent", "date"].dt.day == 1).all()
    open_ = d[~d.closed]
    ratio = open_.revenue / open_.groupby(open_.date.dt.day_of_week).revenue.transform("mean")
    assert ratio.std() > 0.12  # far from a deterministic weekday pattern
    margin = (d.revenue - d.variable_costs - d.fixed_costs).tail(365).sum() / d.revenue.tail(365).sum()
    assert 0.0 < margin < 0.2  # realistic small-business operating margin


# ------------------------------------------------------------------ data reaches the engine
def test_uploaded_csv_reaches_forecast_and_decisions(tmp_path):
    d = load_business("restaurant").daily.rename(columns={
        "date": "Day", "revenue": "Sales", "variable_costs": "COGS", "fixed_costs": "Overhead"})
    path = tmp_path / "my_cafe.csv"
    d[["Day", "Sales", "COGS", "Overhead"]].to_csv(path, index=False)
    mapping = ColumnMapping({"Day": "date", "Sales": "revenue", "COGS": "variable_costs",
                             "Overhead": "fixed_costs"})
    with pytest.raises(ValueError, match="confirm"):
        BusinessSession.from_upload(path, mapping, BusinessProfile("Cafe", "restaurant", "NJ",
                                                                   "max_expected_profit"))
    s = BusinessSession.from_upload(path, mapping, BusinessProfile(
        "Cafe", "restaurant", "NJ", "max_expected_profit", {"max_price_increase": 0.04},
        confirmed_by_user=True), models=FAST, n_sims=300)
    fc = run_tool(s, "forecast_business", {"target": "revenue"})
    assert fc["data_label"] == "UPLOADED USER DATA" and fc["point_total"] > 0
    out = run_tool(s, "evaluate_restaurant_actions", FOOD)
    assert out["scenario"]["max_price_increase"] == 0.04
    assert out["scenario"]["starting_cash"] is None  # never invented
    inv = run_tool(s, "evaluate_inventory_policies", INV)
    assert inv["supported"] is False and "units_sold" in inv["missing_columns"]


def test_model_outputs_are_what_scenarios_use(restaurant):
    fc = run_tool(restaurant, "forecast_business", {"target": "revenue"})
    board = restaurant.board("revenue")
    point = forecast_selected(restaurant.history, "revenue", board)["revenue"].sum()
    assert fc["selected_model"] == board.selected
    assert fc["point_total"] == pytest.approx(round(point), abs=1)
    out = run_tool(restaurant, "evaluate_restaurant_actions", FOOD)
    assert out["forecast_models"]["revenue"] == board.selected
    bundle = restaurant.bundle(("revenue", "variable_costs", "fixed_costs"))
    np.testing.assert_allclose(bundle.targets["revenue"].point,
                               forecast_selected(restaurant.history, "revenue", board)["revenue"])


# ------------------------------------------------------------------ FRED retrieval
def test_fred_is_actually_queried_when_refresh_requested(tmp_path, monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake-fred-key-0123456789abcdef")
    fake = FakeFRED([FRED_RAW])
    s = BusinessSession.from_demo("restaurant", models=FAST, fred_cache_dir=str(tmp_path))
    s.fred_session = fake
    out = run_tool(s, "get_economic_indicators", {"refresh": True})
    assert any(u.endswith("/series/observations") for u, _ in fake.calls)
    assert out["observations"] and all(o["retrieval"] == "live_api" for o in out["observations"])
    assert out["observations"][0]["category"] == "observed_economic_data"
    assert "fake-fred-key" not in json.dumps(out)
    offline = BusinessSession.from_demo("restaurant", fred_cache_dir=str(tmp_path / "empty"))
    out2 = run_tool(offline, "get_economic_indicators", {"refresh": False})
    assert out2["observations"] == [] and any("unavailable" in n for n in out2["notes"])


# ------------------------------------------------------------------ agent (offline mode)
def test_offline_agent_reruns_with_changed_assumptions_and_stays_grounded(restaurant, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    agent = DecisionAgent(restaurant)
    assert agent.mode == "offline"
    r1 = agent.ask("What should I do if my food costs increase 15%?")
    c1 = r1.tool_calls[0]
    assert c1["name"] == "evaluate_restaurant_actions" and c1["input"]["food_cost_change_pct"] == 15
    assert r1.grounded, r1.ungrounded
    r2 = agent.ask("What happens if demand becomes more sensitive to price increases?")
    c2 = r2.tool_calls[0]
    assert c2["output"]["run_id"] != c1["output"]["run_id"]
    sc1, sc2 = c1["output"]["scenario"], c2["output"]["scenario"]
    assert sc2["food_cost_change_pct"] == 15  # earlier change carried forward
    assert sc2["price_elasticity"]["mode"] < sc1["price_elasticity"]["mode"]
    assert sc2["price_elasticity"]["source"] == "user_declared"
    p1 = {a["action_id"]: a["change_vs_no_action"] for a in c1["output"]["actions"]}
    p2 = {a["action_id"]: a["change_vs_no_action"] for a in c2["output"]["actions"]}
    assert p2["raise_prices_3pct"] < p1["raise_prices_3pct"]  # fresh calculation, not a replay
    assert r2.grounded, r2.ungrounded


def test_infeasible_budget_is_explained_not_forced(retail):
    agent = DecisionAgent(retail, mode="offline")
    r = agent.ask("What should I do with a $40,000 inventory budget?")
    out = r.tool_calls[0]["output"]
    assert out["scenario"]["purchase_budget_usd"] == 40000
    if out["recommended_action"] is None:
        assert "Smallest relaxation" in out["rationale"] and "no feasible policy" in r.text
    r2 = agent.ask("What if I increase the budget to $45,000?")
    assert r2.tool_calls[0]["output"]["scenario"]["purchase_budget_usd"] == 45000
    assert r.grounded and r2.grounded


def test_missing_information_is_requested_not_invented(tmp_path):
    d = load_business("retail").daily[["date", "revenue", "variable_costs", "fixed_costs"]]
    path = tmp_path / "shop.csv"
    d.to_csv(path, index=False)
    s = BusinessSession.from_upload(path, ColumnMapping({c: c for c in d.columns}),
                                    BusinessProfile("Shop", "retail", "OH", "max_expected_profit",
                                                    confirmed_by_user=True), models=FAST)
    cash = run_tool(s, "assess_cash_risk", {"starting_cash": None})
    assert cash["available"] is False and "provide" in cash["reason"]
    reply = DecisionAgent(s, mode="offline").ask("Will I run out of cash?")
    assert "needs a starting balance" in reply.text


# ------------------------------------------------------------------ agent (Claude mode, faked)
def _block(**kw):
    return types.SimpleNamespace(**kw)


class FakeAnthropic:
    """Scripted stand-in for anthropic.Anthropic(): records requests, replays responses."""

    def __init__(self, script):
        self.script, self.requests = list(script), []
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})
        return self.script.pop(0)(kw)


def tool_turn(name, args):
    return lambda kw: _block(stop_reason="tool_use", content=[
        _block(type="text", text="Running the analysis."),
        _block(type="tool_use", id=f"tu_{name}", name=name, input=args)])


def final_turn(fn):
    return lambda kw: _block(stop_reason="end_turn", content=[_block(type="text", text=fn(kw))])


def _last_tool_output(kw):
    for m in reversed(kw["messages"]):
        if m["role"] == "user" and isinstance(m["content"], list):
            return json.loads(m["content"][0]["content"])
    raise AssertionError("no tool result sent")


def test_claude_mode_executes_tools_and_grounds_reply(restaurant):
    def answer(kw):
        out = _last_tool_output(kw)
        top = out["actions"][0]
        return (f"Recommended: {top['label']} — change vs no action ${top['change_vs_no_action']:,.0f}. "
                f"Food costs +15% is hypothetical.")

    fake = FakeAnthropic([tool_turn("evaluate_restaurant_actions", {**FOOD, "food_cost_change_pct": 15}),
                          final_turn(answer)])
    agent = DecisionAgent(restaurant, client=fake)
    reply = agent.ask("What should I do if my food costs increase 15%?")
    assert agent.mode == "claude" and reply.grounded, reply.ungrounded
    first = fake.requests[0]
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert all(t["strict"] for t in first["tools"]) and {t["name"] for t in first["tools"]} == {
        t["name"] for t in TOOL_SCHEMAS}
    second = fake.requests[1]["messages"]
    assert second[-2]["role"] == "assistant"  # full assistant content appended
    results = second[-1]["content"]
    assert len(results) == 1 and results[0]["tool_use_id"] == "tu_evaluate_restaurant_actions"
    assert reply.tool_calls[0]["output"]["scenario"]["food_cost_change_pct"] == 15


def test_claude_mode_invented_number_is_retried_then_flagged(restaurant):
    fake = FakeAnthropic([tool_turn("get_assumptions", {}),
                          final_turn(lambda kw: "Profit will rise by $123,457 next quarter."),
                          final_turn(lambda kw: "Still guessing: 87.6% better.")])
    reply = DecisionAgent(restaurant, client=fake).ask("Summarize assumptions")
    assert reply.retried and not reply.grounded
    assert "87.6%" in reply.ungrounded and "Unverified numbers" in reply.text
    assert "grounding check" in fake.requests[-1]["messages"][-1]["content"]


# ------------------------------------------------------------------ monitoring
def _fred_connector(tmp_path, rows):
    return FREDConnector(api_key="fake-fred-key-0123456789abcdef", session=FakeFRED([rows]),
                         cache_dir=tmp_path / "fred")


def test_alert_rules_trigger_deduplicate_persist_and_respect_thresholds(tmp_path, restaurant):
    path = tmp_path / "monitor.json"
    store = MonitorStore(path)
    rule = store.add_rule("restaurant", "fred_release", "CUSR0000SAF11", threshold=None)
    before = json.dumps(restaurant.state.__dict__, default=str)
    a1 = run_checks(store, {"restaurant": restaurant}, fred=_fred_connector(tmp_path, FRED_RAW))
    assert len(a1) == 1 and a1[0].category == "observed_economic_data"
    assert a1[0].evidence["observed_at"] == "2024-01-01" and "re_evaluation" in a1[0].evidence
    assert json.dumps(restaurant.state.__dict__, default=str) == before  # user scenario untouched
    again = MonitorStore(path)  # persisted state reloaded
    assert run_checks(again, {"restaurant": restaurant},
                      fred=_fred_connector(tmp_path / "b", FRED_RAW)) == []  # no duplicate
    revised = FRED_RAW + [{"realtime_start": "2024-04-10", "realtime_end": "9999-12-31",
                           "date": "2024-03-01", "value": "105"}]
    a3 = run_checks(MonitorStore(path), {}, fred=_fred_connector(tmp_path / "c", revised))
    assert len(a3) == 1 and a3[0].evidence["observed_at"] == "2024-03-01"
    strict = MonitorStore(tmp_path / "strict.json")
    strict.add_rule("restaurant", "fred_release", "CUSR0000SAF11", threshold=99.0)
    assert run_checks(strict, {}, fred=_fred_connector(tmp_path / "d", FRED_RAW)) == []
    assert rule.rule_id in {r.rule_id for r in MonitorStore(path).rules}


def test_cash_rule_and_telegram_opt_in(tmp_path, restaurant):
    class Post:
        def __init__(self):
            self.sent = []

        def post(self, url, json=None, timeout=None):
            self.sent.append((url, json))
            return types.SimpleNamespace(status_code=200)

    post = Post()
    notifier = TelegramNotifier("tg-token-secret", "42", session=post)
    store = MonitorStore(tmp_path / "m.json")
    store.add_rule("restaurant", "cash_shortfall", threshold=0.0, telegram_opt_in=True)
    store.add_rule("restaurant", "cash_shortfall", threshold=0.0)  # same rule id -> not duplicated
    alerts = run_checks(store, {"restaurant": restaurant}, fred=FREDConnector(offline=True),
                        notifier=notifier)
    assert len(alerts) == 1 and "telegram" in alerts[0].delivered and len(post.sent) == 1
    quiet = MonitorStore(tmp_path / "q.json")
    quiet.add_rule("restaurant", "cash_shortfall", threshold=0.0, telegram_opt_in=False)
    run_checks(quiet, {"restaurant": restaurant}, notifier=notifier)
    assert len(post.sent) == 1  # no opt-in, no Telegram message
    with pytest.raises(ValueError):
        MonitorStore(tmp_path / "x.json").add_rule("restaurant", "cash_shortfall")


# ------------------------------------------------------------------ Nessie banking source
@pytest.fixture()
def nessie_fake():
    fake = FakeNessie(unreadable={"bills"})
    initialize(client_for(fake), build_seed_plan(date(2026, 11, 13), 28), confirm=True,
               log=lambda *_: None)
    return fake


def test_nessie_records_retrieved_normalized_deduplicated(nessie_fake):
    b = import_banking(client_for(nessie_fake))
    assert len(b.transactions) == 56 and list(b.transactions.columns) == list(TRANSACTION_COLUMNS)
    assert (b.transactions.data_origin == "SYNTHETIC_SANDBOX_NESSIE").all()
    assert b.duplicates_dropped == 0 and b.transactions.transaction_id.is_unique
    assert any("bills list unreadable" in m for m in b.missing_bills)
    assert any("rent" in m for m in b.missing_bills)
    assert not b.forecast_eligible and any("504" in r for r in b.eligibility_reasons)
    checking = next(a for a in b.accounts if a["role"] == "checking")
    assert checking["inflows"] > 0 and "not used" in checking["balance_note"]
    # A repeated import of the same records produces identical, non-duplicated ids.
    again = import_banking(client_for(nessie_fake))
    assert set(again.transactions.transaction_id) == set(b.transactions.transaction_id)


def test_nessie_duplicate_api_rows_are_dropped(nessie_fake):
    acct = next(a["_id"] for a in nessie_fake.accounts if "checking" in a["nickname"])
    dup = next(r for r in nessie_fake.records["deposits"] if r.get("payee_id") == acct)
    nessie_fake.records["deposits"].append(dict(dup))  # server returns the same record twice
    b = import_banking(client_for(nessie_fake))
    assert b.duplicates_dropped == 1 and len(b.transactions) == 56


def test_nessie_summary_tool_and_ui_display(nessie_fake, monkeypatch, restaurant):
    from decisiondesk import banking
    monkeypatch.setattr(banking, "NessieClient", lambda: client_for(nessie_fake))
    out = run_tool(restaurant, "get_banking_summary", {})
    assert out["available"] and out["transactions"] == 56 and not out["forecast_eligible"]
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("MSRD_FAST", "1")
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception
    at.button(key="open_settings").click().run()  # 5.4: Settings -> Advanced diagnostics
    at.radio(key="seg_settings").set_value("Advanced diagnostics").run()
    at.radio(key="seg_ev_section").set_value("Banking (Nessie)").run()
    at.button(key="bank_go").click().run()
    assert not at.exception
    texts = " ".join(x.value for x in at.markdown) + " ".join(w.value for w in at.warning)
    assert "56 transactions" in texts and "Missing bill records" in texts
    assert any("Not used for forecasting" in e.value for e in at.error)


# ------------------------------------------------------------------ UI, offline, secrets
def test_ui_runs_end_to_end_without_credentials(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "FRED_API_KEY", "NESSIE_API_KEY", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.setenv(k, "")
    monkeypatch.setenv("MSRD_FAST", "1")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    banner = lambda: " ".join(m.value for m in at.markdown if "demo-banner" in m.value)
    assert not at.exception and "SYNTHETIC" in banner()
    at.radio(key="nav").set_value("Decision Lab").run()
    at.button(key="d_run").click().run()
    assert not at.exception and (at.success or at.warning)
    at.button(key="chat_toggle").click().run()  # floating "Ask your analyst" (5.4)
    at.chat_input(key="chat_in").set_value("What should I do if my food costs increase 15%?").run()
    assert not at.exception and "Offline analyst" in at.chat_message[-1].markdown[0].value
    at.radio(key="source").set_value("Demo retailer (Columbus, OH)").run()
    assert not at.exception and "Maple Street Hardware" in banner()
    at.radio(key="nav").set_value("Decision Lab").run()
    assert not at.exception
    assert any("Item-level what-ifs are not available" in m.value for m in at.markdown)  # empty state
    at.button(key="d_run").click().run()
    assert not at.exception


def test_no_secrets_in_tool_outputs_or_agent_text(monkeypatch, restaurant, tmp_path):
    secrets = {"FRED_API_KEY": "fredsecret0123456789abcdef012345",
               "NESSIE_API_KEY": "nessiesecret0123456789abcdef0123",
               "ANTHROPIC_API_KEY": "sk-ant-not-a-real-key-000"}
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)
    agent = DecisionAgent(restaurant, mode="offline")
    texts = [agent.ask(q).text for q in ("What does inflation look like?",
                                         "Give me an overview", "Forecast revenue")]
    blob = json.dumps(agent.tool_outputs, default=str) + " ".join(texts)
    blob += json.dumps(run_tool(restaurant, "get_banking_summary", {}), default=str)
    for v in secrets.values():
        assert v not in blob


def test_grounding_detector_basics():
    tool = [{"x": 1033.4, "share": 0.993, "series": "CUSR0000SAF11", "d": "2026-11-13"}]
    assert ungrounded_numbers("Adds $1,033 (99.3%) as of 2026-11-13", tool, []) == []
    assert ungrounded_numbers("Adds $1,500", tool, []) == ["$1,500"]
    assert ungrounded_numbers("Budget $40,000", tool, ["I have $40,000"]) == []
    assert os.environ.get("NONEXISTENT") is None


def test_offline_fallback_and_indicator_questions_are_grounded(restaurant, tmp_path):
    agent = DecisionAgent(restaurant, mode="offline")
    help_reply = agent.ask("Tell me a joke")
    assert help_reply.tool_calls == [] and help_reply.grounded, help_reply.ungrounded
    econ = agent.ask("What do current U.S. food-price indicators show?")
    # Milestone 5.4: national indicators are not part of the product; the analyst says so and
    # offers a business-specific supplier cost test instead (no FRED call).
    assert econ.tool_calls == [] and econ.grounded and "not part of" in econ.text
