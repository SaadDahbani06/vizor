"""Main Street Risk Desk — Streamlit interface (Milestone 5.4: simple four-page analyst).

    streamlit run app/streamlit_app.py

Four pages: Overview (what happened, what needs attention, one decision), My Business (sales,
products, inventory, expenses, cash, forecasts), Decision Lab (what if I change something) and
Local Market (verified public menu prices). "Your Financial Analyst" chat is in the sidebar on
every page. Notifications (iMessage) and Advanced diagnostics (validation, calibration,
reconciliation, banking sandbox) live under Settings, outside the main navigation.

Every number on screen comes from the analytics modules (``decisiondesk.dashboard``,
``decisiondesk.briefing``, ``decisiondesk.decision.product_scenario`` and the analyst tools in
``decisiondesk.agent.tools``); this file only lays them out. Synthetic business data, public
market data, model forecasts, simulations and assumptions are labelled wherever they appear.
Nothing is executed automatically: every action needs the owner's approval.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, timedelta
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
MB_SECTIONS = ["Sales & profit", "Products", "Inventory", "Expenses", "Cash & bills", "Forecasts"]
SETTINGS = ["Notifications", "Data", "Advanced diagnostics"]

st.set_page_config(page_title="Main Street Risk Desk", layout="wide", page_icon="🏪",
                   initial_sidebar_state="expanded")
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
    for k in [k for k in st.session_state if k.startswith(("sl_", "lab_", "brief"))]:
        del st.session_state[k]


def _load_demo(key: str) -> None:
    with st.spinner("Loading the business (cached model evaluations load in seconds; run "
                    "`python -m decisiondesk.precompute` once to warm them)..."):
        s = BusinessSession.from_demo(key, models=MODELS, n_sims=1000 if FAST else 2000)
    _reset_lab()
    st.session_state.update(session=s, agent=DecisionAgent(s), chat=[], business_key=key,
                            last_decision=None, pending=None)


# ------------------------------------------------------------------------------ business picker
def business_picker() -> None:
    """Data source. Rendered on every run (inside a sidebar expander) so its state persists."""
    choice = st.radio("Data source", ["Demo restaurant (Austin, TX)", "Demo retailer (Columbus, OH)",
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


with st.sidebar, st.expander("Business & data", expanded=False):
    business_picker()

s = _session()
if s is None:
    st.warning("Choose a demo business or upload and confirm your data in the sidebar.")
    st.stop()

if "nav" not in st.session_state:  # deep links: ?page=Decision%20Lab, ?view=settings&section=...
    st.session_state["nav"] = st.query_params.get("page") if st.query_params.get("page") in PAGES else PAGES[0]
    if st.query_params.get("section") in MB_SECTIONS:
        st.session_state["seg_mb_section"] = st.query_params.get("section")
    if st.query_params.get("view") == "settings":
        open_settings(st.query_params.get("section") if st.query_params.get("section") in SETTINGS
                      else "Notifications")
st.session_state.setdefault("view", None)


def product_ctx():
    try:
        return s.product_context()
    except ProductLayerUnavailable:
        return None


def empty_state(what: str) -> None:
    st.markdown(f'<div class="callout">{T.esc(what)} Product-level analytics need daily records '
                "per product (units sold, price, unit cost and stock or ingredients). The demo "
                "restaurant has them; this dataset has daily totals only.</div>", unsafe_allow_html=True)


def seg(label: str, options: list, key: str, default=None, fmt=None) -> object:
    st.session_state.setdefault(key, default if default is not None else options[0])
    return st.radio(label, options, horizontal=True, key=key, format_func=fmt or str,
                    label_visibility="collapsed")


def how(text: str, label: str = "How was this calculated?") -> None:
    with st.popover(label, width="content"):
        st.markdown(text)


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
    page = st.session_state.get("nav")
    return {"My Business": st.session_state.get("prod_pick"),
            "Decision Lab": st.session_state.get("sl_product"),
            "Local Market": st.session_state.get("lm_product")}.get(page) or (
        "Tacos" if page in ("Decision Lab", "Local Market") else None)


# ------------------------------------------------------------------------------ header
b = _briefing()
hdr = st.columns([6, 1.1, 1.1], vertical_alignment="center")
hdr[0].markdown(T.masthead(s.metadata.get("business_name", s.dataset), s.data_label, str(s.origin.date()),
                           f" · {s.metadata.get('city', '')} {s.metadata.get('state', '')}"
                           + (" · fast mode" if FAST else "")), unsafe_allow_html=True)
with hdr[1].popover(f"🔔 {len(b['alerts'])}", width="stretch"):
    st.markdown("**Notifications**")
    if not b["alerts"]:
        st.caption("Nothing needs attention today.")
    for a in b["alerts"]:
        st.markdown(f'<div class="alert-row"><span class="sev {a["severity"]}">{a["severity"]}</span><div>'
                    f'<div class="alert-title">{T.esc(a["title"])}</div>'
                    f'<div class="alert-detail">{T.esc(a["detail"])}</div></div></div>', unsafe_allow_html=True)
    st.button("Notification settings", key="bell_settings", on_click=open_settings, args=("Notifications",))
hdr[2].button("⚙ Settings", key="open_settings", on_click=open_settings, args=("Notifications",),
              width="stretch")
st.info(f"**{s.metadata.get('business_name')}** · data: **{s.data_label}** · last data date "
        f"{s.origin.date()}. Business records are synthetic unless you uploaded your own; nothing "
        "here is validated on a real business. Nothing is changed without your approval.", icon="🏪")
page = st.radio("Navigate", PAGES, horizontal=True, key="nav", label_visibility="collapsed",
                on_change=close_settings)


# ================================================================================ ANALYST CHAT
TOOL_LABELS = {"get_business_overview": "business overview", "forecast_business": "forecast",
               "evaluate_restaurant_actions": "decision engine", "evaluate_inventory_policies": "inventory engine",
               "assess_cash_risk": "cash simulation", "get_product_performance": "product table",
               "simulate_product_scenario": "Decision Lab simulation", "explain_profit_change": "profit drivers",
               "get_local_market_prices": "local market data", "get_daily_briefing": "daily briefing",
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
    check = ("every number checked against the calculations ✓" if reply.grounded else
             "⚠ some numbers could not be verified")
    meta = f"Calculated with: {', '.join(used)} · {check}" if used else check
    st.session_state["chat"].append({"role": "assistant", "text": reply.text, "meta": meta,
                                     "grounded": reply.grounded})


def analyst_panel() -> None:
    agent: DecisionAgent = st.session_state["agent"]
    model = f" · {agent.gemini.model}" if getattr(agent, "gemini", None) else ""
    sub = {"gemini": "Gemini" + model, "claude": "Claude", "offline": "Offline mode (rule-based, no AI model)"}
    st.markdown('<div class="analyst-head"><div class="analyst-dot">FA</div><div>'
                '<div class="analyst-name">Your Financial Analyst</div>'
                f'<div class="analyst-sub">{T.esc(sub.get(agent.mode, agent.mode))} · explains your numbers, '
                'never makes them up</div></div></div>', unsafe_allow_html=True)
    if getattr(agent, "last_fallback", None):
        st.caption(f"⚠ The AI model was unavailable ({agent.last_fallback}); the last answer came from "
                   "offline mode.")
    pending = st.session_state.get("pending")
    chat = st.session_state["chat"]
    box = st.container(height=440 if chat else 150, border=False, key="chat_box")
    with box:
        if not chat:
            st.caption(f"Ask about your sales, products, costs, cash or a decision. I'm looking at "
                       f"**{page}**" + (f" · **{current_product()}**." if current_product() else ".")
                       + " Follow-ups work: “what about 3% instead?”, “which is safer?”.")
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
    q = st.chat_input("Ask your analyst…", key="chat_in", disabled=bool(pending))
    if q:
        _ask(q)
        st.rerun()
    c1 = st.columns(2)[0]
    if chat and c1.button("Clear chat", key="chat_clear", disabled=bool(pending)):
        st.session_state["chat"] = []
        st.session_state["agent"] = DecisionAgent(s)
        st.rerun()
    st.caption("Answers come from the same calculations as the dashboard. Synthetic demo data; "
               "every action needs your approval.")


with st.sidebar:
    analyst_panel()


# ================================================================================ OVERVIEW
def _bounds(period):
    w = dash.window(s.history, period)
    return w.start, w.end


def page_overview() -> None:
    hour = datetime.now().hour
    hello = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    y = b["yesterday"]
    chg = "n/a" if y["revenue_change_pct"] is None else f"{y['revenue_change_pct']:+.0f}%"
    st.markdown(f'<div class="greeting">{hello}.</div><div class="greeting-sub">Yesterday ({T.esc(b["weekday"])} '
                f'{T.esc(b["date"])}) {T.esc(b["business"])} took {T.money(y["revenue"])} in sales, {chg} vs a typical '
                f'{T.esc(b["weekday"])} ({T.money(y["revenue_typical"])}).</div>', unsafe_allow_html=True)
    briefing_panel()

    top = st.columns([3, 2], vertical_alignment="bottom")
    top[0].markdown("### Your numbers")
    with top[1]:
        period = seg("Period", list(dash.PERIODS), "seg_ov_period", "mtd", lambda k: dash.PERIODS[k])
    k = dash.kpis(s, period)
    st.caption(f"{k['period_label']} compared with {k['comparison_label']}.")
    r1, r2 = st.columns(3), st.columns(3)
    rev, prof, exp, c, inv, li = (k["revenue"], k["net_profit"], k["expenses"], k["cash"], k["inventory"],
                                  k["liabilities"])
    with r1[0], st.container(key="kpi_revenue"):
        st.markdown(T.kpi_html("Revenue", T.money(rev["value"]), chip_html=T.chip(rev["change_pct"]),
                               sub=f"vs {T.money(rev['previous'])}", tag=T.origin("observed")),
                    unsafe_allow_html=True)
        st.altair_chart(charts.sparkline(pd.DataFrame(rev["spark"]).assign(
            date=lambda d: pd.to_datetime(d["date"])), "revenue"), width="stretch")
        how(f"Total sales recorded in the period ({k['period_label'].lower()}), from your daily records, "
            f"compared with {k['comparison_label']}. Demo data is synthetic.")
        st.button("Sales details →", key="go_revenue", on_click=go, args=("My Business",),
                  kwargs={"seg_mb_section": "Sales & profit"})
    with r1[1], st.container(key="kpi_profit"):
        st.markdown(T.kpi_html("Profit (operating)", T.money(prof["value"]), chip_html=T.chip(prof["change_pct"]),
                               sub=f"{T.pct(prof['margin_pct'])} of sales (was {T.pct(prof['previous_margin_pct'])})",
                               tag=T.origin("observed")), unsafe_allow_html=True)
        how(prof["definition"] + " It is before taxes and loan repayments.")
        st.button("What changed profit →", key="go_profit", on_click=go, args=("My Business",),
                  kwargs={"seg_mb_section": "Expenses"})
    with r1[2], st.container(key="kpi_expenses"):
        st.markdown(T.kpi_html("Expenses", T.money(exp["value"]),
                               chip_html=T.chip(exp["change_pct"], good_when_up=False),
                               sub=f"fixed {T.money(exp['fixed'])} · variable {T.money(exp['variable'])}",
                               tag=T.origin("observed")), unsafe_allow_html=True)
        how("All costs recorded in the period (accrual): variable costs (ingredients, packaging, card "
            "fees, hourly labor) move with sales; fixed costs (rent, salaries, insurance, software) "
            "follow the bill schedule.")
        st.button("Expense breakdown →", key="go_expenses", on_click=go, args=("My Business",),
                  kwargs={"seg_mb_section": "Expenses"})
    with r2[0], st.container(key="kpi_cash"):
        run = ("cash flow positive" if c.get("runway_days") is None else f"runway ≈ {c['runway_days']:.0f} days")
        st.markdown(T.kpi_html("Cash on hand", T.money(c["value"]), sub=run, tag=T.origin("observed")),
                    unsafe_allow_html=True)
        how(c["source"] + ". " + c.get("runway_note", ""))
        st.button("Cash & bills →", key="go_cash", on_click=go, args=("My Business",),
                  kwargs={"seg_mb_section": "Cash & bills"})
    with r2[1], st.container(key="kpi_inventory"):
        low = inv.get("low_stock") or []
        warn = (f'<span class="chip warn">low: {T.esc(", ".join(x["ingredient"] for x in low))}</span>'
                if low else '<span class="chip up">no low-stock items</span>')
        st.markdown(T.kpi_html("Inventory health", T.money(inv["value"]), chip_html=warn,
                               sub=" at cost", tag=T.origin("observed")), unsafe_allow_html=True)
        how(inv["basis"].capitalize() + (f". Next delivery in {inv['next_delivery_in_days']} days; an item is "
                                         "'low' when its stock will not last until then at forecast use."
                                         if inv.get("next_delivery_in_days") else "."))
        st.button("Inventory →", key="go_inventory", on_click=go, args=("My Business",),
                  kwargs={"seg_mb_section": "Inventory"})
    with r2[2], st.container(key="kpi_liabilities"):
        if li.get("payables") is None:
            st.markdown(T.kpi_html("Bills due", "Unavailable", small=True), unsafe_allow_html=True)
            st.caption(li["note"])
        else:
            st.markdown(T.kpi_html("Bills due (7 days)", T.money(b.get("bills_due_7d_total", 0.0)),
                                   sub=f"{len(b['bills_due_7d'])} scheduled bills · supplier invoices "
                                       f"{T.money(li['payables'])}", tag=T.origin("observed")),
                        unsafe_allow_html=True)
            how(f"Scheduled bills from your bill history due within 7 days, plus unpaid net-"
                f"{li['payment_terms_days']} supplier invoices. Loan principal is not in the data.")
        st.button("Bills →", key="go_liabilities", on_click=go, args=("My Business",),
                  kwargs={"seg_mb_section": "Cash & bills"})

    st.markdown("### Sales and profit over time")
    win = seg("Window", ["7D", "30D", "90D", "1Y"], "seg_ov_window", "90D")
    ts = dash.revenue_profit_series(s.history, {"7D": 7, "30D": 30, "90D": 90, "1Y": 365}[win])
    with st.container(key="panel_rp"):
        st.altair_chart(charts.line_hover(ts, {"Revenue": "revenue", "Operating profit": "profit"},
                                          colors={"Revenue": T.COBALT, "Operating profit": T.ORANGE},
                                          zero_rule=True, height=280), width="stretch")
        st.caption(f"{ts['granularity'].iloc[0].capitalize()} values. Operating profit = sales − all costs "
                   "(accrual). Negative days early in the week are normal for a restaurant.")
    c1, c2 = st.columns([3, 2])
    with c1, st.container(key="panel_bi"):
        st.markdown("#### Changes worth investigating")
        st.caption("Last 28 days. These describe what moved, not why.")
        if product_ctx() is None:
            pc = dash.profit_change(s, 28)
            for d in pc["drivers"][:4]:
                st.markdown(f'<div class="insight-text">{T.esc(d["driver"])}: {T.money(d["previous"])} → '
                            f'{T.money(d["current"])} (profit effect {T.signed_money(d["profit_effect"])})</div>',
                            unsafe_allow_html=True)
        else:
            for ins in dash.insights(s, "28d"):
                kind = "simulated" if "simulated" in ins["basis"] else "observed"
                st.markdown(f'<div class="insight-title">{T.esc(ins["title"])} {T.origin(kind)}</div>'
                            f'<div class="insight-text">{T.esc(ins["text"])}</div>', unsafe_allow_html=True)
    with c2, st.container(key="card_decision"):
        decision_card()


def briefing_panel() -> None:
    with st.container(key="card_briefing"):
        st.markdown(f'<div class="insight-title">Today\'s briefing · needs attention '
                    f'{T.origin("observed")}</div>', unsafe_allow_html=True)
        if not b["alerts"]:
            st.markdown('<div class="insight-text">Nothing urgent today.</div>', unsafe_allow_html=True)
        for j, a in enumerate(b["alerts"]):
            basis = {"observed": "observed", "forecast": "forecast", "simulated": "simulated"}[a["basis"]]
            cols = st.columns([5, 1.3], vertical_alignment="center")
            cols[0].markdown(f'<div class="alert-row"><span class="sev {a["severity"]}">{a["severity"]}</span><div>'
                             f'<div class="alert-title">{T.esc(a["title"])} {T.origin(basis)}</div>'
                             f'<div class="alert-detail">{T.esc(a["detail"])}</div>'
                             f'<div class="alert-next">Next step: {T.esc(a["next_step"])}</div></div></div>',
                             unsafe_allow_html=True)
            if a.get("page"):
                kw = {}
                if a["page"] == "Decision Lab" and a.get("product"):
                    kw["sl_product"] = a["product"]
                elif a["page"] == "My Business" and a.get("product"):
                    kw = {"seg_mb_section": "Products", "prod_pick": a["product"]}
                elif a["page"] == "My Business":
                    kw["seg_mb_section"] = {"cash": "Cash & bills", "bills": "Cash & bills"}.get(a["kind"], "Sales & profit")
                cols[1].button(f"Open {a['page']} →", key=f"go_alert_{j}", on_click=go, args=(a["page"],), kwargs=kw)
        if b.get("alerts_suppressed"):
            st.caption(f"{b['alerts_suppressed']} lower-priority item(s) not shown, to keep this short.")
        st.caption(b["note"])


def decision_card() -> None:
    a = b.get("action")
    st.markdown(f'<div class="insight-title">One decision to consider {T.origin("simulated")}</div>',
                unsafe_allow_html=True)
    if a is None:
        st.markdown('<div class="insight-text">Test inventory budgets, cost changes and actions for the '
                    'whole business in the Decision Lab.</div>', unsafe_allow_html=True)
        st.button("Open the Decision Lab →", key="go_lab", on_click=go, args=("Decision Lab",))
        return
    st.markdown(f'<div class="insight-text"><b>{T.esc(a["action"])}</b></div>', unsafe_allow_html=True)
    if a.get("impact"):
        i = a["impact"]
        st.markdown(f"Estimated profit **{T.signed_money(i['profit_change'])}** over {i['horizon_days']} days "
                    f"(likely range {T.signed_money(i['range_90'][0])} to {T.signed_money(i['range_90'][1])}; "
                    f"better than the current plan in {i['probability_better_pct']}% of simulated months).")
        st.caption("Why it might help: " + a["why"])
    else:
        st.caption(a["why"])
    with st.expander("Checklist before deciding"):
        checklist(a)
    if a.get("scenario"):
        sc = a["scenario"]
        st.button("Open in the Decision Lab →", key="go_brief_lab", on_click=go, args=("Decision Lab",),
                  kwargs={"sl_product": a["product"], "sl_price": sc["price_change_pct"],
                          "sl_price_num": sc["price_change_pct"], "sl_supply": sc["supply_change_pct"],
                          "sl_cost": sc.get("ingredient_cost_change_pct", 0.0), "lab_link_applied": True})


def checklist(a: dict, key: str | None = None) -> None:
    """Approval-first checklist: what, why, impact, uncertainty, assumptions, risks, verify."""
    if a.get("uncertainty"):
        st.markdown('<div class="check-head">How uncertain</div>', unsafe_allow_html=True)
        st.markdown(a["uncertainty"])
    if a.get("assumptions"):
        st.markdown('<div class="check-head">Assumptions</div>', unsafe_allow_html=True)
        st.markdown("\n".join(f"- {x}" for x in a["assumptions"]))
    st.markdown('<div class="check-head">What could go wrong</div>', unsafe_allow_html=True)
    st.markdown("\n".join(f"- {r}" for r in a["risks"]) or "- No major risks flagged by the simulation.")
    st.markdown('<div class="check-head">Verify first</div>', unsafe_allow_html=True)
    if key:
        for j, v in enumerate(a["verify"]):
            st.checkbox(v, key=f"{key}_v{j}")
    else:
        st.markdown("\n".join(f"- {v}" for v in a["verify"]))
    st.markdown("**Your approval is required. Nothing is changed automatically:** you make the change "
                "yourself (menu, POS or supplier order) if you decide to.")


# ================================================================================ MY BUSINESS
def page_my_business() -> None:
    st.markdown("# My Business")
    section = seg("Section", MB_SECTIONS, "seg_mb_section", "Sales & profit")
    ctx = product_ctx()
    h = s.history
    end = h["date"].max().date()
    start, stop = pd.Timestamp(end) - pd.Timedelta(days=89), pd.Timestamp(end)
    if section in ("Sales & profit", "Inventory"):
        dr = st.date_input("Date range", value=(end - timedelta(days=89), end),
                           min_value=h["date"].min().date(), max_value=end, key="mb_dates")
        if isinstance(dr, tuple) and len(dr) == 2:
            start, stop = pd.Timestamp(dr[0]), pd.Timestamp(dr[1])
    {"Sales & profit": lambda: section_sales(ctx, start, stop), "Products": lambda: section_products(ctx),
     "Inventory": lambda: section_inventory(ctx, start, stop), "Expenses": section_expenses,
     "Cash & bills": lambda: section_cash(start, stop), "Forecasts": section_forecasts}[section]()


def section_sales(ctx, start, stop) -> None:
    h = s.history.copy()
    h["profit"] = h["revenue"] - h["variable_costs"] - h["fixed_costs"]
    span = (stop - start).days + 1
    cur, prev = dash._slice(h, start, stop), dash._slice(h, start - pd.Timedelta(days=span), start - pd.Timedelta(days=1))
    vol = "Guests" if s.industry == "restaurant" else "Units sold"
    metrics = {"Revenue": ("revenue", "sum", "$"), "Operating profit": ("profit", "sum", "$")}
    if "units_sold" in h:
        metrics[vol] = ("units_sold", "sum", "#")
        metrics["Sales per guest" if s.industry == "restaurant" else "Revenue per unit"] = ("per", "ratio", "$2")
    if ctx is not None:
        rc = ctx.history.recon.set_index("date")["food_cost"]
        h["gross_profit"] = h["revenue"] - h["date"].map(rc).fillna(0)
        cur, prev = dash._slice(h, start, stop), dash._slice(h, start - pd.Timedelta(days=span), start - pd.Timedelta(days=1))
        metrics["Gross profit"] = ("gross_profit", "sum", "$")

    def value(frame, col, how_):
        if how_ == "ratio":
            return frame["revenue"].sum() / frame["units_sold"].sum() if frame["units_sold"].sum() else None
        return frame[col].sum()

    def fmt(v, kind):
        return "n/a" if v is None else T.money(v, 2) if kind == "$2" else T.money(v) if kind == "$" else f"{v:,.0f}"

    st.caption("Click a metric to see its chart and explanation. Each is compared with the previous "
               "period of the same length.")
    names = list(metrics)
    pick = seg("Metric", names, "seg_mb_metric", "Revenue")
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
        "Revenue": "Total sales recorded each day.",
        "Operating profit": "Sales minus all costs that day (ingredients, packaging, card fees, labor, "
                            "rent, utilities, other). Low or negative days early in the week are normal.",
        "Gross profit": "Sales minus ingredient (food) cost. It is what is left to pay labor, rent "
                        "and everything else.",
        vol: "Recorded guests (covers) per day." if s.industry == "restaurant" else "Units sold per day.",
        "Sales per guest": "Revenue divided by guests. There is no ticket-level data, so this is "
                           "not a true average check.",
        "Revenue per unit": "Revenue divided by units sold.",
    }[pick]
    with st.container(key="panel_metric"):
        st.markdown(f"#### {pick} " + T.origin("observed"), unsafe_allow_html=True)
        yf = "$,.2f" if kind == "$2" else "$,.0f" if kind == "$" else ",.0f"
        st.altair_chart(charts.line_hover(d, {f"Daily {pick.lower()}": "v", "7-day average": "avg7"},
                                          colors={f"Daily {pick.lower()}": T.BASELINE, "7-day average": T.COBALT},
                                          height=280, y_format=yf, zero_rule=pick == "Operating profit"),
                        width="stretch")
        st.caption(explain)
    if ctx is not None:
        with st.container(key="panel_contrib"):
            st.markdown("#### Sales by product (weekly) " + T.origin("observed"), unsafe_allow_html=True)
            w = views.weekly_product_revenue(ctx.history.sales, ctx.history.recon, start, stop, None)
            st.altair_chart(charts.stacked_area(w), width="stretch")
            st.caption("Five tracked products plus other items (sides, add-ons, desserts); together they "
                       "equal total revenue.")


def section_products(ctx) -> None:
    if ctx is None:
        return empty_state("No product table for this dataset.")
    period = seg("Sales period", ["28d", "90d", "365d"], "seg_mb_tbl", "28d", lambda k: dash.PERIODS[k])
    t = dash.product_table(s, period)
    show = t.assign(stockout_risk_7d=t["stockout_risk_7d"] * 100)[
        ["product", "price", "unit_cost", "gross_margin_pct", "units_sold", "revenue",
         "forecast_demand_7d", "sellable_units", "stockout_risk_7d", "days_of_supply"]]
    st.markdown("#### Your five products — select a row to explore one")
    ev = st.dataframe(show, hide_index=True, width="stretch", on_select="rerun",
                      selection_mode="single-row", key="prod_table", column_config={
                          "product": st.column_config.TextColumn("Product"),
                          "price": st.column_config.NumberColumn("Price", format="$%.2f", help="Current menu price"),
                          "unit_cost": st.column_config.NumberColumn("Ingredient cost", format="$%.2f", help="Ingredient cost per unit (recipe x current ingredient prices)"),
                          "gross_margin_pct": st.column_config.ProgressColumn("Margin", format="%.1f%%", min_value=0, max_value=100, help="(price − ingredient cost) / price, on recorded sales"),
                          "units_sold": st.column_config.NumberColumn("Units sold", format="%d"),
                          "revenue": st.column_config.NumberColumn("Sales", format="$%.0f"),
                          "forecast_demand_7d": st.column_config.NumberColumn("Forecast demand (7 d)", format="%.0f", help="Model forecast of customer demand, next 7 days"),
                          "sellable_units": st.column_config.NumberColumn("Can make from stock", format="%.0f", help="Units the ingredients on hand can make at the forecast menu mix"),
                          "stockout_risk_7d": st.column_config.ProgressColumn("Sell-out risk (7 d)", format="%.0f%%", min_value=0, max_value=100, help="Share of simulated weeks with ≥1% of demand unmet under the current ordering plan. A simulation frequency, not a validated probability."),
                          "days_of_supply": st.column_config.NumberColumn("Days of supply", format="%.1f")})
    rows = (ev.selection.rows if ev is not None and hasattr(ev, "selection") else []) or []
    if rows:
        st.session_state["prod_pick"] = t.iloc[rows[0]]["product"]
    st.session_state.setdefault("prod_pick", t.iloc[0]["product"])
    name = st.selectbox("Explore one product", list(views.NAMES.values()), key="prod_pick")
    product_detail(ctx, name, t)


def section_inventory(ctx, start, stop) -> None:
    inv = dash.inventory_kpi(s)
    if ctx is None:
        st.metric("Inventory value", T.money(inv["value"]), help=inv["basis"])
        return empty_state("No ingredient ledger for this dataset.")
    t = dash.product_table(s, "28d")
    nd = dash.next_delivery_days(ctx.origin)
    with st.container(key="panel_inv"):
        st.markdown("#### Sell-out risk this week " + T.origin("simulated"), unsafe_allow_html=True)
        st.altair_chart(charts.inventory_health(views.inventory_frame(t, nd)), width="stretch")
        st.caption(f"Bars: units the ingredients on hand can make (physical stock). Black tick: forecast "
                   f"demand until the next delivery in {nd} days. Colour: share of simulated weeks with "
                   "unmet demand — a simulation frequency, not a validated probability.")
    st.markdown(f"#### Ingredients on hand (end of {ctx.origin.date()}) " + T.origin("observed"),
                unsafe_allow_html=True)
    st.dataframe(pd.DataFrame(inv["items"]).round(2), hide_index=True, width="stretch")
    st.caption(f"Next delivery in {inv['next_delivery_in_days']} days (Mon/Thu; orders bring stock up to "
               "1.35× expected use). Days of supply use the forecast usage of all five products.")
    led = ctx.history.ledger
    led = led[(led["date"] >= start) & (led["date"] <= stop)].copy()
    avg_use = led.groupby("ingredient")["used"].transform(lambda x: x.rolling(28, min_periods=7).mean())
    led["days_of_supply"] = led["closing"] / avg_use.replace(0, np.nan)
    with st.container(key="panel_ingr"):
        st.altair_chart(charts.ingredient_history(led.dropna(subset=["days_of_supply"])), width="stretch")
        st.caption("Recorded stock after each day, in days of recent average use. Deliveries make the "
                   "saw-tooth; drops toward zero are near sell-outs.")
    from decisiondesk.products import BOM, INGREDIENTS
    with st.expander("Recipes (what each product uses)"):
        st.dataframe(pd.DataFrame(BOM, index=list(views.NAMES.values()),
                                  columns=[f"{k.name} ({k.unit})" for k in INGREDIENTS]), width="stretch")
        st.caption("Assumed recipes for the demo restaurant.")


def section_expenses() -> None:
    period = seg("Period", ["mtd", "28d", "90d", "365d"], "seg_mb_exp", "28d", lambda k: dash.PERIODS[k])
    e = dash.expense_breakdown(s, period)
    e["pct_label"] = e["share_pct"].map(lambda v: f"{v:.0f}%")
    with st.container(key="panel_exp"):
        st.markdown("#### Where the money goes " + T.origin("observed"), unsafe_allow_html=True)
        st.altair_chart(charts.expense_bars(e), width="stretch")
        st.caption("Accrual expenses in the period; they add up to total expenses on the Overview.")
    pc = dash.profit_change(s, 28)
    m = st.columns(3)
    m[0].metric("Operating profit (last 28 days)", T.money(pc["profit"]["current"]),
                T.signed_money(pc["profit"]["change"]) + " vs previous 28 days")
    m[1].metric("Previous 28 days", T.money(pc["profit"]["previous"]))
    m[2].metric("Closed days (now / before)", f"{pc['closed_days']['current']} / {pc['closed_days']['previous']}")
    with st.container(key="panel_drivers"):
        st.markdown("#### What changed profit (last 28 days vs the 28 before) " + T.origin("observed"),
                    unsafe_allow_html=True)
        dfd = pd.DataFrame(pc["drivers"]).head(10)
        dfd["abs_effect"] = dfd["profit_effect"].abs()
        st.altair_chart(charts.driver_bars(dfd), width="stretch")
        st.caption(pc["note"])
    if pc["products"]:
        with st.expander("Product sales changes"):
            st.dataframe(pd.DataFrame(pc["products"]), hide_index=True, width="stretch")


def section_cash(start, stop) -> None:
    h = s.history
    if "cash_balance" not in h:
        st.info("No cash ledger in this dataset.")
    else:
        with st.container(key="panel_cash"):
            st.markdown("#### Cash balance " + T.origin("observed"), unsafe_allow_html=True)
            d = dash._slice(h, stop - pd.Timedelta(days=179), stop)[["date", "cash_balance"]]
            st.altair_chart(charts.line_hover(d, {"Cash balance (ledger)": "cash_balance"},
                                              colors={"Cash balance (ledger)": T.COBALT}, height=260), width="stretch")
            st.caption("Synthetic cash ledger: declared opening balance plus recorded money in and out "
                       "(including owner draws). Not a bank balance.")
    li = dash.liabilities(s)
    if li.get("payables") is not None:
        m = st.columns(3)
        m[0].metric("Supplier invoices unpaid", T.money(li["payables"]), help=f"Net-{li['payment_terms_days']} invoices not yet paid")
        m[1].metric("Due in next 14 days", T.money(li["due_14d"]))
        m[2].metric("Loan principal", "not in data")
        st.markdown("#### Scheduled bills, next 14 days " + T.origin("forecast"), unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(li["scheduled_bills"]), hide_index=True, width="stretch")
        st.caption(li["note"])
    if st.button("Simulate cash for the next 28 days", key="mb_cash_risk"):
        st.session_state["mb_cash"] = run_tool(s, "assess_cash_risk", {"starting_cash": None})
    cr = st.session_state.get("mb_cash")
    if cr:
        if not cr["available"]:
            st.warning(f"Cash simulation needs a starting balance: {cr['reason']}.")
        else:
            lo, hi = cr["ending_cash_80pct_interval"]
            st.markdown(f"Simulated 28-day cash shortfall frequency **{cr['simulated_shortfall_frequency']}**; "
                        f"median ending cash {T.money(cr['median_ending_cash'])} (80% range {T.money(lo)} to "
                        f"{T.money(hi)}). " + T.origin("simulated"), unsafe_allow_html=True)
            st.caption(f"{cr['calibration']}. {cr['warning']}")


def section_forecasts() -> None:
    h = s.history
    targets = [t for t in ("revenue", "units_sold", "variable_costs", "net_cash_flow")
               if t == "net_cash_flow" or t in h.columns]
    target = st.selectbox("Metric", targets, key="fc_target")
    with st.spinner("Running chronological validation..."):
        fc = run_tool(s, "forecast_business", {"target": target})
    v = fc["interval_80_total_validation"]
    a, b_, c3, c4 = st.columns(4)
    a.metric("Next 28 days (forecast)", f"{fc['point_total']:,.0f}")
    b_.metric("Likely range (80%)", f"{fc['interval_80_total'][0]:,.0f} – {fc['interval_80_total'][1]:,.0f}",
              help=fc["interval_method"])
    c3.metric("Range check: past totals inside", f"{v['total_hits']}/{v['windows']}",
              f"{v['total_coverage']:.0%} (target 80%)", delta_color="off", delta_arrow="off",
              help=v["scheme"] + ". 95% uncertainty of this coverage estimate: "
                   f"{v['total_coverage_95ci'][0]:.0%}–{v['total_coverage_95ci'][1]:.0%}.")
    c4.metric("Typical error on unseen data (WAPE)", f"{fc['final_test']['wape']}")
    _range_warning(v)
    from decisiondesk.decision.baseline import make_draws
    bundle = s.bundle((target,))
    tf = bundle.targets[target]
    sims = bundle.simulate(make_draws(bundle, 2000, seed=11))[target]
    y = h[target] if target != "net_cash_flow" else (h.revenue - h.variable_costs - h.fixed_costs)
    past = pd.DataFrame({"date": h["date"], "Actual": y}).tail(84)
    fut = pd.DataFrame({"date": tf.dates, "Forecast": tf.point,
                        "lo": np.quantile(sims, .1, axis=0), "hi": np.quantile(sims, .9, axis=0)})
    both = pd.concat([past, fut], ignore_index=True)
    with st.container(key="panel_fc"):
        st.altair_chart(charts.line_hover(both[["date", "Actual", "Forecast"]],
                                          {"Actual": "Actual", "Forecast": "Forecast"},
                                          colors={"Actual": T.INK, "Forecast": T.COBALT},
                                          dashed=("Forecast",), y_format=",.0f"), width="stretch")
    with st.expander("How was this calculated?"):
        st.write(f"Model chosen by validation on past data: **{fc['selected_model']}** — {fc['selection_reason']}")
        st.caption(f"Daily ranges cover {v['daily_coverage']:.0%} of past out-of-sample days; the 28-day "
                   "total range is wider because days are correlated.")
        st.dataframe(pd.DataFrame(fc["leaderboard"]), width="stretch", hide_index=True)
        st.caption(fc["caveat"])


def _range_warning(v: dict) -> None:
    """Warn when a displayed range is not validated or its check is far from the 80% target."""
    if not v.get("windows"):
        st.warning("This range is NOT validated: there are no out-of-sample windows to check it on.")
    elif not v.get("assessable"):
        st.warning(f"Range checked on only {v['windows']} past windows: calibration is not established.")
    elif v["total_coverage"] < 0.65:
        st.warning(f"Range is likely TOO NARROW: only {v['total_coverage']:.0%} of past totals fell inside.")
    elif v["total_coverage"] > 0.92:
        st.info(f"Range is conservative: {v['total_coverage']:.0%} of past totals fell inside an 80% range "
                "(early backtest windows had larger errors). Treat it as a cautious band.")


def product_detail(ctx, name: str, table: pd.DataFrame) -> None:
    from decisiondesk.decision.product_scenario import baseline
    from decisiondesk.products import BOM, INGREDIENTS, PRODUCT_IDS
    pid = next(k for k, v in views.NAMES.items() if v == name)
    i = PRODUCT_IDS.index(pid)
    row = table.set_index("product").loc[name]
    b28 = baseline(ctx, 28, pid)
    so = views.stockout_date(b28, pid)
    st.markdown(f"## {name}")
    m = st.columns(6)
    m[0].metric("Current price", T.money(row["price"], 2))
    m[1].metric("Ingredient cost / unit", T.money(row["unit_cost"], 2))
    m[2].metric("Gross margin", T.pct(row["gross_margin_pct"]))
    m[3].metric("Available stock (units)", f"{row['sellable_units']:,.0f}", f"{row['days_of_supply']:.1f} days of supply",
                delta_color="off", delta_arrow="off",
                help="Units the ingredients on hand can make at the forecast menu mix, before the next delivery.")
    m[4].metric("Forecast demand (7 d)", f"{row['forecast_demand_7d']:,.0f}")
    m[5].metric("Stock-out date", so[5:] if so else "None",
                help="First forecast day on which at least half of the simulated paths leave demand "
                     "unmet under the current ordering plan.")
    s_ = ctx.history.sales
    ps = s_[s_["product_id"] == pid].tail(120)
    c1, c2 = st.columns([3, 2])
    with c1, st.container(key="panel_pd_hist"):
        st.markdown("#### Sales vs estimated demand (last 120 days) " + T.origin("observed"), unsafe_allow_html=True)
        st.altair_chart(charts.sales_demand_history(ps[["date", "units_sold", "demand_est"]]), width="stretch")
        st.caption(f"{int(ps['stockout'].sum())} sell-out day(s) in this window. On a sell-out day sales are "
                   "capped by stock; estimated demand corrects for that using the sell-out time.")
    with c2, st.container(key="panel_pd_price"):
        st.markdown("#### Price and ingredient cost " + T.origin("observed"), unsafe_allow_html=True)
        st.altair_chart(charts.price_history(s_[s_["product_id"] == pid][["date", "price", "unit_cost"]]),
                        width="stretch")
        st.caption("Black: menu price. Orange dashed: ingredient cost per unit.")
    with st.container(key="panel_pd_ds"):
        st.altair_chart(charts.demand_supply(views.demand_supply_frame(b28, pid),
                                             title=f"{name}: forecast demand vs supply, next 28 days (current plan)"),
                        width="stretch")
        st.caption("Blue: forecast customer demand (band = 10th–90th percentile of simulated paths). Orange: "
                   "units this product's share of stock allows each morning, including planned Mon/Thu "
                   "deliveries. Black dashed: expected sales. Red markers: days when a quarter or more of "
                   "simulated paths run short.")
    use = pd.DataFrame({"ingredient": [k.name for k in INGREDIENTS], "per unit": BOM[i],
                        "next 7 days (forecast)": BOM[i] * row["forecast_demand_7d"],
                        "unit": [k.unit for k in INGREDIENTS]})
    with st.expander("Ingredients this product will use and how the forecast was made"):
        st.dataframe(use[use["per unit"] > 0].round(2), hide_index=True, width="stretch")
        bd = ctx.boards[pid]
        from decisiondesk.calibration import product_validation
        v7, v28 = product_validation(ctx, pid, 7), product_validation(ctx, pid, 28)
        st.caption(f"Demand model: {bd['selected_model']} — {bd['selection_reason']}. Error on unseen days "
                   f"{bd['test_mae']:.1f} units/day (same-weekday-last-week method {bd['seasonal_naive_test_mae']:.1f}). "
                   f"Ranges checked out of sample: {v7.total_hits}/{v7.windows} past 7-day totals and "
                   f"{v28.total_hits}/{v28.windows} past 28-day totals inside their 80% range. Synthetic data: "
                   "this tests the software, not real-world accuracy.")
    st.button(f"Test a change to {name.lower()} in the Decision Lab →", key="go_pd_lab", on_click=go,
              args=("Decision Lab",), kwargs={"sl_product": name})


# ================================================================================ LOCAL MARKET
def page_local_market() -> None:
    from decisiondesk import market as mk
    st.markdown("# Local Market " + T.pill("Public menus · verified & dated", "real"), unsafe_allow_html=True)
    ctx = product_ctx()
    if ctx is None:
        return empty_state("Local price comparison needs your product prices.")
    if "market" not in st.session_state:
        st.session_state["market"] = mk.load_market()
    m = st.session_state["market"]
    today = date.today()
    fr = mk.freshness(m, today)
    st.markdown(f'<div class="callout">{T.esc(mk.NOT_A_RECOMMENDATION)}</div>',
                unsafe_allow_html=True)
    st.caption(f"{fr['restaurants_with_verified_prices']} Austin restaurants with prices read from their own "
               f"official menus (of {fr['restaurants_researched']} researched); verified "
               f"{fr['oldest_verification']}. Public data, kept separate from your private records; it never "
               "feeds forecasts or profit calculations. City-wide comparison (the demo business has no address).")
    if fr["stale"]:
        st.warning(f"Market prices are {fr['oldest_age_days']} days old: re-verify before relying on them.")
    names = list(views.NAMES.values())
    c1, c2, c3 = st.columns([1, 2, 1])
    name = c1.selectbox("Product", names, key="lm_product")
    pid = next(k for k, v in views.NAMES.items() if v == name)
    concepts = sorted(m.prices["concept"].unique())
    types = c2.multiselect("Restaurant types", concepts, default=concepts, key="lm_types")
    similar = c3.toggle("Include similar servings", key="lm_similar",
                        help="Also count items whose portion or tier may differ (e.g. a smaller taquito).")
    from decisiondesk.products import PRODUCT_IDS
    our = float(ctx.prices[PRODUCT_IDS.index(pid)])
    c = mk.compare(pid, our, market=m, concepts=types or None, include_similar=similar, as_of=today)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Your price", f"${our:.2f}", c["our_serving"], delta_color="off", delta_arrow="off")
    if c["peer_count"]:
        k2.metric("Peer median", f"${c['peer_median']:.2f}", f"{c['difference_vs_median_pct']:+.1f}% yours vs median",
                  delta_color="off", delta_arrow="off")
        k3.metric("Peer range", f"${c['peer_min']:.0f}–{c['peer_max']:.0f}",
                  f"${c['peer_min']:.2f} to ${c['peer_max']:.2f}", delta_color="off", delta_arrow="off")
    else:
        k2.metric("Peer median", "n/a")
        k3.metric("Peer range", "n/a")
    k4.metric("Verified comparables", str(c["peer_count"]),
              c.get("position", "") if c["sufficient"] else "not enough for a benchmark",
              delta_color="off", delta_arrow="off")
    for w in c["warnings"]:
        st.warning(w)
    if c["comparables"]:
        df = pd.DataFrame(c["comparables"])
        with st.container(key="panel_market"):
            st.altair_chart(charts.price_position(df, our, c.get("peer_median"), label=c["our_serving"]),
                            width="stretch")
            st.caption("Each dot: one restaurant's price converted to your serving (e.g. 3 × a single taco = a "
                       "plate of three). Black line: your price; grey dashed: peer median.")
        st.dataframe(df[["restaurant", "item", "listed_price", "comparable_price", "source_url",
                         "verified_date", "match_level", "concept"]], hide_index=True, width="stretch",
                     column_config={"listed_price": st.column_config.NumberColumn("Listed", format="$%.2f"),
                                    "comparable_price": st.column_config.NumberColumn("For your serving", format="$%.2f"),
                                    "source_url": st.column_config.LinkColumn("Source (official menu)"),
                                    "verified_date": "Verified"})
    if c["excluded"]:
        with st.expander(f"Not counted ({len(c['excluded'])}): different servings, other items, filters"):
            st.dataframe(pd.DataFrame(c["excluded"])[["restaurant", "item", "listed_price", "serving",
                                                       "excluded_because", "source_url"]],
                         hide_index=True, width="stretch",
                         column_config={"source_url": st.column_config.LinkColumn("Source")})
    st.markdown("#### Test a price change in the Decision Lab " + T.origin("simulated"), unsafe_allow_html=True)
    st.caption("The Lab estimates demand, sales, stock and profit under stated price-sensitivity "
               "assumptions. A competitor's price does not tell you how your customers will react.")
    b1, b2, b3 = st.columns(3)
    options = [("Lower 5%", -5.0), ("Raise 5%", 5.0)]
    if c["sufficient"] and abs(c["difference_vs_median_pct"]) <= 30:
        to_med = round((c["peer_median"] / our - 1) * 100, 1)
        options.append((f"Test the peer median ({to_med:+.1f}%)", to_med))
    for col, (lab_, v) in zip((b1, b2, b3), options):
        col.button(f"{lab_} →", key=f"lm_go_{lab_[:5]}", on_click=go, args=("Decision Lab",),
                   kwargs={"sl_product": name, "sl_price": float(v), "sl_price_num": float(v),
                           "lab_link_applied": True})
    st.markdown("#### All five products")
    ov = mk.overview({p: float(ctx.prices[i]) for i, p in enumerate(PRODUCT_IDS)}, market=m,
                     as_of=today, concepts=types or None, include_similar=similar)
    ov.insert(0, "product", ov["product_id"].map(views.NAMES))
    for col in ("peer_min", "peer_median", "peer_max", "vs_median_pct"):
        ov[col] = pd.to_numeric(ov[col], errors="coerce")
    st.dataframe(ov.drop(columns=["product_id"]), hide_index=True, width="stretch",
                 column_config={k: st.column_config.NumberColumn(format="$%.2f")
                                for k in ("our_price", "peer_min", "peer_median", "peer_max")})
    with st.expander("Price monitoring and research log"):
        st.markdown(f"- Method: {fr['method']}\n- Next re-verification due: **{fr['next_reverification_due']}**\n"
                    "- No real-time prices: there is no reliable, permitted real-time source for independent "
                    "restaurants' menus. Ordering platforms that block automated access are not scraped.")
        if len(m.log):
            st.dataframe(m.log, hide_index=True, width="stretch")


# ================================================================================ DECISION LAB
def _sync(src: str, dst: str, lo: float, hi: float) -> None:
    st.session_state[dst] = float(min(hi, max(lo, st.session_state[src])))


def page_decision_lab() -> None:
    st.markdown("# Decision Lab")
    st.caption("Try a change and compare it with your current plan. Results are simulations under stated "
               "assumptions, not promises. Nothing is changed in your business until you decide to do it.")
    ctx = product_ctx()
    if ctx is None:
        empty_state("Product-level what-ifs are unavailable.")
    else:
        lab(ctx)
    with st.expander("Whole-business options (separate business-level model)", expanded=ctx is None):
        business_wide_actions()


LAB_KEYS = ("sl_price", "sl_price_num", "sl_supply", "sl_extra", "sl_cost", "sl_shock", "sl_mkt")


def _apply_link_params() -> None:
    """Shareable links, e.g. ?page=Decision%20Lab&product=Tacos&price=10&cost=8 (first load only)."""
    if st.session_state.get("lab_link_applied"):
        return
    st.session_state["lab_link_applied"] = True
    q = st.query_params
    if q.get("product") in views.NAMES.values():
        st.session_state["sl_product"] = q.get("product")
    for name, key, lo, hi in (("price", "sl_price", -30, 30), ("supply", "sl_supply", -100, 100),
                              ("extra", "sl_extra", 0, 5000), ("cost", "sl_cost", -20, 40),
                              ("shock", "sl_shock", -50, 50), ("marketing", "sl_mkt", -100, 100)):
        try:
            if q.get(name) is not None:
                st.session_state[key] = float(min(hi, max(lo, float(q.get(name)))))
        except ValueError:
            pass
    st.session_state["sl_price_num"] = st.session_state.get("sl_price", 0.0)
    if q.get("h") in ("7", "14", "28"):
        st.session_state["seg_sl_h"] = int(q.get("h"))


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
    left, right = st.columns([1, 2.3], gap="large")
    with left, st.container(key="panel_controls"):
        st.markdown("#### What do you want to change?")
        name = st.selectbox("Product", list(views.NAMES.values()), key="sl_product")
        pid = next(k for k, v in views.NAMES.items() if v == name)
        i = PRODUCT_IDS.index(pid)
        cap = (ctx.max_price_increase or 0.05) * 100
        st.slider(f"{name} price (%)", -30.0, 30.0, step=1.0, key="sl_price",
                  on_change=_sync, args=("sl_price", "sl_price_num", -30.0, 30.0))
        st.number_input("Exact price change (%)", -30.0, 30.0, step=0.5, key="sl_price_num",
                        on_change=_sync, args=("sl_price_num", "sl_price", -30.0, 30.0))
        p = st.session_state["sl_price"]
        new_price = round(ctx.prices[i] * (1 + p / 100), 2)
        tag = ('<span class="chip down">above your limit</span>' if p > cap + 1e-9
               else '<span class="chip up">within policy</span>')
        st.markdown(f'<div class="kpi-sub">{T.money(ctx.prices[i], 2)} → <b>{T.money(new_price, 2)}</b> '
                    f'{tag}<br>Your limit: price increases up to {cap:g}%.</div>', unsafe_allow_html=True)
        h = st.session_state.get("seg_sl_h", 28)
        st.slider(f"Ingredient orders for {name.lower()} (%)", -100.0, 100.0, step=5.0, key="sl_supply",
                  help="Scales this product's share of every planned Mon/Thu ingredient order.")
        fc_units = float(ctx.bundle.targets[f"demand_{pid}"].point[:h].clip(0).sum())
        st.caption(f"≈ {st.session_state['sl_supply'] / 100 * 1.35 * fc_units:+,.0f} units of "
                   f"{name.lower()} ingredients over {h} days (forecast demand {fc_units:,.0f} units).")
        st.number_input("One-time extra ingredient purchase (units)", 0.0, 5000.0, step=25.0, key="sl_extra",
                        help="Bought today, arrives tomorrow. Regular orders then bring stock up less.")
        st.caption(f"Costs {T.money(st.session_state['sl_extra'] * ctx.unit_cost[i])} at current ingredient prices.")
        st.slider("Supplier ingredient prices (%)", -20.0, 40.0, step=1.0, key="sl_cost",
                  help="A hypothetical change in what your suppliers charge for ALL ingredients (e.g. a "
                       "supplier price increase). Menu prices stay as set above.")
        st.slider("Customer demand (%)", -50.0, 50.0, step=5.0, key="sl_shock",
                  help="A hypothetical change in demand (weather, an event, road works).")
        scope = st.radio("Demand change applies to", ["This product", "All products"], horizontal=True, key="sl_scope")
        st.slider("Optional spending: marketing & software (%)", -100.0, 100.0, step=10.0, key="sl_mkt")
        st.caption(f"Now {T.money(ctx.marketing_monthly)}/month → "
                   f"{T.money(ctx.marketing_monthly * (1 + st.session_state['sl_mkt'] / 100))}/month.")
        st.markdown("**Look ahead**")
        h = seg("Horizon", [7, 14, 28], "seg_sl_h", 28, lambda d: f"{d} days")
        st.button("Reset to current plan", key="sl_reset", on_click=_reset_controls)
    sc = ProductScenario(pid, float(st.session_state["sl_price"]), float(st.session_state["sl_supply"]),
                         float(st.session_state["sl_extra"]), float(st.session_state["sl_shock"]),
                         "product" if scope == "This product" else "all",
                         float(st.session_state["sl_mkt"]), int(h), float(st.session_state["sl_cost"]))
    s.state.product_scenario = asdict(sc)
    t0 = time.perf_counter()
    base = baseline(ctx, h, pid)
    scen = simulate(ctx, sc)
    cmp_ = compare(ctx, scen, base)
    ms = (time.perf_counter() - t0) * 1000
    with right:
        head = "Current plan (no change yet)" if sc.is_baseline else f"Current plan vs {T.esc(sc.describe())}"
        st.markdown(f"#### {head} · next {h} days "
                    f"({cmp_['forecast_start']} to {cmp_['forecast_end']}) " + T.origin("simulated"),
                    unsafe_allow_html=True)
        if not cmp_["within_policy"]:
            st.markdown('<div class="callout bad"><b>Outside the owner\'s approved limits:</b> '
                        + T.esc("; ".join(cmp_["policy_violations"])) + ". Shown for exploration only; "
                        "it is excluded from recommendations.</div>", unsafe_allow_html=True)
        sr, cf = cmp_["stockout_risk"], cmp_["cash_flow"]
        cards = [("Revenue", cmp_["revenue"], True, T.money), ("Profit", cmp_["profit"], True, T.money),
                 ("Cash flow", cf, True, T.money),
                 (f"{name} demand (units)", cmp_["demand_units"], True, lambda v: f"{v:,.0f}")]
        g = st.columns(4)
        for j, (col, (lab_, blk, up_good, f)) in enumerate(zip(g, cards)):
            with col, st.container(key=f"card_cmp_a{j}"):
                chg = blk["change"]
                st.markdown(T.compare_html(lab_, f(blk["baseline"]), f(blk["scenario"]),
                                           T.chip(blk["change_pct"], good_when_up=up_good),
                                           f"{'+' if chg > 0 else ''}{f(chg)}"), unsafe_allow_html=True)
        g2 = st.columns(4)
        gm, inv, u = cmp_["gross_margin_pct"], cmp_["ending_inventory_value"], cmp_["units_sold"]
        extra = [
            (f"{name} units sold", f"{u['baseline']:,.0f}", f"{u['scenario']:,.0f}", T.chip(u["change_pct"]),
             f"{'+' if u['change'] > 0 else ''}{u['change']:,.0f}"),
            (f"{name} sell-out risk", f"{sr['baseline']:.0%}", f"{sr['scenario']:.0%}",
             T.chip((sr["scenario"] - sr["baseline"]) * 100, good_when_up=False, suffix=" pts")
             if sr["scenario"] != sr["baseline"] else '<span class="chip flat">no change</span>',
             f"lost units {cmp_['lost_units']['baseline']:,.0f} → {cmp_['lost_units']['scenario']:,.0f}"),
            ("Gross margin", f"{gm['baseline']:.1f}%", f"{gm['scenario']:.1f}%",
             f'<span class="chip flat">{gm["change_points"]:+.2f} pts</span>', "after ingredients, packaging, card fees"),
            ("Ingredients left at the end", T.money(inv["baseline"]), T.money(inv["scenario"]),
             T.chip(inv["change_pct"]), f"≈ {cmp_['ending_makeable_units_selected']:,.0f} {name.lower()} makeable"),
        ]
        for j, (col, (lab_, b_, n_, ch_, note)) in enumerate(zip(g2, extra)):
            with col, st.container(key=f"card_cmp_b{j}"):
                st.markdown(T.compare_html(lab_, b_, n_, ch_, T.esc(note)), unsafe_allow_html=True)
        lo, hi = cmp_["profit"]["change_90pct_range"]
        st.markdown(f'<div class="callout{" good" if cmp_["profit"]["change"] > 0 else ""}">Profit change '
                    f'<b>{T.signed_money(cmp_["profit"]["change"])}</b> (likely range {T.signed_money(lo)} to '
                    f'{T.signed_money(hi)} in 9 of 10 simulated futures); better than the current plan in '
                    f'<b>{cmp_["probability_profit_improves"]:.0%}</b> of {cmp_["n_simulations"]:,} simulated '
                    f'futures. Cash and profit differ because ingredient purchases are paid net-7 and unsold '
                    f'stock stays on the shelf. <span class="legend-row">Computed in {ms:.0f} ms.</span></div>',
                    unsafe_allow_html=True)
        rv = cmp_["range_validation"]
        tabs = st.tabs(["Sales & profit", "Demand vs supply", "Other products", "Uncertainty"])
        with tabs[0]:
            c1, c2 = st.columns(2)
            c1.altair_chart(charts.band_compare(views.band_frame(scen, base, "revenue"), title="Daily revenue"),
                            width="stretch")
            c2.altair_chart(charts.band_compare(views.band_frame(scen, base, "profit"), title="Daily operating profit"),
                            width="stretch")
            st.caption("Lines: average of simulated futures; bands: 10th–90th percentile. Grey dashed = current plan.")
        with tabs[1]:
            st.altair_chart(charts.demand_supply(views.demand_supply_frame(scen, pid),
                                                 title=f"{name}: demand vs supply under your change"), width="stretch")
            st.caption("When demand (blue) is above what the morning stock allows (orange), sales are capped; "
                       "some unmet demand switches to other dishes and the rest is lost.")
        with tabs[2]:
            st.altair_chart(charts.impact_bars(views.impact_frame(cmp_)), width="stretch")
            st.caption("Change vs the current plan for each product. Shared ingredients and customers switching "
                       "dishes mean one product's change moves the others.")
        with tabs[3]:
            d = scen.profit.sum(1) - base.profit.sum(1)
            st.altair_chart(charts.delta_histogram(d, lo, hi), width="stretch")
            st.caption(cmp_["interval_meaning"] + f" Range check ({h}-day totals, out of sample): revenue "
                       f"{rv['revenue_total']['total_hits']}/{rv['revenue_total']['windows']}, profit "
                       f"{rv['profit_total']['total_hits']}/{rv['profit_total']['windows']} past windows inside "
                       "their 80% range. " + rv["note"])

    # Checklist and recommendation
    rec = _cached(("rec", repr(sc)), lambda: recommend(ctx, sc))
    with st.container(key="panel_rec"):
        c1, c2 = st.columns([3, 2], gap="large")
        with c1:
            st.markdown("### Before you decide " + T.origin("simulated"), unsafe_allow_html=True)
            if sc.is_baseline:
                st.markdown("Change something on the left to see its checklist. Best tested option for "
                            f"{name.lower()}: " + rec["text"])
            else:
                from decisiondesk.briefing import decision_checklist
                from decisiondesk.decision.product_scenario import sensitivity
                sens = _cached(("sens", repr(sc)), lambda: sensitivity(ctx, sc))
                lm = _local_market(pid, float(ctx.prices[i])) if sc.price_change_pct else None
                a = decision_checklist(ctx, sc, cmp_, sens, local_market=lm)
                st.markdown(f"**What:** {a['action']}  \n**Why it might help:** {a['why']}  \n"
                            f"**Expected impact:** profit {T.signed_money(a['impact']['profit_change'])}, revenue "
                            f"{T.signed_money(a['impact']['revenue_change'])}, cash {T.signed_money(a['impact']['cash_change'])} "
                            f"over {h} days.")
                checklist(a, key="lab_chk")
        with c2:
            st.markdown("#### Options tested for " + T.esc(name.lower()), unsafe_allow_html=True)
            st.caption(rec["text"])
            if rec["ranked"]:
                st.dataframe(pd.DataFrame([{
                    "option": r["action"], "profit change": T.signed_money(r["profit_change"]),
                    "likely range": f"{T.money(r['profit_change_90pct_range'][0])} to {T.money(r['profit_change_90pct_range'][1])}",
                    "better in": f"{r['probability_profit_improves']:.0%}"} for r in rec["ranked"]]),
                    hide_index=True, width="stretch")
            for r in rec["outside_policy"]:
                st.caption(f"Excluded: {r['action']} — {'; '.join(r['policy_violations'])}.")
            e1, e2 = st.columns(2)
            if e1.button("Use for iMessage answers", key="lab_publish"):
                _publish()
            if e2.button("Ask the analyst about this", key="go_analyst"):
                _ask(f"What happens if {name.lower()} prices change {sc.price_change_pct:+g}%?"
                     if sc.price_change_pct else f"Should I buy more {name.lower()} ingredients?"
                     if sc.supply_change_pct or sc.extra_units else "What should I do?")
                st.rerun()
            if st.session_state.get("lab_published"):
                st.success(st.session_state["lab_published"])
    with st.expander("Assumptions and risks"):
        st.markdown("Price sensitivity, customers switching dishes and the effect of marketing are "
                    "**assumptions**, not measured from your sales. Each simulated future draws them from "
                    "the ranges below, so the likely range already includes them.")
        st.dataframe(pd.DataFrame([{k: a_.get(k) for k in ("name", "value", "low", "high", "source", "note")}
                                   for a_ in [asdict(ELASTICITY[pid]), asdict(SUBSTITUTION), asdict(MARKETING_RESPONSE)]]),
                     hide_index=True, width="stretch")
        if rec["sensitivity"]:
            st.markdown("**If an assumption is at its edge** (best tested option):\n" + "\n".join(
                f"- {x['assumption'].replace('_', ' ')} at its {x['level']} bound ({x['value']}): profit change "
                f"{T.signed_money(x['profit_change'])}" for x in rec["sensitivity"]))
        st.markdown("**Other limits:** forecasts are validated on synthetic history only; supplier prices change "
                    "every ingredient by the same percent; the simulation does not model staff capacity or "
                    "competitor reactions.")
        st.caption(f"{ctx.n_sims:,} simulated futures, seed {cmp_['seed']}, same futures for every option. "
                   "Data: SYNTHETIC.")


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
    st.caption("Change the business or upload your own daily file under **Business & data** at the top of the "
               "sidebar. Uploaded files stay on this computer (data/private, never committed).")
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
