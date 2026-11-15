"""Milestones 5-5.5: the four-page owner dashboard and the "Ask Vizor AI" side panel (headless Streamlit
AppTest, offline analyst, fast models). Business language on screen; numbers unchanged."""
import time
from pathlib import Path

import pytest

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")
PAGES = ["Overview", "My Business", "Decision Lab", "Local Market"]
JARGON = ("Monte Carlo", "Ridge", "XGBoost", "CatBoost", "elasticity", "WAPE", "rolling-origin", "bootstrap",
          "simulated futures", "90% simulation", "calibration", "residual")


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


def visible(at) -> str:
    """Markdown outside expanders and popovers (what the owner sees without clicking)."""
    hidden = set()
    for kind in ("expander", "popover"):
        for blk in at.get(kind):
            hidden |= {id(m) for m in blk.markdown}
    return " ".join(m.value for m in at.markdown if id(m) not in hidden)


def test_overview_answers_the_owners_questions(at):
    assert list(at.radio(key="nav").options) == PAGES
    t = md(at)
    for label in ("Revenue", "Operating profit", "Cash available", "Expenses", "Inventory health",
                  "Upcoming obligations"):
        assert f'<div class="kpi-label">{label}' in t, label
    assert "Business attention" in t and "Sales were lower yesterday" in t and "fewer customers came in" in t
    assert "Covers this week" in t and "Rising most:" in t and "Product performance" in t
    assert "Recommended decision to review" in t and "Raise tacos prices by 5%" in t and "+$286" in t
    assert "Key risk:" in t
    assert "Very likely to help, though a loss is possible." in t
    assert "Downside <b>-$5</b>" in t and "Upside <b>+$557</b>" in t  # downside stays visible
    assert at.button(key="act_attn_0").label == "Investigate"  # top alert: sales decline
    assert 1 <= len([b for b in at.button if b.key and b.key.startswith("go_alert_")]) <= 2
    assert "Demo Business · Sample Data" in t  # one discreet sample-data label


def test_no_technical_jargon_on_the_main_screens(at):
    for page in PAGES:
        at.radio(key="nav").set_value(page).run()
        assert not at.exception, page
        t = visible(at)
        for word in JARGON:
            assert word.lower() not in t.lower(), (page, word)
        assert "FRED" not in t and "CPI" not in t, page


def test_no_economy_messaging_or_evidence_pages(at):
    gone = {"Economy", "Messaging", "Evidence & Data", "AI Analyst", "Scenario Lab"}
    assert not gone & set(at.radio(key="nav").options)


def test_cards_alerts_and_opportunity_navigate(at):
    for button, sec in (("go_revenue", "Sales & profit"), ("go_profit", "Costs"), ("go_cash", "Cash & bills"),
                        ("go_expenses", "Costs"), ("go_inventory", "Stock")):
        at.radio(key="nav").set_value("Overview").run()
        at.button(key=button).click().run()
        assert at.radio(key="nav").value == "My Business" and at.radio(key="seg_mb_section").value == sec
    at.radio(key="nav").set_value("Overview").run()
    at.button(key="go_alert_1").click().run()  # "Nachos sold less than usual" -> its detail view
    assert at.radio(key="seg_mb_section").value == "Products" and "## Nachos" in md(at)
    at.radio(key="nav").set_value("Overview").run()
    at.button(key="cta_brief_lab").click().run()
    assert at.radio(key="nav").value == "Decision Lab" and at.slider(key="sl_price").value == 5.0
    assert not at.exception


def test_estimates_explain_themselves_on_hover(at):
    t = md(at)
    assert t.count('class="hint') >= 8  # explanations live in hover tips, not on the page
    assert "9 in 10 runs" in t and "-$5" in t and "+$557" in t and "Not a guarantee" in t
    assert "Rising most:" in t and "Key risk:" in t  # moved into tips, still available


def test_my_business_sections_and_product_detail(at):
    at.radio(key="nav").set_value("My Business").run()
    for metric in ("Profit", "After ingredients", "Customers"):
        at.radio(key="seg_mb_metric").set_value(metric).run()
        assert not at.exception and f"#### {metric}" in md(at)
    assert "Revenue breakdown" in md(at)
    at.radio(key="seg_mb_section").set_value("Products").run()
    assert "$9.49" in md(at)  # tacos: $12.75 price - $3.26 ingredients
    at.button(key="go_prod_burritos").click().run()
    t = md(at)
    assert "## Burritos" in t and "Kept per order" in t and "Expected next week" in t
    at.button(key="prod_back").click().run()
    assert "## Burritos" not in md(at) and "All items" in md(at)
    at.button(key="go_prod_burritos").click().run()
    at.button(key="cta_pd_lab").click().run()
    assert at.radio(key="nav").value == "Decision Lab" and at.selectbox(key="sl_product").value == "Burritos"
    at.radio(key="nav").set_value("My Business").run()
    for sec in ("Costs", "Stock", "Cash & bills", "Outlook"):
        at.radio(key="seg_mb_section").set_value(sec).run()
        assert not at.exception, sec
    assert "Forecast based on your historical business performance" in md(at)


