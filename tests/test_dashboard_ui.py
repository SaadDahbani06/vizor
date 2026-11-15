"""Milestones 5 and 5.4: the simple four-page dashboard with the floating analyst (headless
Streamlit AppTest, offline analyst, fast models)."""
import time
from pathlib import Path

import pytest

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")
PAGES = ["Overview", "My Business", "Decision Lab", "Local Market"]


@pytest.fixture
def at(monkeypatch):
    monkeypatch.setenv("MSRD_FAST", "1")
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(APP, default_timeout=300)
    app.run()
    assert not app.exception
    return app


def md(at) -> str:
    return " ".join(m.value for m in at.markdown)


def text(at) -> str:
    return md(at) + " " + " ".join(c.value for c in at.caption)


def test_overview_is_minimal_and_plain(at):
    assert list(at.radio(key="nav").options) == PAGES
    t = md(at)
    for label in ("Sales this month", "Profit this month", "Cash on hand"):
        assert label in t, label
    assert "greeting" in t and "What needs your attention" in t and "One idea to consider" in t
    assert "of every $1 of sales" in t and "Covers this week's bills" in t
    assert "Raise the price of tacos by 5%" in t and "About +$286 profit over the next 4 weeks" in t
    assert "19 of 20" in t  # 95% in plain words
    attention = [b for b in at.button if b.key and b.key.startswith("go_alert_")]
    assert 1 <= len(attention) <= 3  # at most three things on the Overview
    for jargon in ("90%", "simulated futures", "elasticity", "Observed · synthetic"):
        assert jargon not in t, jargon
    assert "SYNTHETIC" in t  # one honest demo-data banner stays


def test_every_page_renders(at):
    for page in PAGES:
        at.radio(key="nav").set_value(page).run()
        assert not at.exception, page
        assert any(m.value.startswith("# ") or 'class="greeting"' in m.value for m in at.markdown), page


def test_no_economy_messaging_or_evidence_pages_and_no_macro_ui(at):
    nav = list(at.radio(key="nav").options)
    assert not {"Economy", "Messaging", "Evidence & Data", "AI Analyst"} & set(nav)
    for page in PAGES:
        at.radio(key="nav").set_value(page).run()
        assert "FRED" not in text(at) and "CPI" not in text(at), page


def test_cards_and_alerts_navigate(at):
    for button, section in (("go_revenue", "Sales & profit"), ("go_profit", "Costs"), ("go_cash", "Cash & bills")):
        at.radio(key="nav").set_value("Overview").run()
        at.button(key=button).click().run()
        assert at.radio(key="nav").value == "My Business"
        assert at.radio(key="seg_mb_section").value == section
    at.radio(key="nav").set_value("Overview").run()
    at.button(key="go_alert_1").click().run()  # "Nachos sold less than usual"
    assert at.radio(key="seg_mb_section").value == "Products" and at.selectbox(key="prod_pick").value == "Nachos"
    at.radio(key="nav").set_value("Overview").run()
    at.button(key="cta_brief_lab").click().run()
    assert at.radio(key="nav").value == "Decision Lab" and at.slider(key="sl_price").value == 5.0
    assert not at.exception


def test_how_we_worked_it_out_tips(at):
    pops = [p for p in at.get("popover") if "How we worked this out" in p.proto.popover.label]
    assert len(pops) >= 4  # three number cards + the idea


def test_my_business_metrics_products_and_sections(at):
    at.radio(key="nav").set_value("My Business").run()
    for metric in ("Profit", "After ingredients", "Customers"):  # tap a number -> its chart
        at.radio(key="seg_mb_metric").set_value(metric).run()
        assert not at.exception and f"#### {metric}" in md(at)
    at.radio(key="seg_mb_section").set_value("Products").run()
    assert "You keep $9.49 of each after ingredients" in md(at)  # tacos: $12.75 - $3.26
    at.button(key="go_prod_burritos").click().run()
    assert "## Burritos" in md(at)
    labels = " ".join(m.label for m in at.metric)
    assert "Stock-out date" in labels and "Available stock" in labels
    at.button(key="go_pd_lab").click().run()
    assert at.radio(key="nav").value == "Decision Lab" and at.selectbox(key="sl_product").value == "Burritos"
    at.radio(key="nav").set_value("My Business").run()
    for sec in ("Stock", "Costs", "Cash & bills", "Next 4 weeks"):
        at.radio(key="seg_mb_section").set_value(sec).run()
        assert not at.exception, sec


def test_decision_lab_shows_one_kind_of_change_at_a_time(at):
    at.radio(key="nav").set_value("Decision Lab").run()
    assert "Move a slider above" in md(at)
    keys = {s.key for s in at.slider}
    assert "sl_price" in keys and "sl_cost" not in keys and "sl_supply" not in keys
    at.slider(key="sl_price").set_value(10.0).run()
    t = md(at)
    assert "Outside the owner" in t and "Tacos price +10%" in t
    at.slider(key="sl_price").set_value(5.0).run()
    t = md(at)
    assert "within your limit" in t and "About +$" in t and "Before you decide" in t and "Check first" in t
    assert any("assumption" in c.label for c in at.checkbox)  # things to check, as ticks
    at.radio(key="seg_sl_mode").set_value("Order more or less").run()
    assert "sl_price" not in {s.key for s in at.slider}
    assert at.session_state["sl_price"] == 0.0  # switching kind resets the other change
    at.slider(key="sl_supply").set_value(-50.0).run()
    at.radio(key="seg_sl_h").set_value(7).run()
    assert "next week" in md(at) and "ingredient orders -50%" in md(at)
    at.button(key="sl_reset").click().run()
    assert at.slider(key="sl_supply").value == 0.0 and not at.exception
    at.radio(key="seg_sl_mode").set_value("Combine changes").run()
    assert {"sl_price", "sl_supply", "sl_cost", "sl_shock", "sl_mkt"} <= {s.key for s in at.slider}
    for name in ("Burritos", "Quesadillas", "Nachos", "Drinks"):  # every product renders
        at.selectbox(key="sl_product").set_value(name).run()
        assert not at.exception, name


