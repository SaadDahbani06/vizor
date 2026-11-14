"""Milestone 5: the redesigned dashboard (headless Streamlit AppTest, offline, fast models)."""
import time
from pathlib import Path

import pytest

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")
PAGES = ["Overview", "Revenue & Operations", "Scenario Lab", "AI Analyst", "Economy", "Messaging",
         "Evidence & Data"]


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


def test_every_page_renders_and_overview_shows_kpis(at):
    text = md(at)
    for label in ("Revenue", "Net operating profit", "Cash on hand", "Total expenses",
                  "Inventory (at cost)", "Supplier payables"):
        assert label.upper() in text.upper()
    assert "Business intelligence" in text and "Recommended action" in text
    for page in PAGES:
        at.radio(key="nav").set_value(page).run()
        assert not at.exception, page
        assert any(m.value.startswith("# ") for m in at.markdown), page


def test_kpi_cards_navigate_to_the_matching_section(at):
    for button, section in (("go_revenue", "Revenue"), ("go_profit", "Profit & costs"),
                            ("go_cash", "Cash & obligations"), ("go_inventory", "Inventory")):
        at.radio(key="nav").set_value("Overview").run()
        at.button(key=button).click().run()
        assert at.radio(key="nav").value == "Revenue & Operations"
        assert at.radio(key="seg_ro_section").value == section
        assert not at.exception


def test_period_toggles_change_the_numbers(at):
    mtd = md(at)
    at.radio(key="seg_ov_period").set_value("365d").run()
    assert not at.exception and md(at) != mtd
    at.radio(key="seg_ov_window").set_value("1Y").run()
    assert not at.exception


def test_product_table_and_product_view(at):
    at.radio(key="nav").set_value("Revenue & Operations").run()
    at.radio(key="seg_ro_section").set_value("Products").run()
    assert not at.exception
    assert at.selectbox(key="prod_pick").value == "Tacos"
    at.selectbox(key="prod_pick").set_value("Burritos").run()
    assert "## Burritos" in md(at)
    labels = " ".join(m.label for m in at.metric)
    assert "Stock-out date" in labels and "Available stock" in labels
    for sec in ("Inventory", "Profit & costs", "Cash & obligations", "Forecasts"):
        at.radio(key="seg_ro_section").set_value(sec).run()
        assert not at.exception, sec


def test_scenario_lab_controls_drive_the_outcomes(at):
    at.radio(key="nav").set_value("Scenario Lab").run()
    assert "no change (current plan)" in md(at).lower()
    at.slider(key="sl_price").set_value(10.0).run()
    text = md(at)
    assert "Outside the owner" in text and "Tacos price +10%" in text
    assert at.number_input(key="sl_price_num").value == 10.0  # slider and number box stay in sync
    at.number_input(key="sl_price_num").set_value(4.0).run()
    assert at.slider(key="sl_price").value == 4.0 and "within policy" in md(at)
    at.slider(key="sl_supply").set_value(-50.0).run()
    at.radio(key="seg_sl_h").set_value(7).run()
    text = md(at)
    assert "next 7 days" in text and "ingredient orders -50%" in text
    assert "Best tested tacos action" in text or "None of the tested" in text
    at.button(key="sl_reset").click().run()
    assert at.slider(key="sl_price").value == 0.0 and not at.exception
    for name in ("Burritos", "Quesadillas", "Nachos", "Drinks"):  # every product renders
        at.selectbox(key="sl_product").set_value(name).run()
        assert not at.exception, name


def test_publishing_the_lab_scenario_for_imessage(at, tmp_path):
    from decisiondesk.messaging.shared import ScenarioStore
    at.radio(key="nav").set_value("Scenario Lab").run()
    at.slider(key="sl_price").set_value(3.0).run()
    at.button(key="lab_publish").click().run()
    rec = ScenarioStore().get("restaurant")
    assert rec["version"] == 1 and rec["state"]["product_scenario"]["price_change_pct"] == 3.0
    at.radio(key="nav").set_value("Messaging").run()
    assert "Published v1" in md(at) and "Scenario Lab: Tacos price +3%" in md(at)


def test_scenario_lab_question_reaches_the_analyst(at):
    """Regression (Milestone 5.2 audit): the suggested question vanished before its click."""
    at.radio(key="nav").set_value("Scenario Lab").run()
    at.slider(key="sl_price").set_value(5.0).run()
    at.button(key="go_analyst").click().run()
    assert at.radio(key="nav").value == "AI Analyst"
    at.button(key="ask_prefill").click().run()
    assert not at.exception
    reply = at.chat_message[-1].markdown[0].value
    assert "Tacos price +5%" in reply and "grounded: True" in reply
    assert "analyst_prefill" not in at.session_state


def test_analyst_suggestions_answer_offline(at):
    at.radio(key="nav").set_value("AI Analyst").run()
    at.button(key="suggest_1").click().run()
    reply = at.chat_message[-1].markdown[0].value
    assert "Most profitable" in reply and "grounded: True" in reply


def test_slow_gemini_does_not_block_the_dashboard(monkeypatch, at):
    """A language-model reply runs in a background thread; other pages stay usable."""
    import threading

    gate = threading.Event()
    agent = at.session_state["agent"]

    class Slow:
        mode, label, gemini, last_fallback = "gemini", "Gemini analyst", None, None

        def ask(self, q):
            gate.wait(20)
            return agent.ask(q)
    at.session_state["agent"] = Slow()
    at.radio(key="nav").set_value("AI Analyst").run()
    at.chat_input(key="chat_in").set_value("What should I do?").run()
    assert at.session_state["pending"] is not None and not at.exception
    t0 = time.time()
    at.radio(key="nav").set_value("Overview").run()  # still interactive while waiting
    assert time.time() - t0 < 30 and not at.exception
    gate.set()
    at.session_state["pending"]["future"].result(timeout=60)
    at.radio(key="nav").set_value("AI Analyst").run()
    at.run()
    assert any("Recommendation" in m.markdown[0].value for m in at.chat_message if m.markdown)