def test_decision_lab_before_and_after(at):
    at.radio(key="nav").set_value("Decision Lab").run()
    assert "Your current plan" in md(at) and "Best option we found" in md(at)
    keys = {x.key for x in at.slider}
    assert "sl_price" in keys and "sl_cost" not in keys and "sl_supply" not in keys  # one change at a time
    at.slider(key="sl_price").set_value(10.0).run()
    assert "Outside the owner" in md(at) and "Tacos price +10%" in md(at)
    at.slider(key="sl_price").set_value(5.0).run()
    t = md(at)
    assert "within your limit" in t and "Profit impact" in t and "+$286" in t
    assert "Today&#x27;s plan" in t or "Today's plan" in t
    assert "With your change" in t and "Tacos orders (demand)" in t
    for step in ("Review action", "What to do", "Expected impact", "ⓘ Why it may help", "ⓘ Main risks",
                 "ⓘ What to check"):
        assert step in t, step
    assert any("assumption" in c.label for c in at.checkbox)
    at.radio(key="seg_sl_mode").set_value("Order more or less").run()
    assert at.session_state["sl_price"] == 0.0 and "sl_price" not in {x.key for x in at.slider}
    at.slider(key="sl_supply").set_value(-50.0).run()
    at.radio(key="seg_sl_h").set_value(7).run()
    assert "ingredient orders -50%" in md(at).lower() and "next week" in md(at)
    at.button(key="sl_reset").click().run()
    assert at.slider(key="sl_supply").value == 0.0 and not at.exception
    at.radio(key="seg_sl_mode").set_value("Combine changes").run()
    assert {"sl_price", "sl_supply", "sl_cost", "sl_shock", "sl_mkt"} <= {x.key for x in at.slider}
    for name in ("Burritos", "Quesadillas", "Nachos", "Drinks"):
        at.selectbox(key="sl_product").set_value(name).run()
        assert not at.exception, name


def test_decision_lab_understands_a_typed_what_if(at):
    at.radio(key="nav").set_value("Decision Lab").run()
    at.text_input(key="lab_q").input("What if I increase burrito prices by 4%?")
    at.get("form_submit_button")[0].click().run()
    assert at.selectbox(key="sl_product").value == "Burritos" and at.slider(key="sl_price").value == 4.0
    assert "Burritos price +4%" in md(at)
    at.text_input(key="lab_q").input("What if supplier costs rise 8%?")
    at.get("form_submit_button")[0].click().run()
    assert at.radio(key="seg_sl_mode").value == "Supplier prices change" and at.slider(key="sl_cost").value == 8.0
    at.text_input(key="lab_q").input("make it better")
    at.get("form_submit_button")[0].click().run()
    assert any("percentage" in c.value for c in at.caption) and not at.exception


def test_supplier_price_change_lowers_profit(at):
    at.radio(key="nav").set_value("Decision Lab").run()
    at.radio(key="seg_sl_mode").set_value("Supplier prices change").run()
    at.slider(key="sl_cost").set_value(10.0).run()
    t = md(at)
    assert "supplier ingredient costs +10%" in t.lower() and "big-number neg" in t
    assert "Very likely to reduce profit." in t and "Don&#x27;t make this change" in t or "Don't make this change" in t
    assert "Check the new prices on your latest supplier invoices." in [c.label for c in at.checkbox]
    assert at.session_state["session"].state.product_scenario["ingredient_cost_change_pct"] == 10.0


def test_local_market_positions_and_opportunities(at):
    at.radio(key="nav").set_value("Local Market").run()
    t = md(at)
    assert "Prices up to date" in t or "Prices need re-checking" in t
    assert "Your prices vs nearby" in t and "Not enough comparable menus" in t  # burritos: not equivalent
    assert "Your tacos are priced 15% below the local middle." in t and "Worth investigating" in t
    at.button(key="go_idea_tacos").click().run()
    assert at.radio(key="nav").value == "Decision Lab" and at.slider(key="sl_price").value == 5.0


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