def test_supplier_price_change_lowers_profit(at):
    at.radio(key="nav").set_value("Decision Lab").run()
    at.radio(key="seg_sl_mode").set_value("Supplier prices change").run()
    at.slider(key="sl_cost").set_value(10.0).run()
    assert not at.exception
    t = md(at)
    assert "supplier ingredient costs +10%" in t.lower() and "About -$" in t
    assert "Check the new prices on your latest supplier invoices." in [c.label for c in at.checkbox]
    assert at.session_state["session"].state.product_scenario["ingredient_cost_change_pct"] == 10.0


def test_publishing_the_lab_scenario_for_imessage(at):
    from decisiondesk.messaging.shared import ScenarioStore
    at.radio(key="nav").set_value("Decision Lab").run()
    at.slider(key="sl_price").set_value(3.0).run()
    at.button(key="lab_publish").click().run()
    rec = ScenarioStore().get("restaurant")
    assert rec["version"] == 1 and rec["state"]["product_scenario"]["price_change_pct"] == 3.0
    at.button(key="open_settings").click().run()
    assert at.radio(key="seg_settings").value == "Notifications"
    assert "Published v1" in md(at) and "Decision Lab: Tacos price +3%" in md(at)


def test_floating_analyst_opens_and_answers(at):
    assert "chat_in" not in {c.key for c in at.chat_input}  # closed until asked
    at.button(key="chat_toggle").click().run()
    labels = [b.label for b in at.button if b.key and b.key.startswith("suggest_")]
    assert "What should I focus on today?" in labels
    at.button(key="suggest_2").click().run()  # "Which of my five products is most profitable?"
    reply = at.chat_message[-1]
    assert "Most profitable" in reply.markdown[0].value and "every number checked" in reply.caption[0].value
    at.button(key="chat_toggle").click().run()
    assert not at.session_state["chat_open"]


def test_lab_question_opens_the_analyst(at):
    """Regression (5.2 audit, kept): the Lab's question is answered, not lost."""
    at.radio(key="nav").set_value("Decision Lab").run()
    at.slider(key="sl_price").set_value(5.0).run()
    at.button(key="go_analyst").click().run()
    assert not at.exception and at.session_state["chat_open"]
    reply = at.chat_message[-1]
    assert "Tacos price +5%" in reply.markdown[0].value and "every number checked" in reply.caption[0].value


def test_chat_is_multi_turn_and_uses_page_context(at):
    at.radio(key="nav").set_value("Decision Lab").run()
    at.selectbox(key="sl_product").set_value("Nachos").run()
    at.button(key="chat_toggle").click().run()
    at.chat_input(key="chat_in").set_value("What if I raise the price 4%?").run()
    assert "Nachos price +4%" in at.chat_message[-1].markdown[0].value  # product from the page
    at.chat_input(key="chat_in").set_value("What about 2% instead?").run()
    assert "Nachos price +2%" in at.chat_message[-1].markdown[0].value  # same product, new value
    at.chat_input(key="chat_in").set_value("Which is safer?").run()
    last = at.chat_message[-1].markdown[0].value
    assert "Nachos price +4%" in last and "Nachos price +2%" in last and "Safer:" in last
    assert len(at.chat_message) == 6 and not at.exception
    at.button(key="chat_clear").click().run()
    assert len(at.chat_message) == 0


def test_slow_gemini_does_not_block_the_dashboard(at):
    """A language-model reply runs in a background thread; other pages stay usable."""
    import threading

    gate = threading.Event()
    agent = at.session_state["agent"]

    class Slow:
        mode, label, gemini, last_fallback = "gemini", "Gemini analyst", None, None

        def ask(self, q, context=None):
            gate.wait(20)
            return agent.ask(q, context)
    at.session_state["agent"] = Slow()
    at.button(key="chat_toggle").click().run()
    at.chat_input(key="chat_in").set_value("What should I do?").run()
    assert at.session_state["pending"] is not None and not at.exception
    assert at.chat_input(key="chat_in").disabled  # no double submits while thinking
    t0 = time.time()
    at.radio(key="nav").set_value("My Business").run()  # still interactive while waiting
    assert time.time() - t0 < 30 and not at.exception
    gate.set()
    at.session_state["pending"]["future"].result(timeout=60)
    at.run()
    assert any("Recommendation" in m.markdown[0].value for m in at.chat_message if m.markdown)


def test_analyst_errors_are_shown_not_swallowed(at):
    class Broken:
        mode, label, gemini, last_fallback = "offline", "Offline analyst", None, None

        def ask(self, q, context=None):
            raise RuntimeError("tool crashed")
    at.session_state["agent"] = Broken()
    at.button(key="chat_toggle").click().run()
    at.chat_input(key="chat_in").set_value("hello").run()
    assert not at.exception and any("tool crashed" in e.value for e in at.error)


def test_settings_is_outside_the_navigation(at):
    at.button(key="open_settings").click().run()
    assert "# Settings" in md(at) and at.radio(key="nav").value == "Overview"
    at.radio(key="seg_settings").set_value("Advanced diagnostics").run()
    for sec in ("Model validation", "Uncertainty calibration", "Reconciliation", "Assumptions"):
        at.radio(key="seg_ev_section").set_value(sec).run()
        assert not at.exception, sec
    at.button(key="settings_close").click().run()
    assert at.session_state["view"] is None and "What needs your attention" in md(at)
