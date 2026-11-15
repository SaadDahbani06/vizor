"""Main Street Risk Desk — Streamlit interface (Milestone 5.4: simple, owner-friendly).

    ./start.sh            (opens http://localhost:8501 in your browser)
    streamlit run app/streamlit_app.py

Four pages written for an owner, not an analyst: Overview (three numbers, what needs attention,
one idea), My Business (sales, products, stock, costs, cash, outlook), Decision Lab (try a change
before making it) and Local Market (verified public menu prices). "Ask your analyst" floats on
every page. Notifications (iMessage) and Advanced diagnostics (validation, calibration,
reconciliation, banking sandbox) live under Settings.

Every number comes from the analytics modules (``decisiondesk.dashboard``, ``decisiondesk.briefing``,
``decisiondesk.decision.product_scenario``, ``decisiondesk.agent.tools``); this file only lays them
out and words them plainly. The technical detail (data origin, ranges, assumptions) stays one tap
away. Nothing is executed automatically: every action needs the owner's approval.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from decisiondesk import dashboard as dash
from decisiondesk.agent.agent import DecisionAgent
from decisiondesk.agent.tools import BusinessSession, ProductLayerUnavailable, run_tool
from decisiondesk.profile import ROLES, US_STATES, BusinessProfile, ColumnMapping
from decisiondesk.ui import charts, views
from decisiondesk.ui import theme as T

load_dotenv(".env")
FAST = os.getenv("MSRD_FAST") == "1"
MODELS = ("seasonal_naive", "weekday_mean_12w", "historical_mean_28d", "ridge") if FAST else None
PAGES = ["Overview", "My Business", "Decision Lab", "Local Market"]
MB_SECTIONS = ["Sales & profit", "Products", "Stock", "Costs", "Cash & bills", "Next 4 weeks"]
SETTINGS = ["Notifications", "Data", "Advanced diagnostics"]
MODES = ["Change a price", "Order more or less", "Supplier prices change", "Busier or quieter days",
         "Marketing spend", "Combine changes"]
LAB_KEYS = ("sl_price", "sl_supply", "sl_extra", "sl_cost", "sl_shock", "sl_mkt")
MODE_KEYS = {"Change a price": ("sl_price",), "Order more or less": ("sl_supply", "sl_extra"),
             "Supplier prices change": ("sl_cost",), "Busier or quieter days": ("sl_shock",),
             "Marketing spend": ("sl_mkt",), "Combine changes": LAB_KEYS}

st.set_page_config(page_title="Main Street Risk Desk", layout="wide", page_icon="🏪",
                   initial_sidebar_state="collapsed")
st.markdown(T.CSS, unsafe_allow_html=True)


@st.cache_resource
def _executor() -> ThreadPoolExecutor:
    """Background threads for slow work (Gemini replies, message polling)."""
    return ThreadPoolExecutor(max_workers=4, thread_name_prefix="msrd")


def go(page: str, **state) -> None:
    """Navigation callback: switch page (closing Settings) and preset widgets on the target."""
    st.session_state["nav"] = page
    st.session_state["view"] = None
    for k, v in state.items():
        st.session_state[k] = v


def open_settings(section: str = "Notifications") -> None:
    st.session_state["view"] = "settings"
    st.session_state["seg_settings"] = section


def close_settings() -> None:
    st.session_state["view"] = None


def _session() -> BusinessSession | None:
    return st.session_state.get("session")


def _reset_lab() -> None:
    for k in [k for k in st.session_state if k.startswith(("sl_", "lab_", "brief", "seg_sl_mode"))]:
        del st.session_state[k]


def _load_demo(key: str) -> None:
    with st.spinner("Getting your numbers ready..."):
        s = BusinessSession.from_demo(key, models=MODELS, n_sims=1000 if FAST else 2000)
    _reset_lab()
    st.session_state.update(session=s, agent=DecisionAgent(s), chat=[], business_key=key,
                            last_decision=None, pending=None)


# ------------------------------------------------------------------------------ business picker
def business_picker() -> None:
    """Data source. Rendered on every run (sidebar) so its state persists."""
    choice = st.radio("Business", ["Demo restaurant (Austin, TX)", "Demo retailer (Columbus, OH)",
                                   "Upload CSV / Excel"], key="source")
    if choice.startswith("Demo"):
        key = "restaurant" if "restaurant" in choice else "retail"
        if st.session_state.get("business_key") != key:
            _load_demo(key)
        return
    up = st.file_uploader("Daily data file", type=["csv", "xlsx"], key="upload")
    st.caption("Daily totals (date, revenue, costs; optional units and cash). Product-level "
               "files are a planned next step: see docs/ONBOARDING.md.")
    if up is None:
        return
    dest = Path("data/private/uploads")
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / up.name
    path.write_bytes(up.getvalue())
    cols = list(pd.read_csv(path, nrows=0).columns) if path.suffix == ".csv" else \
        list(pd.read_excel(path, nrows=0).columns)
    st.caption("Map your columns to roles (leave unused roles empty).")
    mapping = {}
    for role in ROLES:
        pick = st.selectbox(role, ["—", *cols], key=f"map_{role}")
        if pick != "—":
            mapping[pick] = role
    name = st.text_input("Business name", key="up_name")
    industry = st.selectbox("Industry", ["restaurant", "retail", "other"], key="up_ind")
    state = st.selectbox("State", sorted(US_STATES), key="up_state")
    objective = st.selectbox("Objective", ["max_expected_profit", "min_operating_cost",
                                           "min_cash_shortage_risk", "min_stockout_risk"], key="up_obj")
    max_price = st.number_input("Max price increase (fraction)", 0.0, 0.5, 0.05, key="up_mp")
    opening = st.number_input("Current cash balance (USD, optional)", value=0.0, key="up_cash")
    confirm = st.checkbox("I confirm industry, state, objective and constraints", key="up_ok")
    if st.button("Load upload", key="up_load", disabled=not (confirm and name)):
        try:
            profile = BusinessProfile(name, industry, state, objective,
                                      {"max_price_increase": max_price}, confirmed_by_user=confirm)
            s_ = BusinessSession.from_upload(path, ColumnMapping(mapping), profile,
                                             opening_cash=opening or None, models=MODELS)
            _reset_lab()
            st.session_state.update(session=s_, agent=DecisionAgent(s_), chat=[],
                                    business_key=f"upload:{up.name}", last_decision=None, pending=None)
        except ValueError as exc:
            st.error(f"Upload rejected: {exc}")


with st.sidebar:
    st.markdown("### Business & data")
    business_picker()

s = _session()
if s is None:
    st.warning("Choose a demo business or upload your data in the sidebar.")
    st.stop()

if "nav" not in st.session_state:  # deep links: ?page=Decision%20Lab, ?view=settings&section=...
    st.session_state["nav"] = st.query_params.get("page") if st.query_params.get("page") in PAGES else PAGES[0]
    if st.query_params.get("section") in MB_SECTIONS:
        st.session_state["seg_mb_section"] = st.query_params.get("section")
    if st.query_params.get("view") == "settings":
        open_settings(st.query_params.get("section") if st.query_params.get("section") in SETTINGS
                      else "Notifications")
    if st.query_params.get("chat") == "open":
        st.session_state["chat_open"] = True
st.session_state.setdefault("view", None)


def product_ctx():
    try:
        return s.product_context()
    except ProductLayerUnavailable:
        return None


def empty_state(what: str) -> None:
    st.markdown(f'<div class="callout">{T.esc(what)} This needs daily sales for each item (units, price, '
                "cost and stock). The demo restaurant has them; this business has daily totals only.</div>",
                unsafe_allow_html=True)


def seg(label: str, options: list, key: str, default=None, fmt=None, on_change=None) -> object:
    st.session_state.setdefault(key, default if default is not None else options[0])
    return st.radio(label, options, horizontal=True, key=key, format_func=fmt or str,
                    label_visibility="collapsed", on_change=on_change)


def how(text: str, label: str = "ⓘ How we worked this out") -> None:
    n = st.session_state["_tip_n"] = st.session_state.get("_tip_n", 0) + 1
    with st.container(key=f"tip_{n}"), st.popover(label, width="content"):
        st.markdown(text)


def weeks(h: int) -> str:
    return {7: "next week", 14: "next 2 weeks", 28: "next 4 weeks"}.get(int(h), f"next {h} days")


def tries(p: float) -> str:
    return f"{round(p * 20)} of 20"


def _briefing() -> dict:
    from decisiondesk.briefing import daily_briefing
    key = ("brief", s.dataset, repr(asdict(s.state)))
    if st.session_state.get("brief_key") != key:
        st.session_state["brief"] = daily_briefing(s, market_as_of=date.today())
        st.session_state["brief_key"] = key
    return st.session_state["brief"]


def current_product() -> str | None:
    if product_ctx() is None:
        return None
    page_ = st.session_state.get("nav")
    return {"My Business": st.session_state.get("prod_pick"),
            "Decision Lab": st.session_state.get("sl_product"),
            "Local Market": st.session_state.get("lm_product")}.get(page_) or (
        "Tacos" if page_ in ("Decision Lab", "Local Market") else None)


def lab_link(product: str | None = None, mode: str = "Change a price", **vals) -> dict:
    """Keyword arguments for ``go('Decision Lab', ...)`` that open one kind of change."""
    kw = {k: 0.0 for k in LAB_KEYS} | {"seg_sl_mode": mode, "lab_link_applied": True} | vals
    if product:
        kw["sl_product"] = product
    return kw


# ------------------------------------------------------------------------------ header
st.session_state["_tip_n"] = 0  # unique keys for the info tips on each run
b = _briefing()
hdr = st.columns([6, 1, 1.25], vertical_alignment="center")
hdr[0].markdown(f'<div class="msrd-brand"><span>MSRD</span>{T.esc(s.metadata.get("business_name", s.dataset))}</div>',
                unsafe_allow_html=True)
with hdr[1].popover(f"🔔 {len(b['alerts'])}", width="stretch"):
    st.markdown("**Today**")
    if not b["alerts"]:
        st.caption("Nothing needs your attention today.")
    for a in b["alerts"]:
        st.markdown(f'<div class="alert-row"><span class="dot {a["severity"]}"></span><div>'
                    f'<div class="alert-title">{T.esc(a["title"])}</div>'
                    f'<div class="alert-detail">{T.esc(a["detail"])}</div></div></div>', unsafe_allow_html=True)
    st.button("Get these on my phone →", key="bell_settings", on_click=open_settings, args=("Notifications",))
hdr[2].button("⚙ Settings", key="open_settings", on_click=open_settings, args=("Notifications",),
              width="stretch")
demo = "SYNTHETIC" in s.data_label.upper()
st.markdown(f'<span class="demo-banner">{"Demo data: " + T.esc(s.metadata.get("business_name", "")) + " is made up (" + T.esc(s.data_label) + " records)" if demo else "Your data: " + T.esc(s.data_label)}'
            f' · up to {s.origin.date():%b %d, %Y} · nothing changes without your OK</span>', unsafe_allow_html=True)
page = st.radio("Navigate", PAGES, horizontal=True, key="nav", label_visibility="collapsed",
                on_change=close_settings)


# ================================================================================ ANALYST CHAT
TOOL_LABELS = {"get_business_overview": "business overview", "forecast_business": "forecast",
               "evaluate_restaurant_actions": "whole-business options", "evaluate_inventory_policies": "stock options",
               "assess_cash_risk": "cash outlook", "get_product_performance": "product numbers",
               "simulate_product_scenario": "Decision Lab", "explain_profit_change": "profit changes",
               "get_local_market_prices": "local menu prices", "get_daily_briefing": "today's briefing",
               "get_assumptions": "assumptions", "get_banking_summary": "banking sandbox",
               "create_alert_rule": "alert rules", "list_alerts": "alerts"}


def suggestions() -> list[str]:
    p = current_product()
    pl = (p or "taco").lower()
    if product_ctx() is None:
        return {"Overview": ["What should I focus on today?", "How did yesterday go?", "Will I run out of cash?"],
                "My Business": ["How is my business doing?", "Why did profit change?", "Forecast my revenue"],
                "Decision Lab": ["What should I do?", "What should I do with a $40,000 inventory budget?"],
                }.get(page, ["How is my business doing?"])
    return {"Overview": ["What should I focus on today?", "Why did revenue fall yesterday?",
                         "Which of my five products is most profitable?"],
            "My Business": ["Which of my five products is most profitable?", "Why did profit change?",
                            f"Should I buy more {pl} ingredients?"],
            "Decision Lab": [f"What if I raise {pl} prices 5%?", "What about 3% instead?", "Which is safer?",
                             "What if supplier costs rise 8%?", "Why would a price increase lower demand?"],
            "Local Market": [f"How do my {pl} prices compare with nearby restaurants?",
                             f"What if I raise {pl} prices 5%?"]}.get(page, [])


def _ask(q: str) -> None:
    agent: DecisionAgent = st.session_state["agent"]
    ctx = {"page": page, "product": current_product()}
    st.session_state["chat"].append({"role": "user", "text": q})
    st.session_state["chat_open"] = True
    if agent.mode == "offline":
        try:
            _append_reply(agent.ask(q, ctx))
        except Exception as exc:  # noqa: BLE001 - shown to the user, never silent
            st.session_state["chat"].append({"role": "assistant", "text": f"Sorry, that failed: {exc}",
                                             "error": True})
    else:  # slow language model: run in the background so the dashboard stays usable
        st.session_state["pending"] = {"future": _executor().submit(agent.ask, q, ctx), "q": q,
                                       "t0": time.time()}


def _append_reply(reply) -> None:
    used = sorted({TOOL_LABELS.get(c["name"], c["name"]) for c in reply.tool_calls if "output" in c})
    check = ("every number checked ✓" if reply.grounded else "⚠ some numbers could not be checked")
    meta = f"Used: {', '.join(used)} · {check}" if used else check
    st.session_state["chat"].append({"role": "assistant", "text": reply.text, "meta": meta,
                                     "grounded": reply.grounded})


def _toggle_chat() -> None:
    st.session_state["chat_open"] = not st.session_state.get("chat_open", False)


def analyst_panel() -> None:
    agent: DecisionAgent = st.session_state["agent"]
    model = f" · {agent.gemini.model}" if getattr(agent, "gemini", None) else ""
    sub = {"gemini": "Gemini" + model, "claude": "Claude", "offline": "Offline mode (no AI model)"}
    st.markdown('<div class="analyst-head"><div class="analyst-dot">FA</div><div>'
                '<div class="analyst-name">Your Financial Analyst</div>'
                f'<div class="analyst-sub">{T.esc(sub.get(agent.mode, agent.mode))} · uses your numbers, '
                'never makes them up</div></div></div>', unsafe_allow_html=True)
    if getattr(agent, "last_fallback", None):
        st.caption(f"⚠ The AI model was unavailable ({agent.last_fallback}); the last answer came from "
                   "offline mode.")
    pending = st.session_state.get("pending")
    chat = st.session_state["chat"]
    if not chat:
        st.markdown(f'<div class="soft">Ask anything about your sales, items, costs or cash. I can see you are on '
                    f'<b>{T.esc(page)}</b>' + (f' looking at <b>{T.esc(current_product())}</b>' if current_product() else "")
                    + '. You can follow up, like “what about 3% instead?”.</div>', unsafe_allow_html=True)
    else:
        with st.container(height=360, border=False, key="chat_box"):
            for m in chat:
                with st.chat_message(m["role"], avatar="🧑‍🍳" if m["role"] == "user" else "📊"):
                    if m.get("error"):
                        st.error(m["text"])
                    else:
                        st.markdown(m["text"])
                    if m.get("meta"):
                        st.caption(m["meta"])

    @st.fragment(run_every="2s" if pending else None)
    def waiting():
        p = st.session_state.get("pending")
        if not p:
            return
        if p["future"].done():
            try:
                _append_reply(p["future"].result())
            except Exception as exc:  # noqa: BLE001 - shown to the user, never silent
                st.session_state["chat"].append({"role": "assistant", "text": f"Sorry, that failed: {exc}",
                                                 "error": True})
            st.session_state["pending"] = None
            st.rerun()
        with st.spinner(f"Thinking about “{p['q']}” ({time.time() - p['t0']:.0f} s). You can keep "
                        "using the dashboard."):
            time.sleep(0.1)
    waiting()
    with st.container(horizontal=True, wrap=True, key="suggestions"):
        for j, q in enumerate(suggestions()):
            if st.button(q, key=f"suggest_{j}", width="content", disabled=bool(pending)):
                _ask(q)
                st.rerun()
    q = st.chat_input("Type your question…", key="chat_in", disabled=bool(pending))
    if q:
        _ask(q)
        st.rerun()
    if chat and st.button("Start over", key="chat_clear", disabled=bool(pending)):
        st.session_state["chat"] = []
        st.session_state["agent"] = DecisionAgent(s)
        st.rerun()


def floating_chat() -> None:
    pending = st.session_state.get("pending")
    is_open = st.session_state.get("chat_open", False)
    label = "✕ Close" if is_open else ("💬 Thinking…" if pending else "💬 Ask your analyst")
    if is_open:
        with st.container(key="chat_panel"):
            analyst_panel()
    with st.container(key="chat_fab"):
        st.button(label, key="chat_toggle", on_click=_toggle_chat)


# ================================================================================ OVERVIEW
def page_overview() -> None:
    hour = datetime.now().hour
    hello = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    y = b["yesterday"]
    rc = y["revenue_change_pct"]
    wd = b["weekday"]
    mood = ("" if rc is None else f"Yesterday was slower than a usual {wd}. " if rc <= -10 else
            f"Yesterday was busier than a usual {wd}. " if rc >= 10 else f"Yesterday was a normal {wd}. ")
    n = len(b["alerts"])
    need = ("Nothing needs your attention today." if n == 0 else
            "One thing needs your attention." if n == 1 else f"{min(n, 3)} things need your attention.")
    st.markdown(f'<div class="greeting">{hello}.</div><div class="greeting-sub">{T.esc(mood)}{need}</div>',
                unsafe_allow_html=True)

    k = dash.kpis(s, "mtd")
    rev, prof, c = k["revenue"], k["net_profit"], k["cash"]
    bills = b.get("bills_due_7d_total") or 0.0
    cols = st.columns(3, gap="large")
    with cols[0], st.container(key="kpi_revenue"):
        chg = rev["change_pct"]
        sub = ("" if chg is None else f"{'▲' if chg > 0 else '▼'} {abs(chg):.0f}% {'more' if chg > 0 else 'less'} "
               "than the same days last month")
        st.markdown(T.kpi_html("Sales this month", T.money(rev["value"]), sub=sub), unsafe_allow_html=True)
        how(f"All sales recorded from {k['period_label'].replace(',', ', ')}, compared with {k['comparison_label']}.")
        st.button("Sales details →", key="go_revenue", on_click=go, args=("My Business",),
                  kwargs={"seg_mb_section": "Sales & profit"})
    with cols[1], st.container(key="kpi_profit"):
        m = prof["margin_pct"]
        sub = (f"You keep about {max(0, round(m))}¢ of every $1 of sales" if m is not None and m > 0
               else "Costs were higher than sales so far this month")
        st.markdown(T.kpi_html("Profit this month", T.money(prof["value"]), sub=sub), unsafe_allow_html=True)
        how(prof["definition"] + " It is before taxes and loan repayments.")
        st.button("Where the money goes →", key="go_profit", on_click=go, args=("My Business",),
                  kwargs={"seg_mb_section": "Costs"})
    with cols[2], st.container(key="kpi_cash"):
        sub = ("" if c["value"] is None else f"✓ Covers this week's bills ({T.money(bills)})" if c["value"] >= bills
               else f"⚠ Less than this week's bills ({T.money(bills)})")
        st.markdown(T.kpi_html("Cash on hand", T.money(c["value"]), sub=sub), unsafe_allow_html=True)
        how(c["source"] + ". " + c.get("runway_note", ""))
        st.button("Cash & bills →", key="go_cash", on_click=go, args=("My Business",),
                  kwargs={"seg_mb_section": "Cash & bills"})

    st.markdown('<div class="section-title">What needs your attention</div>', unsafe_allow_html=True)
    with st.container(key="card_briefing"):
        if not b["alerts"]:
            st.markdown('<div class="plain">All good: nothing unusual yesterday.</div>', unsafe_allow_html=True)
        for j, a in enumerate(b["alerts"][:3]):
            row = st.columns([6, 1.3], vertical_alignment="center")
            row[0].markdown(f'<div class="alert-row"><span class="dot {a["severity"]}"></span><div>'
                            f'<div class="alert-title">{T.esc(a["title"])}</div>'
                            f'<div class="alert-detail">{T.esc(a["detail"])}</div>'
                            f'<div class="alert-next">→ {T.esc(a["next_step"])}</div></div></div>',
                            unsafe_allow_html=True)
            if a.get("page"):
                kw = {}
                if a["page"] == "Decision Lab":
                    mode = "Supplier prices change" if a["kind"] == "cost_increase" else "Order more or less"
                    kw = lab_link(a.get("product"), mode)
                elif a.get("product"):
                    kw = {"seg_mb_section": "Products", "prod_pick": a["product"]}
                else:
                    kw = {"seg_mb_section": "Cash & bills" if a["kind"] in ("cash", "bills") else "Sales & profit"}
                row[1].button("Look →", key=f"go_alert_{j}", on_click=go, args=(a["page"],), kwargs=kw)
        extra = len(b["alerts"]) - 3 + (b.get("alerts_suppressed") or 0)
        if extra > 0:
            st.caption(f"{extra} smaller item(s) are in the 🔔 at the top.")
        how(b["note"] + " We compare yesterday with the average of the same weekday over the last 4 weeks.",
            "ⓘ How we decide what to show")

    st.markdown('<div class="section-title">One idea to consider</div>', unsafe_allow_html=True)
    with st.container(key="card_decision"):
        decision_card()
    st.button("See all your numbers →", key="go_all", on_click=go, args=("My Business",))


def plain_action(a: dict) -> str:
    sc = a.get("scenario") or {}
    name = a["product"].lower()
    if sc.get("price_change_pct"):
        p = sc["price_change_pct"]
        return f"{'Raise' if p > 0 else 'Lower'} the price of {name} by {abs(p):g}%"
    if sc.get("supply_change_pct"):
        p = sc["supply_change_pct"]
        return f"Order {'more' if p > 0 else 'less'} for {name} ({abs(p):g}%)"
    return a["action"]


def impact_block(change: float, lo: float, hi: float, p: float, h: int) -> None:
    good = change > 0
    st.markdown(f'<div class="big-line {"good" if good else "bad"}">About {T.signed_money(change)} profit '
                f'over the {weeks(h)}</div>'
                f'<div class="plain">Could be anywhere from {T.signed_money(lo)} to {T.signed_money(hi)}. '
                f'It came out ahead in {tries(p)} of our test runs.</div>', unsafe_allow_html=True)
    how("We ran your next weeks a thousand times, each time with forecast errors taken from how wrong our "
        "forecasts were on past weeks they had not seen, and with customer reactions drawn from a stated "
        "range. The middle result is the 'about' number; 9 in 10 runs fell inside the 'anywhere from' range. "
        "These are estimates under assumptions, not promises (and the data here is a demo).")


def decision_card() -> None:
    a = b.get("action")
    if a is None:
        st.markdown('<div class="plain">Try a change before you make it: prices, orders, supplier costs or a '
                    'stock budget.</div>', unsafe_allow_html=True)
        st.button("Open the Decision Lab", key="cta_lab", on_click=go, args=("Decision Lab",))
        return
    st.markdown(f'<div class="idea">{T.esc(plain_action(a))}</div>', unsafe_allow_html=True)
    if a.get("impact"):
        i = a["impact"]
        impact_block(i["profit_change"], i["range_90"][0], i["range_90"][1], i["probability_better_pct"] / 100,
                     i["horizon_days"])
        st.markdown(f'<div class="soft">Why it might help: {T.esc(a["why"])}</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="plain">{T.esc(a["why"])}</div>', unsafe_allow_html=True)
    with st.expander("Before you decide"):
        checklist(a)
    if a.get("scenario"):
        sc = a["scenario"]
        mode = "Change a price" if sc["price_change_pct"] else "Order more or less"
        st.button("Try it in the Decision Lab", key="cta_brief_lab", on_click=go, args=("Decision Lab",),
                  kwargs=lab_link(a["product"], mode, sl_price=sc["price_change_pct"],
                                  sl_supply=sc["supply_change_pct"]))


def checklist(a: dict, key: str | None = None) -> None:
    """Approval-first checklist: uncertainty, assumptions, what could go wrong, what to verify."""
    if a.get("risks"):
        st.markdown('<div class="check-head">What could go wrong</div>', unsafe_allow_html=True)
        st.markdown("\n".join(f"- {r}" for r in a["risks"]))
    st.markdown('<div class="check-head">Check first</div>', unsafe_allow_html=True)
    if key:
        for j, v in enumerate(a["verify"]):
            st.checkbox(v, key=f"{key}_v{j}")
    else:
        st.markdown("\n".join(f"- {v}" for v in a["verify"]))
    if a.get("assumptions"):
        st.markdown('<div class="check-head">What we assumed</div>', unsafe_allow_html=True)
        st.markdown("\n".join(f"- {x}" for x in a["assumptions"]))
    if a.get("uncertainty"):
        st.markdown('<div class="check-head">How sure we are</div>', unsafe_allow_html=True)
        st.markdown(a["uncertainty"])
    st.markdown("**This needs your OK.** Nothing changes automatically: if you decide to do it, you make the "
                "change yourself (menu, POS or supplier order).")


# ================================================================================ MY BUSINESS
def page_my_business() -> None:
    st.markdown("# My Business")
    section = seg("Section", MB_SECTIONS, "seg_mb_section", "Sales & profit")
    ctx = product_ctx()
    h = s.history
    end = h["date"].max().date()
    start, stop = pd.Timestamp(end) - pd.Timedelta(days=89), pd.Timestamp(end)
    if section in ("Sales & profit", "Stock"):
        span = seg("Range", [30, 90, 365], "seg_mb_range", 90, lambda d: f"Last {d} days")
        start = stop - pd.Timedelta(days=int(span) - 1)
    {"Sales & profit": lambda: section_sales(ctx, start, stop), "Products": lambda: section_products(ctx),
     "Stock": lambda: section_inventory(ctx, start, stop), "Costs": section_expenses,
     "Cash & bills": lambda: section_cash(start, stop), "Next 4 weeks": section_forecasts}[section]()


def section_sales(ctx, start, stop) -> None:
    h = s.history.copy()
    h["profit"] = h["revenue"] - h["variable_costs"] - h["fixed_costs"]
    span = (stop - start).days + 1
    vol = "Customers" if s.industry == "restaurant" else "Items sold"
    metrics = {"Sales": ("revenue", "sum", "$"), "Profit": ("profit", "sum", "$")}
    if "units_sold" in h:
        metrics[vol] = ("units_sold", "sum", "#")
        metrics["Spend per customer" if s.industry == "restaurant" else "Sales per item"] = ("per", "ratio", "$2")
    if ctx is not None:
        rc = ctx.history.recon.set_index("date")["food_cost"]
        h["gross_profit"] = h["revenue"] - h["date"].map(rc).fillna(0)
        metrics["After ingredients"] = ("gross_profit", "sum", "$")
    cur, prev = dash._slice(h, start, stop), dash._slice(h, start - pd.Timedelta(days=span), start - pd.Timedelta(days=1))

    def value(frame, col, how_):
        if how_ == "ratio":
            return frame["revenue"].sum() / frame["units_sold"].sum() if frame["units_sold"].sum() else None
        return frame[col].sum()

    def fmt(v, kind):
        return "n/a" if v is None else T.money(v, 2) if kind == "$2" else T.money(v) if kind == "$" else f"{v:,.0f}"

    names = list(metrics)
    st.markdown('<div class="soft">Tap a number to see its chart. Each is compared with the period just before.</div>',
                unsafe_allow_html=True)
    pick = seg("Metric", names, "seg_mb_metric", "Sales")
    cols = st.columns(len(names))
    for col, n in zip(cols, names):
        c_, how_, kind = metrics[n]
        v, pv = value(cur, c_, how_), value(prev, c_, how_)
        chg = (v - pv) / abs(pv) * 100 if v is not None and pv else None
        col.metric(n, fmt(v, kind), None if chg is None else f"{chg:+.1f}%")
    col, how_, kind = metrics[pick]
    d = cur[["date"]].copy()
    d["v"] = (cur["revenue"] / cur["units_sold"].replace(0, np.nan)) if how_ == "ratio" else cur[col]
    d["avg7"] = d["v"].rolling(7, min_periods=1).mean()
    explain = {
        "Sales": "Everything customers paid each day.",
        "Profit": "What was left after every cost that day (ingredients, packaging, card fees, staff, rent, "
                  "utilities). Low or negative days early in the week are normal for a restaurant.",
        "After ingredients": "Sales minus what the ingredients cost. This pays for staff, rent and everything else.",
        vol: "How many customers came in each day." if s.industry == "restaurant" else "Items sold each day.",
        "Spend per customer": "Sales divided by customers.",
        "Sales per item": "Sales divided by items sold.",
    }[pick]
    with st.container(key="panel_metric"):
        st.markdown(f"#### {pick}")
        yf = "$,.2f" if kind == "$2" else "$,.0f" if kind == "$" else ",.0f"
        st.altair_chart(charts.line_hover(d, {"Each day": "v", "Weekly average": "avg7"},
                                          colors={"Each day": "#C9C6BE", "Weekly average": T.COBALT},
                                          height=300, y_format=yf, zero_rule=pick == "Profit"), width="stretch")
        st.markdown(f'<div class="soft">{T.esc(explain)}</div>', unsafe_allow_html=True)
    if ctx is not None:
        with st.container(key="panel_contrib"):
            st.markdown("#### Sales by item, each week")
            w = views.weekly_product_revenue(ctx.history.sales, ctx.history.recon, start, stop, None)
            st.altair_chart(charts.stacked_area(w), width="stretch")
            st.markdown('<div class="soft">Your five main items plus everything else (sides, add-ons, desserts).</div>',
                        unsafe_allow_html=True)


def section_products(ctx) -> None:
    if ctx is None:
        return empty_state("Item-level numbers are not available for this business.")
    t = dash.product_table(s, "28d")
    st.markdown('<div class="soft">Last 4 weeks. Tap “Look closer” on an item.</div>', unsafe_allow_html=True)
    st.session_state.setdefault("prod_pick", t.iloc[0]["product"])
    rows = [t.iloc[:3], t.iloc[3:]]
    for r in rows:
        cols = st.columns(3, gap="large")
        for col, (_, p) in zip(cols, r.iterrows()):
            keep = p["price"] - p["unit_cost"]
            risk = p["stockout_risk_7d"]
            status = ('<span class="chip warn">may sell out this week</span>' if risk >= 0.25
                      else '<span class="chip up">stock looks fine</span>')
            with col, st.container(key=f"card_prod_{p['product_id']}"):
                st.markdown(f'<div class="prod-name">{T.esc(p["product"])}</div>'
                            f'<div class="prod-price">{T.money(p["price"], 2)}</div>'
                            f'<div class="plain">You keep {T.money(keep, 2)} of each after ingredients</div>'
                            f'<div class="soft">{p["units_sold"]:,.0f} sold · {T.money(p["revenue"])} in sales</div>'
                            f'<div style="margin-top:8px">{status}</div>', unsafe_allow_html=True)
                st.button("Look closer →", key=f"go_prod_{p['product_id']}", on_click=go, args=("My Business",),
                          kwargs={"prod_pick": p["product"], "seg_mb_section": "Products"})
    name = st.selectbox("Look closer at", list(views.NAMES.values()), key="prod_pick")
    product_detail(ctx, name, t)
    with st.expander("See all items as a table"):
        show = t.assign(stockout_risk_7d=t["stockout_risk_7d"] * 100)[
            ["product", "price", "unit_cost", "gross_margin_pct", "units_sold", "revenue",
             "forecast_demand_7d", "sellable_units", "stockout_risk_7d", "days_of_supply"]]
        st.dataframe(show, hide_index=True, width="stretch", column_config={
            "product": "Item", "price": st.column_config.NumberColumn("Price", format="$%.2f"),
            "unit_cost": st.column_config.NumberColumn("Ingredient cost", format="$%.2f"),
            "gross_margin_pct": st.column_config.NumberColumn("Kept after ingredients", format="%.0f%%"),
            "units_sold": st.column_config.NumberColumn("Sold (4 weeks)", format="%d"),
            "revenue": st.column_config.NumberColumn("Sales", format="$%.0f"),
            "forecast_demand_7d": st.column_config.NumberColumn("Expected orders next week", format="%.0f"),
            "sellable_units": st.column_config.NumberColumn("Can make from stock", format="%.0f"),
            "stockout_risk_7d": st.column_config.NumberColumn("Chance of selling out", format="%.0f%%"),
            "days_of_supply": st.column_config.NumberColumn("Days of stock", format="%.1f")})


def section_inventory(ctx, start, stop) -> None:
    inv = dash.inventory_kpi(s)
    if ctx is None:
        st.metric("Stock value", T.money(inv["value"]), help=inv["basis"])
        return empty_state("Ingredient stock is not available for this business.")
    t = dash.product_table(s, "28d")
    nd = dash.next_delivery_days(ctx.origin)
    with st.container(key="panel_inv"):
        st.markdown("#### Will anything sell out before the next delivery?")
        st.altair_chart(charts.inventory_health(views.inventory_frame(t, nd)), width="stretch")
        st.markdown(f'<div class="soft">Bar: how many you can make from what is on the shelf. Black mark: how many '
                    f'customers are expected to order before the next delivery in {nd} days. Colour: how often '
                    'it sold out in our test runs.</div>', unsafe_allow_html=True)
    with st.container(key="panel_ingr_now"):
        st.markdown(f"#### Ingredients on hand ({ctx.origin.date():%b %d})")
        st.dataframe(pd.DataFrame(inv["items"]).round(2), hide_index=True, width="stretch")
        st.markdown(f'<div class="soft">Next delivery in {inv["next_delivery_in_days"]} days (Mondays and Thursdays).</div>',
                    unsafe_allow_html=True)
    led = ctx.history.ledger
    led = led[(led["date"] >= start) & (led["date"] <= stop)].copy()
    avg_use = led.groupby("ingredient")["used"].transform(lambda x: x.rolling(28, min_periods=7).mean())
    led["days_of_supply"] = led["closing"] / avg_use.replace(0, np.nan)
    with st.expander("Stock over time and recipes"):
        st.altair_chart(charts.ingredient_history(led.dropna(subset=["days_of_supply"])), width="stretch")
        st.caption("Days of stock left after each day. Deliveries make the saw-tooth; dips near zero are near sell-outs.")
        from decisiondesk.products import BOM, INGREDIENTS
        st.dataframe(pd.DataFrame(BOM, index=list(views.NAMES.values()),
                                  columns=[f"{k.name} ({k.unit})" for k in INGREDIENTS]), width="stretch")
        st.caption("What each item uses (demo recipes).")


def section_expenses() -> None:
    period = seg("Period", ["mtd", "28d", "90d", "365d"], "seg_mb_exp", "28d", lambda k: dash.PERIODS[k])
    e = dash.expense_breakdown(s, period)
    e["pct_label"] = e["share_pct"].map(lambda v: f"{v:.0f}%")
    with st.container(key="panel_exp"):
        st.markdown("#### Where the money goes")
        st.altair_chart(charts.expense_bars(e), width="stretch")
    pc = dash.profit_change(s, 28)
    with st.container(key="panel_drivers"):
        ch = pc["profit"]["change"]
        st.markdown(f"#### Profit was {T.money(pc['profit']['current'])} in the last 4 weeks, "
                    f"{T.signed_money(ch)} compared with the 4 weeks before")
        st.markdown('<div class="soft">What moved it (positive bars helped profit, negative bars hurt it):</div>',
                    unsafe_allow_html=True)
        dfd = pd.DataFrame(pc["drivers"]).head(8)
        dfd["abs_effect"] = dfd["profit_effect"].abs()
        st.altair_chart(charts.driver_bars(dfd), width="stretch")
        how(pc["note"])


def section_cash(start, stop) -> None:
    h = s.history
    if "cash_balance" in h:
        with st.container(key="panel_cash"):
            st.markdown("#### Cash over the last 6 months")
            d = dash._slice(h, stop - pd.Timedelta(days=179), stop)[["date", "cash_balance"]]
            st.altair_chart(charts.line_hover(d, {"Cash": "cash_balance"}, colors={"Cash": T.COBALT}, height=260),
                            width="stretch")
            how("Opening balance plus all money in and out that was recorded (including owner draws). Demo "
                "ledger, not a bank balance.")
    li = dash.liabilities(s)
    if li.get("payables") is not None:
        with st.container(key="panel_bills"):
            st.markdown(f"#### Bills in the next 2 weeks: {T.money(li['due_14d'])}")
            st.markdown(f'<div class="soft">Including supplier invoices not yet paid ({T.money(li["payables"])}, '
                        f'paid {li["payment_terms_days"]} days after delivery).</div>', unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(li["scheduled_bills"]), hide_index=True, width="stretch")
    if st.button("Will I run short of cash in the next 4 weeks?", key="mb_cash_risk"):
        st.session_state["mb_cash"] = run_tool(s, "assess_cash_risk", {"starting_cash": None})
    cr = st.session_state.get("mb_cash")
    if cr:
        if not cr["available"]:
            st.warning(f"We need your current cash balance for this: {cr['reason']}.")
        else:
            lo, hi = cr["ending_cash_80pct_interval"]
            st.markdown(f'<div class="plain">In 4 weeks you would most likely have about '
                        f'<b>{T.money(cr["median_ending_cash"])}</b> (usually between {T.money(lo)} and {T.money(hi)}). '
                        f'Share of test runs where cash ran short: <b>{T.esc(cr["simulated_shortfall_frequency"])}</b>.</div>',
                        unsafe_allow_html=True)
            how(f"{cr['calibration']}. {cr['warning']}")


def section_forecasts() -> None:
    h = s.history
    names = {"revenue": "Sales", "units_sold": "Customers" if s.industry == "restaurant" else "Items sold",
             "variable_costs": "Costs that move with sales", "net_cash_flow": "Cash in minus cash out"}
    targets = [t for t in names if t == "net_cash_flow" or t in h.columns]
    target = st.selectbox("What to look ahead at", targets, key="fc_target", format_func=names.get)
    with st.spinner("Looking ahead..."):
        fc = run_tool(s, "forecast_business", {"target": target})
    v = fc["interval_80_total_validation"]
    money = target != "units_sold"
    f = (lambda x: T.money(x)) if money else (lambda x: f"{x:,.0f}")
    lo, hi = fc["interval_80_total"]
    with st.container(key="panel_fc"):
        st.markdown(f'<div class="big-line">{names[target]} in the next 4 weeks: about {f(fc["point_total"])}</div>'
                    f'<div class="plain">Most likely between {f(lo)} and {f(hi)}.</div>', unsafe_allow_html=True)
        from decisiondesk.decision.baseline import make_draws
        bundle = s.bundle((target,))
        tf = bundle.targets[target]
        sims = bundle.simulate(make_draws(bundle, 2000, seed=11))[target]
        y = h[target] if target != "net_cash_flow" else (h.revenue - h.variable_costs - h.fixed_costs)
        past = pd.DataFrame({"date": h["date"], "So far": y}).tail(84)
        fut = pd.DataFrame({"date": tf.dates, "Expected": tf.point,
                            "lo": np.quantile(sims, .1, axis=0), "hi": np.quantile(sims, .9, axis=0)})
        both = pd.concat([past, fut], ignore_index=True)
        st.altair_chart(charts.line_hover(both[["date", "So far", "Expected"]], {"So far": "So far", "Expected": "Expected"},
                                          colors={"So far": T.INK, "Expected": T.COBALT}, dashed=("Expected",),
                                          y_format="$,.0f" if money else ",.0f"), width="stretch")
    _range_warning(v)
    with st.expander("How we worked this out"):
        st.write(f"We tried several forecasting methods on past weeks they had not seen and kept the most "
                 f"accurate: **{fc['selected_model']}** ({fc['selection_reason']}). Typical error on unseen data "
                 f"(WAPE): {fc['final_test']['wape']}. Past 4-week totals inside the range: "
                 f"{v['total_hits']}/{v['windows']}.")
        st.dataframe(pd.DataFrame(fc["leaderboard"]), width="stretch", hide_index=True)
        st.caption(fc["caveat"])


def _range_warning(v: dict) -> None:
    """Warn when a displayed range is not validated or its check is far from the 80% target."""
    if not v.get("windows"):
        st.warning("This range is NOT checked yet: there is not enough past data to test it.")
    elif not v.get("assessable"):
        st.warning(f"This range was checked on only {v['windows']} past periods, so treat it as rough.")
    elif v["total_coverage"] < 0.65:
        st.warning(f"This range may be TOO NARROW: only {v['total_coverage']:.0%} of past totals fell inside.")


def product_detail(ctx, name: str, table: pd.DataFrame) -> None:
    from decisiondesk.decision.product_scenario import baseline
    from decisiondesk.products import BOM, INGREDIENTS, PRODUCT_IDS
    pid = next(k for k, v in views.NAMES.items() if v == name)
    i = PRODUCT_IDS.index(pid)
    row = table.set_index("product").loc[name]
    b28 = baseline(ctx, 28, pid)
    so = views.stockout_date(b28, pid)
    with st.container(key="panel_pd"):
        st.markdown(f"## {name}")
        m = st.columns(4)
        m[0].metric("Price", T.money(row["price"], 2))
        m[1].metric("Ingredients per order", T.money(row["unit_cost"], 2))
        m[2].metric("Expected orders next week", f"{row['forecast_demand_7d']:,.0f}")
        m[3].metric("Stock-out date", so[5:] if so else "None expected",
                    help="First day when, in at least half of our test runs, there is not enough stock.")
        m2 = st.columns(4)
        m2[0].metric("Available stock (portions)", f"{row['sellable_units']:,.0f}",
                     f"{row['days_of_supply']:.1f} days", delta_color="off", delta_arrow="off")
        s_ = ctx.history.sales
        ps = s_[s_["product_id"] == pid].tail(120)
        st.altair_chart(charts.demand_supply(views.demand_supply_frame(b28, pid),
                                             title=f"{name}: expected orders vs what you can make, next 4 weeks"),
                        width="stretch")
        st.markdown('<div class="soft">Blue: orders we expect. Orange: what your stock (plus Monday and Thursday '
                    'deliveries) lets you make. Red marks: days it might run short.</div>', unsafe_allow_html=True)
        with st.expander("Past sales, price history and ingredients"):
            st.altair_chart(charts.sales_demand_history(ps[["date", "units_sold", "demand_est"]]), width="stretch")
            st.caption(f"{int(ps['stockout'].sum())} sold-out day(s) in the last 120 days. On those days we estimate "
                       "how many people wanted it from the time it sold out.")
            st.altair_chart(charts.price_history(s_[s_["product_id"] == pid][["date", "price", "unit_cost"]]),
                            width="stretch")
            use = pd.DataFrame({"ingredient": [k.name for k in INGREDIENTS], "per order": BOM[i],
                                "next 7 days": BOM[i] * row["forecast_demand_7d"], "unit": [k.unit for k in INGREDIENTS]})
            st.dataframe(use[use["per order"] > 0].round(2), hide_index=True, width="stretch")
            bd = ctx.boards[pid]
            st.caption(f"Forecast method: {bd['selected_model']}. Typical error on unseen days {bd['test_mae']:.1f} "
                       f"orders/day (vs {bd['seasonal_naive_test_mae']:.1f} for 'same as last week'). Demo data.")
        st.button(f"Try a change to {name.lower()} →", key="go_pd_lab", on_click=go,
                  args=("Decision Lab",), kwargs=lab_link(name))


# ================================================================================ LOCAL MARKET
def page_local_market() -> None:
    from decisiondesk import market as mk
    st.markdown("# Local Market")
    ctx = product_ctx()
    if ctx is None:
        return empty_state("Local price comparison needs your item prices.")
    if "market" not in st.session_state:
        st.session_state["market"] = mk.load_market()
    m = st.session_state["market"]
    today = date.today()
    fr = mk.freshness(m, today)
    names = list(views.NAMES.values())
    c1, _ = st.columns([1, 2])
    name = c1.selectbox("Item", names, key="lm_product")
    pid = next(k for k, v in views.NAMES.items() if v == name)
    concepts = sorted(m.prices["concept"].unique())
    with st.expander("Filters"):
        types = st.multiselect("Restaurant types", concepts, default=concepts, key="lm_types")
        similar = st.toggle("Include similar servings", key="lm_similar",
                            help="Also count items whose portion or tier may differ (e.g. a smaller taquito).")
    from decisiondesk.products import PRODUCT_IDS
    our = float(ctx.prices[PRODUCT_IDS.index(pid)])
    c = mk.compare(pid, our, market=m, concepts=types or None, include_similar=similar, as_of=today)
    with st.container(key="card_market_summary"):
        if c["sufficient"]:
            pos = {"within the comparable range": "in the usual range",
                   "below the comparable range": "below everyone we checked",
                   "above the comparable range": "above everyone we checked"}[c["position"]]
            st.markdown(f'<div class="big-line">Your {T.esc(c["our_serving"])}: ${our:.2f}</div>'
                        f'<div class="plain">{c["peer_count"]} nearby restaurants charge ${c["peer_min"]:.2f} to '
                        f'${c["peer_max"]:.2f} for the same thing (middle price ${c["peer_median"]:.2f}). '
                        f'Your price is {pos}.</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="big-line">Your {T.esc(c["our_serving"])}: ${our:.2f}</div>'
                        f'<div class="plain">We found only {c["peer_count"]} restaurant(s) selling the same thing, '
                        'not enough to compare fairly.</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="soft">{T.esc(mk.NOT_A_RECOMMENDATION)}</div>', unsafe_allow_html=True)
        how(f"Prices read by hand from {fr['restaurants_with_verified_prices']} Austin restaurants' own menus "
            f"(of {fr['restaurants_researched']} researched), checked {fr['oldest_verification']}. Single tacos are "
            "converted to a plate of three. Public data, kept apart from your records; it never feeds your "
            "forecasts or profit.")
    if fr["stale"]:
        st.warning(f"These prices are {fr['oldest_age_days']} days old: re-check them before relying on them.")
    for w in c["warnings"]:
        if "Small sample" not in w:
            st.warning(w)
    if c["comparables"]:
        df = pd.DataFrame(c["comparables"])
        with st.container(key="panel_market"):
            st.altair_chart(charts.price_position(df, our, c.get("peer_median"), label=c["our_serving"]),
                            width="stretch")
            st.markdown('<div class="soft">Each dot is one restaurant. Black line: your price. Grey dashed: the middle price.</div>',
                        unsafe_allow_html=True)
        with st.expander("See the restaurants and sources"):
            st.dataframe(df[["restaurant", "item", "listed_price", "comparable_price", "source_url", "verified_date"]],
                         hide_index=True, width="stretch",
                         column_config={"listed_price": st.column_config.NumberColumn("Menu price", format="$%.2f"),
                                        "comparable_price": st.column_config.NumberColumn("For your serving", format="$%.2f"),
                                        "source_url": st.column_config.LinkColumn("Official menu"),
                                        "verified_date": "Checked"})
            if c["excluded"]:
                st.caption(f"Not counted ({len(c['excluded'])}): different servings or other items.")
    st.markdown('<div class="section-title">Want to test a price change?</div>', unsafe_allow_html=True)
    st.markdown('<div class="soft">The Decision Lab shows what might happen to orders and profit. Other '
                'restaurants\' prices do not tell you how your customers will react.</div>', unsafe_allow_html=True)
    b1, b2, b3 = st.columns(3)
    options = [("Lower 5%", -5.0), ("Raise 5%", 5.0)]
    if c["sufficient"] and abs(c["difference_vs_median_pct"]) <= 30:
        to_med = round((c["peer_median"] / our - 1) * 100, 1)
        options.append((f"Match the middle price ({to_med:+.1f}%)", to_med))
    for col, (lab_, v) in zip((b1, b2, b3), options):
        col.button(f"{lab_} →", key=f"lm_go_{lab_[:5]}", on_click=go, args=("Decision Lab",),
                   kwargs=lab_link(name, sl_price=float(v)))
    with st.expander("All five items and how we collect prices"):
        ov = mk.overview({p: float(ctx.prices[i]) for i, p in enumerate(PRODUCT_IDS)}, market=m,
                         as_of=today, concepts=types or None, include_similar=similar)
        ov.insert(0, "product", ov["product_id"].map(views.NAMES))
        for col in ("peer_min", "peer_median", "peer_max", "vs_median_pct"):
            ov[col] = pd.to_numeric(ov[col], errors="coerce")
        st.dataframe(ov.drop(columns=["product_id"]), hide_index=True, width="stretch",
                     column_config={k: st.column_config.NumberColumn(format="$%.2f")
                                    for k in ("our_price", "peer_min", "peer_median", "peer_max")})
        st.markdown(f"- {fr['method']}\n- Next re-check due: **{fr['next_reverification_due']}**\n"
                    "- No live prices: ordering platforms that block automated access are not scraped.")
        if len(m.log):
            st.dataframe(m.log, hide_index=True, width="stretch")


# ================================================================================ DECISION LAB
def _mode_changed() -> None:
    keep = MODE_KEYS[st.session_state["seg_sl_mode"]]
    for k in LAB_KEYS:
        if k not in keep:
            st.session_state[k] = 0.0


def _apply_link_params() -> None:
    """Shareable links, e.g. ?page=Decision%20Lab&product=Tacos&price=10&cost=8 (first load only)."""
    if st.session_state.get("lab_link_applied"):
        return
    st.session_state["lab_link_applied"] = True
    q = st.query_params
    if q.get("product") in views.NAMES.values():
        st.session_state["sl_product"] = q.get("product")
    found = []
    for name, key, lo, hi in (("price", "sl_price", -30, 30), ("supply", "sl_supply", -100, 100),
                              ("extra", "sl_extra", 0, 5000), ("cost", "sl_cost", -20, 40),
                              ("shock", "sl_shock", -50, 50), ("marketing", "sl_mkt", -100, 100)):
        try:
            if q.get(name) is not None:
                st.session_state[key] = float(min(hi, max(lo, float(q.get(name)))))
                found.append(key)
        except ValueError:
            pass
    modes = [m for m in MODES[:-1] if set(found) & set(MODE_KEYS[m])]
    if modes:
        st.session_state["seg_sl_mode"] = modes[0] if len(modes) == 1 else "Combine changes"
    if q.get("h") in ("7", "14", "28"):
        st.session_state["seg_sl_h"] = int(q.get("h"))


def page_decision_lab() -> None:
    st.markdown("# Decision Lab")
    st.markdown('<div class="greeting-sub">Try a change before you make it. We show what would likely happen '
                'to your sales, profit and stock.</div>', unsafe_allow_html=True)
    ctx = product_ctx()
    if ctx is None:
        empty_state("Item-level what-ifs are not available for this business.")
    else:
        lab(ctx)
    with st.expander("Whole-business options (a separate, business-level model)", expanded=ctx is None):
        business_wide_actions()


def lab(ctx) -> None:
    from decisiondesk.decision.product_scenario import (
        ELASTICITY,
        MARKETING_RESPONSE,
        SUBSTITUTION,
        ProductScenario,
        baseline,
        compare,
        recommend,
        simulate,
    )
    from decisiondesk.products import PRODUCT_IDS
    _apply_link_params()
    for k in LAB_KEYS:
        st.session_state.setdefault(k, 0.0)
    st.markdown('<div class="section-title">1. What do you want to try?</div>', unsafe_allow_html=True)
    mode = seg("Kind of change", MODES, "seg_sl_mode", "Change a price", on_change=_mode_changed)
    show = MODE_KEYS[mode]
    with st.container(key="panel_controls"):
        left, right = st.columns([1, 1.6], gap="large")
        with left:
            name = st.selectbox("Which item?", list(views.NAMES.values()), key="sl_product")
            pid = next(k for k, v in views.NAMES.items() if v == name)
            i = PRODUCT_IDS.index(pid)
            h = seg("Look ahead", [7, 14, 28], "seg_sl_h", 28, lambda d: {7: "1 week", 14: "2 weeks", 28: "4 weeks"}[d])
            st.button("Reset", key="sl_reset", on_click=_reset_controls)
        with right:
            cap = (ctx.max_price_increase or 0.05) * 100
            if "sl_price" in show:
                st.slider(f"Change the price of {name.lower()} by (%)", -30.0, 30.0, step=1.0, key="sl_price")
                p = st.session_state["sl_price"]
                new_price = round(ctx.prices[i] * (1 + p / 100), 2)
                tag = ('<span class="chip down">above your limit</span>' if p > cap + 1e-9
                       else '<span class="chip up">within your limit</span>' if p else "")
                st.markdown(f'<div class="plain">{T.money(ctx.prices[i], 2)} → <b>{T.money(new_price, 2)}</b> {tag}</div>'
                            f'<div class="soft">Your own limit: raise prices by at most {cap:g}% at a time.</div>',
                            unsafe_allow_html=True)
            if "sl_supply" in show:
                st.slider(f"Change your regular {name.lower()} ingredient orders by (%)", -100.0, 100.0, step=5.0,
                          key="sl_supply", help="Changes this item's share of every Monday and Thursday order.")
                fc_units = float(ctx.bundle.targets[f"demand_{pid}"].point[:h].clip(0).sum())
                st.markdown(f'<div class="soft">About {st.session_state["sl_supply"] / 100 * 1.35 * fc_units:+,.0f} '
                            f'portions over the {weeks(h)} (we expect {fc_units:,.0f} orders).</div>',
                            unsafe_allow_html=True)
            if "sl_extra" in show:
                st.number_input("Or buy extra once (portions)", 0.0, 5000.0, step=25.0, key="sl_extra",
                                help="Bought today, arrives tomorrow.")
                st.markdown(f'<div class="soft">Costs {T.money(st.session_state["sl_extra"] * ctx.unit_cost[i])} '
                            'at today\'s ingredient prices.</div>', unsafe_allow_html=True)
            if "sl_cost" in show:
                st.slider("Supplier ingredient prices change by (%)", -20.0, 40.0, step=1.0, key="sl_cost",
                          help="What your suppliers charge for ALL ingredients. Your menu prices stay the same.")
            if "sl_shock" in show:
                st.slider("Customers: busier (+) or quieter (−) by (%)", -50.0, 50.0, step=5.0, key="sl_shock",
                          help="For example bad weather, a local event or road works.")
                st.radio("Applies to", ["This item", "All items"], horizontal=True, key="sl_scope")
            if "sl_mkt" in show:
                st.slider("Marketing & software spending change (%)", -100.0, 100.0, step=10.0, key="sl_mkt")
                st.markdown(f'<div class="soft">Now {T.money(ctx.marketing_monthly)}/month → '
                            f'{T.money(ctx.marketing_monthly * (1 + st.session_state["sl_mkt"] / 100))}/month.</div>',
                            unsafe_allow_html=True)
    scope = st.session_state.get("sl_scope", "This item")
    sc = ProductScenario(pid, float(st.session_state["sl_price"]), float(st.session_state["sl_supply"]),
                         float(st.session_state["sl_extra"]), float(st.session_state["sl_shock"]),
                         "all" if scope == "All items" else "product",
                         float(st.session_state["sl_mkt"]), int(h), float(st.session_state["sl_cost"]))
    s.state.product_scenario = asdict(sc)
    base = baseline(ctx, h, pid)
    scen = simulate(ctx, sc)
    cmp_ = compare(ctx, scen, base)

    st.markdown('<div class="section-title">2. What would likely happen</div>', unsafe_allow_html=True)
    with st.container(key="card_result"):
        if sc.is_baseline:
            st.markdown('<div class="plain">Move a slider above to see what would change. Right now this is your '
                        f'current plan for the {weeks(h)}.</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="soft">{T.esc(sc.describe()).capitalize()}, {weeks(h)} '
                        f'({cmp_["forecast_start"]} to {cmp_["forecast_end"]}), compared with changing nothing</div>',
                        unsafe_allow_html=True)
            if not cmp_["within_policy"]:
                st.markdown('<div class="callout bad"><b>Outside the owner\'s approved limits:</b> '
                            + T.esc("; ".join(cmp_["policy_violations"])) + ". You can explore it, but we "
                            "won't suggest it.</div>", unsafe_allow_html=True)
            lo, hi = cmp_["profit"]["change_90pct_range"]
            impact_block(cmp_["profit"]["change"], lo, hi, cmp_["probability_profit_improves"], h)
        sr, u = cmp_["stockout_risk"], cmp_["units_sold"]
        tiles = [("Sales", T.money(cmp_["revenue"]["baseline"]), T.money(cmp_["revenue"]["scenario"]),
                  T.chip(cmp_["revenue"]["change_pct"])),
                 ("Profit", T.money(cmp_["profit"]["baseline"]), T.money(cmp_["profit"]["scenario"]),
                  T.chip(cmp_["profit"]["change_pct"])),
                 ("Cash", T.money(cmp_["cash_flow"]["baseline"]), T.money(cmp_["cash_flow"]["scenario"]),
                  T.chip(cmp_["cash_flow"]["change_pct"])),
                 (f"{name} sold", f"{u['baseline']:,.0f}", f"{u['scenario']:,.0f}", T.chip(u["change_pct"])),
                 (f"Chance {name.lower()} sells out", f"{sr['baseline']:.0%}", f"{sr['scenario']:.0%}",
                  T.chip((sr["scenario"] - sr["baseline"]) * 100, good_when_up=False, suffix=" pts")
                  if sr["scenario"] != sr["baseline"] else '<span class="chip flat">no change</span>')]
        g = st.columns(5)
        for j, (col, (lab_, b_, n_, ch_)) in enumerate(zip(g, tiles)):
            with col, st.container(key=f"card_cmp_a{j}"):
                if sc.is_baseline:
                    st.markdown(f'<div class="cmp-label">{T.esc(lab_)}</div><div class="cmp-new">{T.esc(b_)}</div>',
                                unsafe_allow_html=True)
                else:
                    st.markdown(T.compare_html(lab_, b_, n_, ch_).replace("plan:", "now:"), unsafe_allow_html=True)

    rec = _cached(("rec", repr(sc)), lambda: recommend(ctx, sc))
    if not sc.is_baseline:
        from decisiondesk.briefing import decision_checklist
        from decisiondesk.decision.product_scenario import sensitivity
        sens = _cached(("sens", repr(sc)), lambda: sensitivity(ctx, sc))
        lm = _local_market(pid, float(ctx.prices[i])) if sc.price_change_pct else None
        a = decision_checklist(ctx, sc, cmp_, sens, local_market=lm)
        st.markdown('<div class="section-title">3. Before you decide</div>', unsafe_allow_html=True)
        with st.container(key="panel_rec"):
            st.markdown(f'<div class="plain"><b>Why it might help:</b> {T.esc(a["why"])}</div>', unsafe_allow_html=True)
            checklist(a, key="lab_chk")
    best = rec["recommended"]
    if best and not (sc.price_change_pct == best["scenario"]["price_change_pct"]
                     and sc.supply_change_pct == best["scenario"]["supply_change_pct"] and not sc.ingredient_cost_change_pct
                     and not sc.demand_shock_pct and not sc.marketing_change_pct and not sc.extra_units):
        st.markdown(f'<div class="soft">Of the changes we tested for {T.esc(name.lower())}, the best was: '
                    f'<b>{T.esc(best["action"])}</b> (about {T.signed_money(best["profit_change"])} over the {weeks(h)}).</div>',
                    unsafe_allow_html=True)
    e1, e2, _ = st.columns([1.2, 1.2, 3])
    if e1.button("Ask the analyst about this", key="go_analyst"):
        _ask(f"What happens if {name.lower()} prices change {sc.price_change_pct:+g}%?" if sc.price_change_pct
             else f"What if supplier costs rise {sc.ingredient_cost_change_pct:g}%?" if sc.ingredient_cost_change_pct
             else f"Should I buy more {name.lower()} ingredients?" if sc.supply_change_pct or sc.extra_units
             else "What should I do?")
        st.rerun()
    if e2.button("Use for iMessage answers", key="lab_publish"):
        _publish()
    if st.session_state.get("lab_published"):
        st.success(st.session_state["lab_published"])

    with st.expander("See the charts and details"):
        tabs = st.tabs(["Sales & profit", "Orders vs stock", "Other items", "All options tested", "Range"])
        with tabs[0]:
            c1, c2 = st.columns(2)
            c1.altair_chart(charts.band_compare(views.band_frame(scen, base, "revenue"), title="Daily sales"),
                            width="stretch")
            c2.altair_chart(charts.band_compare(views.band_frame(scen, base, "profit"), title="Daily profit"),
                            width="stretch")
            st.caption("Lines: average of the test runs; shaded: the middle 80% of runs. Grey dashed = changing nothing.")
        with tabs[1]:
            st.altair_chart(charts.demand_supply(views.demand_supply_frame(scen, pid),
                                                 title=f"{name}: orders vs what you can make, with your change"),
                            width="stretch")
            st.caption("When expected orders (blue) are above what stock allows (orange), some customers pick "
                       "another dish and some leave.")
        with tabs[2]:
            st.altair_chart(charts.impact_bars(views.impact_frame(cmp_)), width="stretch")
            st.caption("Shared ingredients and customers switching dishes mean one item's change moves the others.")
        with tabs[3]:
            st.markdown(rec["text"])
            if rec["ranked"]:
                st.dataframe(pd.DataFrame([{
                    "option": r["action"], "profit change": T.signed_money(r["profit_change"]),
                    "could be": f"{T.money(r['profit_change_90pct_range'][0])} to {T.money(r['profit_change_90pct_range'][1])}",
                    "came out ahead": tries(r["probability_profit_improves"])} for r in rec["ranked"]]),
                    hide_index=True, width="stretch")
            for r in rec["outside_policy"]:
                st.caption(f"Not suggested: {r['action']} — {'; '.join(r['policy_violations'])}.")
        with tabs[4]:
            d = scen.profit.sum(1) - base.profit.sum(1)
            lo, hi = cmp_["profit"]["change_90pct_range"]
            st.altair_chart(charts.delta_histogram(d, lo, hi), width="stretch")
            rv = cmp_["range_validation"]
            st.caption(cmp_["interval_meaning"] + f" Checked on past data: revenue "
                       f"{rv['revenue_total']['total_hits']}/{rv['revenue_total']['windows']}, profit "
                       f"{rv['profit_total']['total_hits']}/{rv['profit_total']['windows']} past periods inside "
                       "their 80% range. " + rv["note"])
    with st.expander("Assumptions and risks"):
        st.markdown("How customers react to a price change, how many switch to another dish, and how much "
                    "marketing brings in are **assumptions**, not measured from your sales. Every test run "
                    "picks a value from the ranges below, so the 'could be anywhere from' range includes them.")
        st.dataframe(pd.DataFrame([{k: a_.get(k) for k in ("name", "value", "low", "high", "source", "note")}
                                   for a_ in [asdict(ELASTICITY[pid]), asdict(SUBSTITUTION), asdict(MARKETING_RESPONSE)]]),
                     hide_index=True, width="stretch")
        if rec["sensitivity"]:
            st.markdown("**If an assumption is at its edge** (best tested option):\n" + "\n".join(
                f"- {x['assumption'].replace('_', ' ')} at its {x['level']} bound ({x['value']}): profit change "
                f"{T.signed_money(x['profit_change'])}" for x in rec["sensitivity"]))
        st.markdown("**Other limits:** forecasts were tested on demo history only; supplier prices change every "
                    "ingredient by the same percent; staff capacity and competitors' reactions are not modelled.")
        st.caption(f"{ctx.n_sims:,} test runs, seed {cmp_['seed']}, the same runs for every option. Data: SYNTHETIC.")


def _reset_controls() -> None:
    for k in LAB_KEYS:
        st.session_state[k] = 0.0


def _cached(key, fn):
    cache = st.session_state.setdefault("lab_cache", {})
    if key not in cache:
        if len(cache) > 20:
            cache.clear()
        cache[key] = fn()
    return cache[key]


def _local_market(pid: str, price: float) -> dict | None:
    from decisiondesk import market as mk
    try:
        if "market" not in st.session_state:
            st.session_state["market"] = mk.load_market()
        return mk.compare(pid, price, market=st.session_state["market"], as_of=date.today())
    except (FileNotFoundError, ValueError):
        return None


def _publish() -> None:
    from decisiondesk.messaging.shared import ScenarioStore
    rec = ScenarioStore().publish(st.session_state.get("business_key", "restaurant"), asdict(s.state))
    st.session_state["lab_published"] = (f"Published scenario v{rec['version']}: {rec['summary']}. "
                                         "iMessage replies use it from your next message.")


def business_wide_actions() -> None:
    st.caption("Changes here are hypothetical; action effects are modelled assumptions, not proven causal "
               "effects. This model forecasts the whole business at once, so its 'current plan' profit "
               "differs by a few percent from the product-level plan above: two models of the same future.")
    if s.industry == "restaurant":
        c1, c2, c4, c5 = st.columns(4)
        food = c1.slider("Supplier / food cost change (%)", -10.0, 30.0, float(s.state.food_cost_change_pct), 1.0,
                         key="d_food", help="A business-specific cost shock: what your suppliers charge.")
        cash_in = c5.number_input("Starting cash (USD, 0 = ledger balance)", 0.0, 1e7,
                                  float(s.state.starting_cash or 0), 500.0, key="d_cash",
                                  help="A hypothetical cash position, e.g. after an unexpected repair bill.")
        el = c2.slider("Price sensitivity range", -2.5, -0.1,
                       (float(s.state.elasticity_low), float(s.state.elasticity_high)), 0.1, key="d_el")
        mpi = c4.number_input("Max price increase", 0.0, 0.3, float(s.state.max_price_increase or 0.05), 0.01, key="d_mpi")
        if st.button("Compare options", key="d_run"):
            with st.spinner("Simulating actions..."):
                changed = (el[0], el[1]) != (s.state.elasticity_low, s.state.elasticity_high)
                st.session_state["last_decision"] = run_tool(s, "evaluate_restaurant_actions", {
                    "food_cost_change_pct": food,
                    "elasticity_mode": round((el[0] + el[1]) / 2, 2) if changed else None,
                    "elasticity_low": el[0] if changed else None,
                    "elasticity_high": el[1] if changed else None, "max_price_increase": mpi,
                    "starting_cash": cash_in or None, "objective": "max_expected_profit"})
    else:
        c1, c2, c4 = st.columns(3)
        budget = c1.number_input("28-day purchase budget (USD, 0 = none)", 0.0, 1e7,
                                 float(s.state.purchase_budget_usd or 0), 1000.0, key="d_budget")
        storage = c2.number_input("Storage capacity (units)", 0.0, 1e6, float(s.state.storage_units or 0), 100.0, key="d_storage")
        tol = c4.slider("Stock-out tolerance after first delivery", 0.0, 1.0,
                        float(s.state.max_stockout_probability or 0.25), 0.05, key="d_tol")
        if st.button("Compare options", key="d_run"):
            with st.spinner("Simulating inventory policies..."):
                st.session_state["last_decision"] = run_tool(s, "evaluate_inventory_policies", {
                    "purchase_budget_usd": budget or None, "storage_units": storage or None,
                    "max_stockout_probability": tol, "objective": "max_expected_profit"})
    out = st.session_state.get("last_decision")
    if out and out.get("supported", True):
        rec = out["recommended_action"]
        (st.success if rec else st.warning)(
            f"**{'Best tested option: ' + rec if rec else 'No option beats the current plan'}** · strength: "
            f"{out['strength']} · objective: {out['objective']}")
        st.write(out["rationale"])
        st.dataframe(pd.DataFrame(out["actions"]), width="stretch", hide_index=True)
        with st.expander("Assumptions, constraints, sensitivity and limitations"):
            st.json({"scenario": out["scenario"], "assumptions": out["assumptions"],
                     "constraints": out["constraints"], "sensitivity": out["sensitivity"],
                     "limitations": out["limitations"]})
        st.caption("Your approval is required for any action; nothing is executed automatically.")
    elif out:
        st.error(out.get("reason"))


# ================================================================================ SETTINGS
def page_settings() -> None:
    top = st.columns([5, 1.2], vertical_alignment="center")
    top[0].markdown("# Settings")
    top[1].button(f"← Back to {page}", key="settings_close", on_click=close_settings, width="stretch")
    section = seg("Settings section", SETTINGS, "seg_settings", "Notifications")
    {"Notifications": notifications_section, "Data": data_section,
     "Advanced diagnostics": diagnostics_section}[section]()


def notifications_section() -> None:
    from decisiondesk.briefing import briefing_sms
    from decisiondesk.messaging.gateway import PhotonGateway, PhotonUnavailable
    from decisiondesk.messaging.risk_signals import scan
    from decisiondesk.messaging.service import (
        ALERT_KINDS,
        MessagingService,
        MessagingStore,
        SimulatorGateway,
    )
    from decisiondesk.messaging.shared import PollerLock, describe_holder

    st.markdown("### Notifications (iMessage)")
    st.caption("Get your briefing and important alerts on your phone, and text questions to your analyst. "
               "Nothing is sent unless you opt in or click send; reply STOP at any time.")
    agent: DecisionAgent = st.session_state["agent"]
    bkey = st.session_state.get("business_key", "restaurant")
    mode = st.radio("Channel", ["Photon iMessage (live)", "Local simulator (no real messages)"],
                    horizontal=True, key="msg_mode")
    live = mode.startswith("Photon")
    skey = f"msg_svc_{'live' if live else 'sim'}"
    if skey not in st.session_state:
        gw = PhotonGateway() if live else SimulatorGateway()
        store = MessagingStore() if live else MessagingStore(
            os.getenv("MSRD_MESSAGING_SIM_STATE") or "data/cache/messaging_sim.json")
        st.session_state[skey] = MessagingService(
            gw, store, lambda key: s if key == bkey else BusinessSession.from_demo(key, models=MODELS))
        st.session_state[f"{skey}_lock"] = PollerLock("dashboard")
    svc: MessagingService = st.session_state[skey]
    lock: PollerLock = st.session_state[f"{skey}_lock"]
    holder = lock.holder() if live else None
    viewer = bool(live and holder and holder.get("token") != lock.token)
    if viewer:
        svc.store.reload()
    st.markdown(f"**Channel: {svc.channel_label}** · replies written by: {agent.label}")
    if live:
        st.caption("Message poller: " + describe_holder(holder))
    if viewer:
        st.warning(f"Another process is answering messages ({describe_holder(holder)}). This page is read-only "
                   "to avoid two pollers; stop that process to manage messaging here.")
    try:
        if st.button("Refresh connection status", key="msg_status") or f"{skey}_status" not in st.session_state:
            st.session_state[f"{skey}_status"] = svc.gateway.status()
            if not viewer:
                svc.sync_recipients()
        status = st.session_state[f"{skey}_status"]
    except PhotonUnavailable as exc:
        status = {"connected": False, "problems": [str(exc)], "recipients": [], "sms_lines": []}
    c1, c2, c3 = st.columns(3)
    c1.metric("Photon connection", "connected" if status.get("connected") else "not connected")
    c2.metric("iMessage line", (status.get("imessage") or {}).get("mode", "—"))
    c3.metric("SMS lines", len(status.get("sms_lines", [])))
    for prob in status.get("problems", []):
        st.error(prob)
    owners = [o for o in svc.store.owners.values() if o.authorized]
    if not owners:
        st.warning("No verified phone yet. In the Photon dashboard (iMessage page), add your phone as the "
                   "sender, then text the assigned number from it. You can then ask questions by iMessage.")
    kinds = [k for k in ALERT_KINDS if k not in ("economic_release", "daily_briefing")]
    for o in owners:
        o.business_key = bkey
        with st.container(key=f"panel_owner_{abs(hash(o.handle)) % 10**8}_{skey}"):
            st.markdown(f"#### Phone {o.masked}")
            st.caption(f"Conversation started from phone: {'yes' if o.space_id else 'no — text the agent line first'}"
                       f" · last message {o.last_inbound_at or '—'} · consent {o.consent_source or 'none'} {o.consent_at or ''}")
            if o.stopped:
                st.warning("This phone replied STOP; nothing will be sent until it replies START.")
            opt = st.checkbox("Send me important alerts (explicit opt-in)", value=o.alerts_opt_in and not o.stopped,
                              key=f"optin_{o.handle}_{skey}", disabled=viewer)
            if not viewer and opt != (o.alerts_opt_in and not o.stopped):
                svc.set_alert_consent(o.handle, opt, "dashboard")
            chosen = st.multiselect("Alert types", kinds, default=[k for k in o.alert_prefs if k in kinds],
                                    key=f"prefs_{o.handle}_{skey}", disabled=viewer)
            daily = st.checkbox("Morning briefing by iMessage (sent when the scheduled job runs)",
                                value="daily_briefing" in o.alert_prefs, key=f"daily_{o.handle}_{skey}",
                                disabled=viewer or not opt,
                                help="Needs the alert opt-in. Sent at most once a day by "
                                     "`python -m decisiondesk.messaging.worker briefing --send` (cron).")
            o.alert_prefs = chosen + (["daily_briefing"] if daily and opt else [])
            confirm = st.checkbox("I confirm this is my own authorized phone", key=f"conf_{o.handle}_{skey}",
                                  disabled=viewer)
            b1, b2, b3 = st.columns(3)
            if b1.button("Send today's briefing to my phone", key=f"brief_{o.handle}_{skey}",
                         disabled=not confirm or viewer or o.stopped, type="primary"):
                try:
                    rec = svc.send_briefing(o.handle, b, confirmed=True)
                    if rec is None:
                        st.warning("Not sent (blocked by the number check or STOP).")
                    else:
                        st.success(f"Submitted to Photon: status {rec['status']} (delivery not yet confirmed)"
                                   if not svc.simulated else "Simulated message stored (not sent to a phone).")
                except (PermissionError, PhotonUnavailable) as exc:
                    st.error(str(exc))
            if b2.button("Send a test message", key=f"test_{o.handle}_{skey}", disabled=not confirm or viewer):
                try:
                    rec = svc.send_test(o.handle)
                    st.success(f"Submitted to Photon: status {rec['status']} (delivery not yet confirmed)"
                               if not svc.simulated else "Simulated message stored (not sent to a phone).")
                except (PermissionError, PhotonUnavailable) as exc:
                    st.error(str(exc))
            if b3.button("Check risks and send opted-in alerts", key=f"scan_{o.handle}_{skey}", disabled=viewer):
                with st.spinner("Checking with the forecasting and decision tools..."):
                    sigs = scan(svc.agent_for(o).session)
                st.session_state[f"signals_{skey}"] = [(x.kind, x.title, x.text) for x in sigs]
                for sig in sigs:
                    try:
                        rec = svc.send_alert(o.handle, sig)
                    except (PermissionError, PhotonUnavailable) as exc:
                        st.error(f"{sig.kind}: {exc}")
                        continue
                    st.write(f"{sig.title}: {'sent (' + rec['status'] + ')' if rec else 'not sent (consent, preference or already sent)'}")
            with st.expander("Preview the briefing text message"):
                st.code(briefing_sms(b), language="text")
            if svc.simulated:
                sim_text = st.text_input("Simulate a text from the owner", "What should I do?", key=f"sim_{skey}")
                if st.button("Send simulated text", key=f"simgo_{skey}"):
                    svc.gateway.simulate_inbound(sim_text, o.handle)
                    with st.spinner("Analyst running tools..."):
                        svc.poll_once()
    if signals := st.session_state.get(f"signals_{skey}"):
        with st.expander(f"Latest risk check ({len(signals)} signal(s))", expanded=True):
            for kind, title, text in signals:
                st.write(f"**{title}** ({kind}): {text}")

    src = svc.scenario_source(bkey)
    with st.container(key="panel_msg_scn"):
        st.markdown("#### Scenario used for iMessage answers")
        st.markdown(f"**{'Published v' + str(src['version']) if src['version'] else 'Default'}:** "
                    f"{T.esc(src['summary'])}" + (f" · published {src['published_at']}" if src["published_at"] else ""))
        st.caption("The worker and the dashboard read the same published scenario. Publish from the Decision "
                   "Lab, or publish the dashboard's current scenario here.")
        c1, c2 = st.columns(2)
        if c1.button("Publish the dashboard's current scenario", key="msg_publish"):
            _publish()
            st.rerun()
        if c2.button("Reset to the default scenario", key="msg_unpublish", disabled=not src["version"]):
            from decisiondesk.messaging.shared import ScenarioStore
            ScenarioStore().clear(bkey)
            st.rerun()

    auto = st.toggle("Check for replies every 5 seconds", key=f"auto_{skey}", disabled=not owners or viewer)
    if live and auto and not viewer and not lock.acquire():
        st.error("Could not take the message-poller lock: " + describe_holder(lock.holder()))
        auto = False
    if live and not auto and lock.held:
        lock.release()

    @st.fragment(run_every="5s" if auto else None)
    def conversation():
        if auto:
            if live:
                lock.heartbeat()
            fut = st.session_state.get(f"{skey}_poll")
            if fut is None or fut.done():  # poll in the background: a Gemini reply can take minutes
                if fut is not None and fut.exception():
                    st.error(str(fut.exception()))

                def _poll():
                    svc.poll_once()
                    svc.refresh_statuses()
                st.session_state[f"{skey}_poll"] = _executor().submit(_poll)
            else:
                st.caption("Answering a message in the background…")
        elif viewer:
            svc.store.reload()
        for o in [x for x in svc.store.owners.values() if x.authorized]:
            st.markdown(f"**Conversation with {o.masked}**")
            for m in svc.store.conversations.get(o.handle, [])[-12:]:
                who = "assistant" if m["direction"] == "out" else "user"
                with st.chat_message(who):
                    meta = f" · {m.get('kind', '')} · {m.get('status', '')}" if who == "assistant" else ""
                    st.markdown(f"{m['text']}\n\n_{m['at']}{meta}_")
        if svc.store.outbound:
            st.markdown("**Message history** (delivery is shown only when confirmed by Photon)")
            st.dataframe(pd.DataFrame([{k: r.get(k) for k in ("created_at", "kind", "to", "status",
                                                               "delivered_at", "acceptance", "simulated", "failure")}
                                       | {"scenario": (r.get("evidence") or {}).get("scenario"),
                                          "model": (r.get("evidence") or {}).get("model")}
                                       for r in reversed(svc.store.outbound[-20:])]), width="stretch", hide_index=True)
        if svc.store.events:
            with st.expander("Messaging events and errors"):
                for e in reversed(svc.store.events[-15:]):
                    st.caption(f"{e['at']} {e['kind']}: {e['detail']}")
        st.caption(f"Messages today: {svc.store.sends_today()} / {svc.daily_limit} (daily cap).")

    conversation()


def data_section() -> None:
    st.markdown("### Your data")
    st.caption("Switch business or upload your own daily file in the sidebar (the › arrow at the top left). "
               "Uploaded files stay on this computer (data/private, never committed).")
    st.table(pd.DataFrame([
        {"source": "Business records (Lone Star Taqueria / Maple Street Hardware)", "origin": "SYNTHETIC",
         "used for": "every business number, product table, forecast and scenario"},
        {"source": "Product layer (5 products, recipes, ingredient ledger)", "origin": "SYNTHETIC (derived)",
         "used for": "products, inventory, Decision Lab"},
        {"source": "Austin menu prices (6 restaurants)", "origin": "PUBLIC, manually verified",
         "used for": "Local Market only; never in forecasts or profit"},
        {"source": "Capital One Nessie", "origin": "SIMULATED banking sandbox",
         "used for": "banking import demo (Advanced diagnostics); never business evidence"},
        {"source": "Google Gemini", "origin": "AI service", "used for": "explaining tool results in the chat"},
        {"source": "Photon", "origin": "messaging service", "used for": "iMessage briefings, alerts and questions"},
    ]).set_index("source"))
    st.markdown("**Connecting a real business** (planned): item sales from the POS, recipes, inventory counts and "
                "invoices, and a bank or accounting export. See docs/ONBOARDING.md for the file formats and checks.")


def diagnostics_section() -> None:
    from decisiondesk.monitor import MonitorStore, TelegramNotifier, run_checks
    st.markdown("### Advanced diagnostics")
    st.caption("For reviewers: how the models were checked, how wide the ranges really are, and whether the "
               "numbers add up.")
    section = seg("Diagnostics", ["Model validation", "Uncertainty calibration", "Reconciliation",
                                  "Assumptions", "Banking (Nessie)", "Alert rules"], "seg_ev_section", "Model validation")
    if section == "Model validation":
        rows = []
        for t in ("revenue", "variable_costs", "fixed_costs", "net_cash_flow"):
            bd = s.board(t)
            sel = next(m for m in bd.models if m.name == bd.selected)
            rows.append({"target": t, "selected": bd.selected, "validation WAPE": sel.validation["wape"],
                         "test WAPE": sel.holdout["wape"], "test MAE": sel.holdout["mae"],
                         "test 80% coverage": sel.holdout_interval_coverage, "reason": bd.selection_reason})
        st.markdown("#### Business-level forecasts (rolling-origin validation, untouched final test)")
        st.dataframe(pd.DataFrame(rows).round(3), hide_index=True, width="stretch")
        ctx = product_ctx()
        if ctx is not None:
            st.markdown("#### Product demand forecasts (target: estimated demand)")
            st.dataframe(pd.DataFrame([{"product": views.NAMES[p], **bd} for p, bd in ctx.boards.items()])
                         .drop(columns=["models_compared"]).round(3), hide_index=True, width="stretch")
        st.caption("Scores are on SYNTHETIC data: they test the software, not real-world accuracy.")
    elif section == "Uncertainty calibration":
        calibration_section()
    elif section == "Reconciliation":
        ctx = product_ctx()
        if ctx is None:
            return empty_state("No product reconciliation for this dataset.")
        r, sales, led = ctx.history.recon, ctx.history.sales, ctx.history.ledger
        checks = [
            ("Product revenue + other items = total revenue (max daily error)",
             f"${(r['tracked_revenue'] + r['other_items_revenue'] - r['revenue']).abs().max():.2f}"),
            ("Other-items revenue is never negative", str(bool((r["other_items_revenue"] >= 0).all()))),
            ("Product ingredient cost + other food = food cost (max daily error)",
             f"${(r['tracked_cogs'] + r['other_food_cost'] - r['food_cost']).abs().max():.2f}"),
            ("Ingredient stock never negative", str(bool((led["closing"] >= 0).all()))),
            ("Units sold never exceed demand", str(bool((sales["units_sold"] <= sales["demand"]).all()))),
            ("Product-days with a sell-out", f"{int(sales['stockout'].sum())} of {len(sales):,}"),
            ("Mix adjustments needed to reconcile", str(ctx.history.metadata["mix_adjustments"]))]
        st.dataframe(pd.DataFrame(checks, columns=["check", "result"]), hide_index=True, width="stretch")
    elif section == "Assumptions":
        out = run_tool(s, "get_assumptions", {})
        st.json(out["scenario_state"])
        st.markdown("**Business data assumptions**")
        st.markdown("\n".join(f"- {a}" for a in out["business_assumptions"]))
        ctx = product_ctx()
        if ctx is not None:
            st.markdown("**Product layer assumptions**")
            st.markdown("\n".join(f"- {a}" for a in ctx.history.metadata["assumptions"]))
    elif section == "Banking (Nessie)":
        st.caption("Capital One Nessie sandbox: SIMULATED banking records, read-only import. Forecasts stay on "
                   "the business dataset until enough consistent bank history exists.")
        if st.button("Import accounts and transactions", key="bank_go"):
            st.session_state["bank"] = run_tool(s, "get_banking_summary", {})
        bank = st.session_state.get("bank")
        if bank:
            if not bank["available"]:
                st.error(f"Nessie unavailable: {bank['reason']}")
            else:
                st.write(f"**{bank['source']}** · {bank['transactions']} transactions · "
                         f"{bank['duplicates_dropped']} duplicates dropped")
                st.dataframe(pd.DataFrame(bank["accounts"]), width="stretch", hide_index=True)
                st.subheader("Cash inflows and outflows by category")
                st.dataframe(pd.DataFrame(bank["cash_by_category"]), width="stretch", hide_index=True)
                for mb in bank["missing_bills"]:
                    st.warning(f"Missing bill records: {mb}")
                st.error("Not used for forecasting: " + "; ".join(bank["eligibility_reasons"]))
    elif section == "Alert rules":
        store = MonitorStore(s.monitor_path)
        bkey = s.metadata.get("key", s.dataset)
        with st.form("new_rule"):
            thr = st.number_input("Alert when the simulated 28-day cash shortfall frequency reaches (0-1)",
                                  0.0, 1.0, 0.2, key="r_thr")
            tg_ok = TelegramNotifier().configured
            tg = st.checkbox("Also send to Telegram (opt-in)", disabled=not tg_ok, key="r_tg",
                             help=None if tg_ok else "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable")
            if st.form_submit_button("Save rule"):
                try:
                    store.add_rule(bkey, "cash_shortfall", None, thr or None, tg)
                    st.success("Rule saved.")
                except ValueError as exc:
                    st.error(str(exc))
        if st.button("Run checks now", key="r_run"):
            new = run_checks(MonitorStore(s.monitor_path), {bkey: s},
                             notifier=TelegramNotifier() if TelegramNotifier().configured else None)
            st.success(f"{len(new)} new alert(s).")
        store = MonitorStore(s.monitor_path)
        st.dataframe(pd.DataFrame([r.__dict__ for r in store.rules if r.kind != "fred_release"]),
                     width="stretch", hide_index=True)
        for al in reversed(store.alerts[-20:]):
            if al.category != "observed_economic_data":
                st.warning(f"**{al.title}** ({al.created_at}) — {al.message}")
        st.caption(f"Last check: {store.last_run or 'never'}. Duplicate alerts are suppressed. Alerts never "
                   "execute business actions.")


def calibration_section() -> None:
    from decisiondesk.calibration import (
        METHODS,
        cash_validation,
        chronological_windows,
        evaluate,
        lab_errors,
    )
    st.markdown("#### Do the 80% Monte Carlo ranges hold out of sample?")
    st.caption("Each past forecast window is judged only by a range built from EARLIER windows (chronological, "
               "expanding). Daily = share of single days inside their daily range; total = share of windows "
               "whose 7/14/28-day total fell inside. Old = Milestone 5 weekly blocks; new = whole historical "
               "windows (current).")
    key = ("calib", s.dataset, MODELS)
    if st.session_state.get("calib_key") != key:
        targets = {t: chronological_windows(s.board(t))[0] for t in ("revenue", "net_cash_flow")}
        ctx = product_ctx()
        if ctx is not None:
            le = lab_errors(ctx)
            targets["Decision Lab revenue (tracked)"] = le["lab_revenue_tracked"]
            targets["Decision Lab profit (tracked − fixed)"] = le["lab_profit_tracked_minus_fixed"]
            for p_, e in le["products"].items():
                targets[f"demand: {views.NAMES[p_]}"] = e
        rows = []
        for t, e in targets.items():
            r = evaluate(e, methods={"old: weekly blocks": METHODS["weekly_blocks"],
                                     "new: whole windows": METHODS["whole_window"]}, n=2000)
            for m, per_h in r["methods"].items():
                row = {"quantity": t, "method": m}
                for h, v in per_h.items():
                    row[f"{h}-day totals inside"] = f"{v['total_hits']}/{v['windows']}"
                    row[f"{h}-day range width"] = round(v["mean_total_width"])
                row["daily coverage"] = f"{per_h[28]['daily_coverage']:.0%}"
                rows.append(row)
        cash = None
        try:
            cash = {m: cash_validation(s.history, s.board("net_cash_flow"), method=m) for m in ("weekly_blocks", "whole_window")}
        except ValueError:
            pass
        st.session_state["calib"] = (rows, cash)
        st.session_state["calib_key"] = key
    rows, cash = st.session_state["calib"]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if cash:
        st.markdown(f"**Cash flow:** lowest point of cumulative cash flow inside its 80% range: old "
                    f"{cash['weekly_blocks']['running_min_hits']}/{cash['weekly_blocks']['windows']}, new "
                    f"{cash['whole_window']['running_min_hits']}/{cash['whole_window']['windows']}. Cash-shortfall "
                    "*probabilities* are NOT validated (no shortfalls in the history to check against).")
    st.warning("Small samples: about 23 evaluated windows per row, so a coverage estimate is uncertain by roughly "
               "±15 points. New 28-day ranges are conservative (early backtest windows had larger errors). "
               "Synthetic data: this checks the method, not real-world calibration. Details: "
               "docs/MILESTONE_5_1_CALIBRATION.md.")


if st.session_state.get("view") == "settings":
    page_settings()
else:
    {"Overview": page_overview, "My Business": page_my_business, "Decision Lab": page_decision_lab,
     "Local Market": page_local_market}[page]()
floating_chat()