def test_ask_ai_panel_answers_and_links_to_the_dashboard(at):
    assert "chat_in" not in {c.key for c in at.chat_input}  # closed until opened
    at.button(key="chat_toggle").click().run()
    labels = [x.label for x in at.button if x.key and x.key.startswith("suggest_")]
    assert "What should I focus on today?" in labels and "Which product is making the most money?" in labels
    at.button(key="suggest_2").click().run()
    reply = at.chat_message[0]  # newest first
    assert "Most profitable" in reply.markdown[0].value and "Every number checked" in reply.caption[0].value
    assert not reply.markdown[0].value.startswith("Offline analyst")  # mode shown once, in the header
    assert not [x for x in at.button if x.key and x.key.startswith("suggest_")]  # prompts only when empty
    at.button(key="chatlink_1_0").click().run()  # "See your products"
    assert at.radio(key="nav").value == "My Business" and at.radio(key="seg_mb_section").value == "Products"
    at.button(key="chat_toggle").click().run()
    assert not at.session_state["chat_open"]


def test_lab_question_opens_the_assistant(at):
    """Regression (5.2 audit, kept): the Lab's question is answered, not lost."""
    at.radio(key="nav").set_value("Decision Lab").run()
    at.slider(key="sl_price").set_value(5.0).run()
    at.button(key="go_analyst").click().run()
    assert not at.exception and at.session_state["chat_open"]
    reply = at.chat_message[0]
    assert "Tacos price +5%" in reply.markdown[0].value and "Every number checked" in reply.caption[0].value


def test_chat_is_multi_turn_and_uses_page_context(at):
    at.radio(key="nav").set_value("Decision Lab").run()
    at.selectbox(key="sl_product").set_value("Nachos").run()
    at.button(key="chat_toggle").click().run()
    at.chat_input(key="chat_in").set_value("What if I raise the price 4%?").run()
    assert "Nachos price +4%" in at.chat_message[0].markdown[0].value
    at.chat_input(key="chat_in").set_value("What about 2% instead?").run()
    assert "Nachos price +2%" in at.chat_message[0].markdown[0].value
    at.chat_input(key="chat_in").set_value("Which is safer?").run()
    last = at.chat_message[0].markdown[0].value
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
    assert at.chat_input(key="chat_in").disabled
    t0 = time.time()
    at.radio(key="nav").set_value("My Business").run()
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
    at.button(key="open_diag").click().run()
    assert "# Settings" in md(at) and at.radio(key="nav").value == "Overview"
    assert at.radio(key="seg_settings").value == "Advanced diagnostics"
    for sec in ("Model validation", "Uncertainty calibration", "Reconciliation", "Assumptions"):
        at.radio(key="seg_ev_section").set_value(sec).run()
        assert not at.exception, sec
    at.button(key="settings_close").click().run()
    assert at.session_state["view"] is None and "Business attention" in md(at)
    at.button(key="open_data").click().run()
    assert "Upload your own daily records" in md(at) and "not connected to a real bank" in md(at)


def test_attention_bar_investigate_flow(at):
    """Observe -> detect -> explain -> simulate: the alert opens the evidence, then the Lab."""
    at.button(key="act_attn_0").click().run()  # "Sales were 31% below a usual Friday" -> Investigate
    assert at.radio(key="seg_mb_section").value == "Sales & profit"
    t = md(at)
    assert "Why this was flagged" in t and "Usual Friday" in t and "not why" in t
    at.radio(key="nav").set_value("Overview").run()
    at.button(key="go_alert_1").click().run()  # Nachos sold less than usual -> product evidence
    t = md(at)
    assert "## Nachos" in t and "Why this was flagged" in t
    at.button(key="act_inv_price").click().run()
    assert at.radio(key="nav").value == "Decision Lab" and at.selectbox(key="sl_product").value == "Nachos"
    assert at.radio(key="seg_sl_mode").value == "Change a price" and not at.exception


def test_review_action_approval_is_recorded_not_executed(at, tmp_path):
    import json
    import os
    at.radio(key="nav").set_value("Decision Lab").run()
    at.slider(key="sl_price").set_value(5.0).run()
    assert at.button(key="cta_approve").disabled  # needs the acknowledgement first
    at.checkbox(key="lab_ack").check().run()
    at.button(key="cta_approve").click().run()
    from pathlib import Path
    log = json.loads(Path(os.environ["MSRD_DECISION_LOG"]).read_text())
    assert log[-1]["action"] == "Tacos price +5%" and log[-1]["executed_automatically"] is False
    assert log[-1]["expected_profit_change"] == 286 and log[-1]["downside"] == -5
    assert "Make the change yourself" in at.session_state["lab_approved"]
    at.slider(key="sl_price").set_value(10.0).run()  # outside the owner's limit: no approve button
    assert "cta_approve" not in {x.key for x in at.button}
