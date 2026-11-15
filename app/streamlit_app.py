"""Main Street Risk Desk — Streamlit interface (Milestone 5.5: premium owner experience).

    ./start.sh            (opens http://localhost:8501 in your browser)
    streamlit run app/streamlit_app.py

Four pages built around an owner's questions (what is happening, why it matters, what can I do):
Overview, My Business, Decision Lab and Local Market. The assistant ("Ask AI") opens as a side
panel on every page. Notifications (iMessage), data upload and Advanced diagnostics (model
validation, calibration, reconciliation, banking sandbox) live under Settings.

Every number comes from the analytics modules (``decisiondesk.dashboard``, ``decisiondesk.briefing``,
``decisiondesk.decision.product_scenario``, ``decisiondesk.agent.tools``); this file only lays them
out and words them in business language. Exact ranges, assumptions and limits stay one click
away ("How we estimated this"). The demo business is labelled "Demo Business · Sample Data".
Nothing is executed automatically: every action needs the owner's approval.
"""
from __future__ import annotations

import os
import re
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
MB_SECTIONS = ["Sales & profit", "Products", "Costs", "Stock", "Cash & bills", "Outlook"]
SETTINGS = ["Notifications", "Data", "Advanced diagnostics"]
DEMOS = {"Demo restaurant (Austin, TX)": "restaurant", "Demo retailer (Columbus, OH)": "retail"}
MODES = ["Change a price", "Order more or less", "Supplier prices change", "Busier or quieter days",
         "Marketing spend", "Combine changes"]
LAB_KEYS = ("sl_price", "sl_supply", "sl_extra", "sl_cost", "sl_shock", "sl_mkt")
MODE_KEYS = {"Change a price": ("sl_price",), "Order more or less": ("sl_supply", "sl_extra"),
             "Supplier prices change": ("sl_cost",), "Busier or quieter days": ("sl_shock",),
             "Marketing spend": ("sl_mkt",), "Combine changes": LAB_KEYS}
SAMPLE_NOTE = ("This demo uses a fictional business with computer-generated (synthetic) records so you can "
               "explore the product. It is not connected to a real bank, POS or accounting system. "
               "Connect your own data in Settings → Data.")
NUM_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five"}

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


def _reset_lab() -> None:
    for k in [k for k in st.session_state if k.startswith(("sl_", "lab_", "brief", "seg_sl_mode", "prod_view"))]:
        del st.session_state[k]


def _load_demo(key: str) -> None:
    with st.spinner("Getting your numbers ready..."):
        s_ = BusinessSession.from_demo(key, models=MODELS, n_sims=1000 if FAST else 2000)
    _reset_lab()
    st.session_state.update(session=s_, agent=DecisionAgent(s_), chat=[], business_key=key,
                            last_decision=None, pending=None)


def _switch_business() -> None:
    _load_demo(DEMOS[st.session_state["source"]])


if "session" not in st.session_state:
    _load_demo("restaurant")
s: BusinessSession = st.session_state["session"]

if "nav" not in st.session_state:  # deep links: ?page=Decision%20Lab, ?view=settings&section=...
    q = st.query_params
    st.session_state["nav"] = q.get("page") if q.get("page") in PAGES else PAGES[0]
    if q.get("section") in MB_SECTIONS:
        st.session_state["seg_mb_section"] = q.get("section")
    if q.get("view") == "settings":
        open_settings(q.get("section") if q.get("section") in SETTINGS else "Notifications")
    if q.get("chat") == "open":
        st.session_state["chat_open"] = True
st.session_state.setdefault("view", None)
st.session_state["_tip_n"] = 0  # unique keys for "How we estimated this" popovers on each run


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


def how(text: str, label: str = "How we estimated this") -> None:
    n = st.session_state["_tip_n"] = st.session_state.get("_tip_n", 0) + 1
    with st.container(key=f"tip_{n}"), st.popover(f"ⓘ {label}", width="content"):
        st.markdown(text)


def weeks(h: int) -> str:
    return {7: "next week", 14: "next 2 weeks", 28: "next 4 weeks"}.get(int(h), f"next {h} days")


def section(title: str, sub: str = "") -> None:
    st.markdown(f'<div class="section-title">{T.esc(title)}</div>'
                + (f'<div class="section-sub">{T.esc(sub)}</div>' if sub else ""), unsafe_allow_html=True)


def biz_name() -> str:
    return re.sub(r"\s*\(fictional\)", "", s.metadata.get("business_name", s.dataset))


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


def lab_link_for(sc: dict) -> dict:
    """Decision Lab link that reproduces a ProductScenario dict."""
    vals = {"sl_price": sc.get("price_change_pct", 0.0), "sl_supply": sc.get("supply_change_pct", 0.0),
            "sl_extra": sc.get("extra_units", 0.0), "sl_cost": sc.get("ingredient_cost_change_pct", 0.0),
            "sl_shock": sc.get("demand_shock_pct", 0.0), "sl_mkt": sc.get("marketing_change_pct", 0.0)}
    used = [m for m in MODES[:-1] if any(vals[k] for k in MODE_KEYS[m])]
    mode = used[0] if len(used) == 1 else "Combine changes" if used else "Change a price"
    return lab_link(views.NAMES.get(sc.get("product_id"), "Tacos"), mode, **vals)


# ------------------------------------------------------------------------------ header
b = _briefing()
demo = "SYNTHETIC" in s.data_label.upper()
hdr = st.columns([2.3, 5.0, 1.75, 0.7, 0.7], vertical_alignment="center")
with hdr[0]:
    sample = (f'<span class="sample" title="{T.esc(SAMPLE_NOTE)}">Demo Business · Sample Data</span>' if demo
              else f'<span class="status pos">{T.esc(s.data_label.title())}</span>')
    st.markdown(f'<div class="brand"><div class="brand-mark">M</div><div><div class="brand-name">Main Street '
                f'Risk Desk</div><div class="biz-line">{sample}</div></div></div>', unsafe_allow_html=True)
with hdr[1]:
    page = st.radio("Navigate", PAGES, horizontal=True, key="nav", label_visibility="collapsed",
                    on_change=close_settings)
with hdr[2]:
    if st.session_state.get("business_key") in DEMOS.values():
        st.session_state.setdefault("source", next(k for k, v in DEMOS.items()
                                                   if v == st.session_state["business_key"]))
        st.selectbox("Business", list(DEMOS), key="source", on_change=_switch_business,
                     label_visibility="collapsed",
                     format_func=lambda k: {"restaurant": "Lone Star Taqueria", "retail": "Maple Street Hardware"}[DEMOS[k]])
    else:
        st.markdown(f'<div class="row-title">{T.esc(biz_name())}</div>', unsafe_allow_html=True)
with hdr[3].popover(f"🔔{len(b['alerts'])}" if b["alerts"] else "🔔", width="stretch", help="Notifications"):
    st.markdown("**Today**")
    if not b["alerts"]:
        st.caption("Nothing needs your attention today.")
    for a in b["alerts"]:
        st.markdown(f'<div class="row-item"><span class="dot {a["severity"]}"></span><div>'
                    f'<div class="row-title">{T.esc(a["title"])}</div>'
                    f'<div class="row-detail">{T.esc(a["detail"])}</div></div></div>', unsafe_allow_html=True)
    st.button("Get alerts on my phone →", key="bell_settings", on_click=open_settings, args=("Notifications",))
with hdr[4].popover("⚙", width="stretch", help="Settings"):
    st.caption("Using your own data? Upload it in Data & upload.")
    st.button("Notifications", key="open_settings", on_click=open_settings, args=("Notifications",), width="stretch")
    st.button("Data & upload", key="open_data", on_click=open_settings, args=("Data",), width="stretch")
    st.button("Advanced diagnostics", key="open_diag", on_click=open_settings, args=("Advanced diagnostics",),
              width="stretch")
st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)


# ================================================================================ ASSISTANT
TOOL_LINKS = {
    "get_local_market_prices": ("See local prices", "Local Market", lambda o, i: {"lm_product": views.NAMES.get(o.get("product_id"), "Tacos")}),
    "get_product_performance": ("See your products", "My Business", lambda o, i: {"seg_mb_section": "Products", "prod_view": False}),
    "explain_profit_change": ("See what changed profit", "My Business", lambda o, i: {"seg_mb_section": "Costs"}),
    "get_daily_briefing": ("Open today's overview", "Overview", lambda o, i: {}),
    "forecast_business": ("See the outlook", "My Business", lambda o, i: {"seg_mb_section": "Outlook"}),
    "assess_cash_risk": ("See cash & bills", "My Business", lambda o, i: {"seg_mb_section": "Cash & bills"}),
    "get_business_overview": ("Open overview", "Overview", lambda o, i: {}),
    "simulate_product_scenario": ("Open this in the Decision Lab", "Decision Lab", lambda o, i: lab_link_for(o.get("scenario", {}))),
    "evaluate_restaurant_actions": ("See whole-business options", "Decision Lab", lambda o, i: {}),
}


def suggestions() -> list[str]:
    p = current_product()
    pl = (p or "taco").lower()
    if product_ctx() is None:
        return ["What should I focus on today?", "How is my business doing?", "Why did profit change?",
                "Will I run out of cash?"]
    return {"Overview": ["What should I focus on today?", "Why did my profit change?",
                         "Which product is making the most money?", "Why did revenue fall yesterday?"],
            "My Business": ["Which product is making the most money?", "Why did my profit change?",
                            f"Should I buy more {pl} ingredients?", "Will I run out of cash?"],
            "Decision Lab": [f"What if I increase {pl} prices by 5%?", "What if supplier costs rise 8%?",
                             "Why would a price increase lower demand?", "What should I do?"],
            "Local Market": [f"How do my {pl} prices compare with nearby competitors?",
                             f"What if I increase {pl} prices by 5%?"]}.get(page, [])


def _ask(q: str) -> None:
    agent: DecisionAgent = st.session_state["agent"]
    ctx = {"page": page, "product": current_product()}
    st.session_state["chat"].append({"role": "user", "text": q})
    st.session_state["chat_open"] = True
    if agent.mode == "offline":
        try:
            _append_reply(agent.ask(q, ctx))
        except Exception as exc:  # noqa: BLE001 - shown to the user, never silent
            st.session_state["chat"].append({"role": "assistant", "text": f"Sorry, something went wrong: {exc}",
                                             "error": True})
    else:  # a language model may be involved: run in the background so the dashboard stays usable
        fut = _executor().submit(agent.ask, q, ctx)
        try:  # most answers (fast facts, engine + one short explanation) finish within this wait
            _append_reply(fut.result(timeout=float(os.getenv("MSRD_CHAT_INLINE_WAIT_S", "3"))))
        except TimeoutError:
            st.session_state["pending"] = {"future": fut, "q": q, "t0": time.time()}
        except Exception as exc:  # noqa: BLE001 - shown to the user, never silent
            st.session_state["chat"].append({"role": "assistant", "text": f"Sorry, something went wrong: {exc}",
                                             "error": True})


def _append_reply(reply) -> None:
    links, seen = [], set()
    for c in reply.tool_calls:
        if "output" not in c or c["name"] not in TOOL_LINKS or c["name"] in seen:
            continue
        seen.add(c["name"])
        label, page_, fn = TOOL_LINKS[c["name"]]
        links.append((label, page_, fn(c["output"], c.get("input", {}))))
    text = re.sub(r"^Offline analyst(?: \(rule-based, no language model\))?\.\s*", "", reply.text)
    meta = "Every number checked against your data ✓" if reply.grounded else "⚠ Some numbers could not be checked"
    st.session_state["chat"].append({"role": "assistant", "text": text, "meta": meta, "links": links[:2],
                                     "grounded": reply.grounded})


def _toggle_chat() -> None:
    st.session_state["chat_open"] = not st.session_state.get("chat_open", False)


def analyst_panel() -> None:
    agent: DecisionAgent = st.session_state["agent"]
    pending = st.session_state.get("pending")
    chat = st.session_state["chat"]
    mode = {"gemini": "Gemini", "claude": "Claude", "offline": "Offline mode"}.get(agent.mode, agent.mode)
    head = st.columns([6, 1], vertical_alignment="center")
    head[0].markdown(f'<div class="chat-head"><div class="chat-avatar">AI</div><div><div class="chat-name">Financial analyst</div>'
                     f'<div class="chat-sub">Knows {T.esc(biz_name())} · {T.esc(mode)}</div></div></div>',
                     unsafe_allow_html=True)
    with head[1], st.container(key="chat_toggle_close"):
        st.button("✕", key="chat_toggle", on_click=_toggle_chat, help="Close")
    if getattr(agent, "last_fallback", None):
        st.caption("The AI model was unavailable, so the last answer came from offline mode.")
    if not chat:
        st.markdown(f'<div class="chat-empty">How can I help with {T.esc(biz_name())} today?</div>'
                    '<div class="soft">I answer from your own numbers and can test changes before you make them.</div>',
                    unsafe_allow_html=True)
        for j, q in enumerate(suggestions()):
            if st.button(q, key=f"suggest_{j}", width="stretch", disabled=bool(pending)):
                _ask(q)
                st.rerun()
    else:
        with st.container(key="chat_box"):
            for i in range(len(chat) - 1, -1, -1):  # newest first; the box shows them bottom-up
                m = chat[i]
                with st.chat_message(m["role"], avatar=":material/person:" if m["role"] == "user" else ":material/auto_awesome:"):
                    if m.get("error"):
                        st.error(m["text"])
                    else:
                        st.markdown(m["text"])
                    for j, (label, page_, kw) in enumerate(m.get("links", [])):
                        st.button(f"{label} →", key=f"chatlink_{i}_{j}", on_click=go, args=(page_,), kwargs=kw)
                    if m.get("meta"):
                        st.caption(m["meta"])

    @st.fragment(run_every="1s" if pending else None)
    def waiting():
        p = st.session_state.get("pending")
        if not p:
            return
        if p["future"].done():
            try:
                _append_reply(p["future"].result())
            except Exception as exc:  # noqa: BLE001 - shown to the user, never silent
                st.session_state["chat"].append({"role": "assistant", "text": f"Sorry, something went wrong: {exc}",
                                                 "error": True})
            st.session_state["pending"] = None
            st.rerun()
        with st.spinner(f"Working on it ({time.time() - p['t0']:.0f} s). You can keep using the dashboard."):
            time.sleep(0.1)
    waiting()
    q = st.chat_input("Ask about your business…", key="chat_in", disabled=bool(pending))
    if q:
        _ask(q)
        st.rerun()
    if chat and st.button("New conversation", key="chat_clear", disabled=bool(pending)):
        st.session_state["chat"] = []
        st.session_state["agent"] = DecisionAgent(s)
        st.rerun()


def floating_chat() -> None:
    if st.session_state.get("chat_open", False):
        st.markdown("<style>@media (min-width: 1500px) { [data-testid='stMainBlockContainer'] "
                    "{ padding-right: 470px !important; } }</style>", unsafe_allow_html=True)
        with st.container(key="chat_panel"):
            analyst_panel()
    else:
        with st.container(key="chat_fab"):
            st.button("✦  Thinking…" if st.session_state.get("pending") else "✦  Ask AI", key="chat_toggle",
                      on_click=_toggle_chat)


# ================================================================================ OVERVIEW
def briefing_sentence() -> str:
    y = b["yesterday"]
    rc, gc, wd = y["revenue_change_pct"], y["guests_change_pct"], b["weekday"]
    if rc is None:
        first = f"Here is your business on {wd}."
    elif rc <= -10:
        first = (f"Sales were lower yesterday, <b>{abs(rc):.0f}% below</b> a usual {wd}"
                 + (", mainly because fewer customers came in." if gc is not None and gc <= -10 else "."))
    elif rc >= 10:
        first = (f"Sales were strong yesterday, <b>{rc:.0f}% above</b> a usual {wd}"
                 + (", with more customers than usual." if gc is not None and gc >= 10 else "."))
    else:
        first = f"Yesterday was a normal {wd} for sales."
    parts = []
    under = len(b["underperforming"])
    if under:
        parts.append(f"{NUM_WORDS.get(under, str(under))} item{'s' if under > 1 else ''} sold less than usual")
    kinds = {a["kind"]: a for a in b["alerts"]}
    if "low_stock" in kinds or "stockout_risk" in kinds:
        parts.append("something may run short before the next delivery")
    if "cost_increase" in kinds:
        parts.append("ingredients are taking a bigger share of sales")
    if "cash" in kinds:
        parts.append("cash may not cover this week's bills")
    elif b["bills_due_7d"]:
        n = len(b["bills_due_7d"])
        parts.append(f"{NUM_WORDS.get(n, str(n)).lower()} bill{'s' if n > 1 else ''} "
                     f"({T.money(b['bills_due_7d_total'])}) {'are' if n > 1 else 'is'} due this week")
    if not parts:
        return first + " Nothing else needs your attention."
    second = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + ", and " + parts[-1]
    return f"{first} {second[0].upper() + second[1:]}."


def alert_actions(a: dict) -> list[tuple[str, str, dict]]:
    """Owner actions for one alert: (label, page, preset widgets). Investigate opens the evidence;
    Simulate decision opens the Decision Lab on the matching change; Review opens obligations."""
    prod, kind = a.get("product"), a["kind"]
    inv = {"investigate": {k: a.get(k) for k in ("kind", "title", "detail", "product")}}
    if kind == "product_mix" and prod:
        return [("Investigate", "My Business", {"seg_mb_section": "Products", "prod_pick": prod, "prod_view": True} | inv),
                ("Simulate decision", "Decision Lab", lab_link(prod, "Change a price"))]
    if kind in ("low_stock", "stockout_risk"):
        return [("Simulate decision", "Decision Lab", lab_link(prod, "Order more or less", sl_supply=25.0)),
                ("Investigate", "My Business", {"seg_mb_section": "Stock"})]
    if kind == "cost_increase":
        return [("Simulate decision", "Decision Lab", lab_link(None, "Supplier prices change")),
                ("Investigate", "My Business", {"seg_mb_section": "Costs"})]
    if kind in ("cash", "bills"):
        return [("Review", "My Business", {"seg_mb_section": "Cash & bills"})]
    return [("Investigate", "My Business", {"seg_mb_section": "Sales & profit"} | inv)]


def attention_bar() -> None:
    alerts = b["alerts"]
    if not alerts:
        with st.container(key="card_attention_calm"):
            st.markdown('<div style="display:flex;gap:14px;align-items:center"><div class="attn-icon">✓</div><div>'
                        '<div class="attn-head">Nothing needs your attention today.</div>'
                        '<div class="attn-sub">Sales, stock, costs and bills all look normal.</div></div></div>',
                        unsafe_allow_html=True)
        return
    top = alerts[0]
    more = alerts[1:3]
    with st.container(key="card_attention"):
        row = st.columns([6.2, 2.4], vertical_alignment="center")
        row[0].markdown(f'<div style="display:flex;gap:14px;align-items:flex-start"><div class="attn-icon">!</div><div>'
                        f'<div class="eyebrow" style="color:#5B4A1A">Business attention · {T.esc(b["weekday"])} '
                        f'{pd.Timestamp(b["date"]):%b %d}</div>'
                        f'<div class="attn-head">{T.esc(top["title"])}.</div>'
                        f'<div class="attn-sub">{T.esc(top["detail"])} {T.esc(top["next_step"])}</div></div></div>',
                        unsafe_allow_html=True)
        with row[1]:
            for j, (label, page_, kw) in enumerate(alert_actions(top)):
                st.button(label, key=("act_" if j == 0 else "act2_") + f"attn_{j}", on_click=go, args=(page_,),
                          kwargs=kw, width="stretch")
        for j, x in enumerate(more, start=1):
            r = st.columns([6.2, 2.4], vertical_alignment="center")
            r[0].markdown(f'<div class="attn-sub" style="padding-left:52px">Also: <b>{T.esc(x["title"])}</b> · '
                          f'{T.esc(x["detail"])}</div>', unsafe_allow_html=True)
            label, page_, kw = alert_actions(x)[0]
            r[1].button(f"{label} →", key=f"go_alert_{j}", on_click=go, args=(page_,), kwargs=kw)


def page_overview() -> None:
    hour = datetime.now().hour
    hello = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    head = st.columns([5, 1.1], vertical_alignment="bottom")
    head[0].markdown(f'<div class="eyebrow">{T.esc(biz_name())} · daily briefing</div>'
                     f'<div class="hero">{hello}. {briefing_sentence()}</div>', unsafe_allow_html=True)
    with head[1]:
        how(b["note"] + " We compare yesterday with the average of the same weekday over the past four weeks.",
            "About the briefing")
    attention_bar()

    k = dash.kpis(s, "mtd")
    rev, prof, exp, c, inv, li = (k["revenue"], k["net_profit"], k["expenses"], k["cash"], k["inventory"],
                                  k["liabilities"])
    pc = dash.profit_change(s, 28)
    costs_up = sorted([d for d in pc["drivers"] if d["driver"] != "Revenue" and d["profit_effect"] < -1],
                      key=lambda d: d["profit_effect"])
    bills = b.get("bills_due_7d_total") or 0.0
    section("How you're doing", f"This month so far ({k['period_label']}) compared with the same days last month")
    r1 = st.columns(3, gap="medium")
    with r1[0], st.container(key="kpi_revenue"):
        st.markdown(T.kpi_card("Revenue", T.money(rev["value"]), delta_html=T.delta(rev["change_pct"]),
                               sub="vs same days last month",
                               tooltip=f"All sales recorded {k['period_label']}, compared with {k['comparison_label']}."),
                    unsafe_allow_html=True)
        st.altair_chart(charts.sparkline(pd.DataFrame(rev["spark"]).assign(
            date=lambda d: pd.to_datetime(d["date"])), "revenue", color="#9DB2FF", background="transparent"),
            width="stretch")
        st.button("Revenue details →", key="go_revenue", on_click=go, args=("My Business",),
                  kwargs={"seg_mb_section": "Sales & profit"})
    tint = "__tpos" if (prof["value"] or 0) > 0 and prof["margin_pct"] >= prof["previous_margin_pct"] else \
        "__tneg" if (prof["value"] or 0) < 0 else ""
    with r1[1], st.container(key="kpi_profit" + tint):
        st.markdown(T.kpi_card("Operating profit", T.money(prof["value"]), delta_html=T.delta(prof["change_pct"]),
                               sub=f"{prof['margin_pct']:.1f}% of sales (was {prof['previous_margin_pct']:.1f}%)",
                               tooltip="Sales minus all costs (ingredients, staff, rent, everything else), before taxes "
                                       "and loan repayments."), unsafe_allow_html=True)
        st.button("What drives profit →", key="go_profit", on_click=go, args=("My Business",),
                  kwargs={"seg_mb_section": "Costs"})
    ok = c["value"] is not None and c["value"] >= bills
    with r1[2], st.container(key="kpi_cash" + ("__tblue" if ok else "__tneg")):
        st.markdown(T.kpi_card("Cash available", T.money(c["value"]),
                               delta_html=T.status("Covers this week's bills" if ok else "Below this week's bills",
                                                   "pos" if ok else "neg"),
                               sub="<br>runway not a concern: cash flow is positive" if c.get("runway_days") is None
                               else f"<br>about {c['runway_days']:.0f} days of runway",
                               tooltip="Cash in your records at the end of yesterday: opening balance plus all money in "
                                       "and out (including owner draws)."), unsafe_allow_html=True)
        st.button("Cash outlook →", key="go_cash", on_click=go, args=("My Business",),
                  kwargs={"seg_mb_section": "Cash & bills"})
    r2 = st.columns(3, gap="medium")
    up = costs_up[0] if costs_up else None
    with r2[0], st.container(key="kpi_expenses" + ("__twarn" if (exp["change_pct"] or 0) > 5 else "")):
        st.markdown(T.kpi_card("Expenses", T.money(exp["value"]), delta_html=T.delta(exp["change_pct"], good_when_up=False),
                               sub=(f"Rising most: {up['driver'].replace('Ingredients: ', '').lower()} "
                                    f"(+{T.money(-up['profit_effect'])} in 4 weeks)" if up else "No cost rose notably"),
                               tooltip="All costs recorded this month. 'Rising most' compares the last 4 weeks with the "
                                       "4 weeks before."), unsafe_allow_html=True)
        st.button("Expense breakdown →", key="go_expenses", on_click=go, args=("My Business",),
                  kwargs={"seg_mb_section": "Costs"})
    low = inv.get("low_stock") or []
    risky = []
    if product_ctx() is not None:
        t = dash.product_table(s, "28d")
        risky = list(t[t["stockout_risk_7d"] >= 0.25]["product"])
    itint = "__tneg" if low else "__twarn" if risky else "__tpos"
    with r2[1], st.container(key="kpi_inventory" + itint):
        state = (T.status(f"{len(low)} ingredient(s) low", "neg") if low else
                 T.status(f"{risky[0]} may sell out", "warn") if risky else T.status("Healthy", "pos"))
        nd = inv.get("next_delivery_in_days")
        st.markdown(T.kpi_card("Inventory health", T.money(inv["value"]), delta_html=state,
                               sub=f"<br>on hand · next delivery in {nd} days" if nd else "<br>on hand",
                               tooltip="Ingredients on hand at today's cost. 'Healthy' means every item should last until "
                                       "the next delivery at the expected pace."), unsafe_allow_html=True)
        st.button("Stock levels →", key="go_inventory", on_click=go, args=("My Business",),
                  kwargs={"seg_mb_section": "Stock"})
    with r2[2], st.container(key="kpi_bills" + ("__twarn" if not ok else "")):
        if li.get("payables") is None:
            st.markdown(T.kpi_card("Upcoming obligations", "Not in data"), unsafe_allow_html=True)
        else:
            n_b = len(b["bills_due_7d"])
            st.markdown(T.kpi_card("Upcoming obligations", T.money(bills),
                                   delta_html=T.status(f"{n_b} bill{'s' if n_b != 1 else ''} due in 7 days", "neutral"),
                                   sub=f"<br>{T.money(li['due_14d'])} due within 2 weeks incl. supplier invoices",
                                   tooltip="Scheduled bills from your bill history plus unpaid supplier invoices. Loan "
                                           "principal is not in the data."), unsafe_allow_html=True)
        st.button("Bills →", key="go_liabilities", on_click=go, args=("My Business",),
                  kwargs={"seg_mb_section": "Cash & bills"})

    left, right = st.columns([1.7, 1], gap="medium")
    with left, st.container(key="panel_trend"):
        hd = st.columns([2, 2.4], vertical_alignment="center")
        hd[0].markdown('<div class="section-title" style="margin-top:0">Revenue & profit</div>', unsafe_allow_html=True)
        with hd[1]:
            win = seg("Window", ["30 days", "3 months", "12 months"], "seg_ov_window", "3 months")
        days = {"30 days": 30, "3 months": 91, "12 months": 365}[win]
        h = s.history.copy()
        h["profit"] = h["revenue"] - h["variable_costs"] - h["fixed_costs"]
        st.altair_chart(charts.trend(h[h["date"] > h["date"].max() - pd.Timedelta(days=days)],
                                     weekly=days > 31, height=260), width="stretch")
        st.markdown('<span class="soft"><span style="color:#2346D8">━</span> Revenue &nbsp; '
                    '<span style="color:#F26A1B">━</span> Operating profit &nbsp;· '
                    + ("weekly totals" if days > 31 else "daily") + "</span>", unsafe_allow_html=True)
    with right, st.container(key="panel_products"):
        ctx = product_ctx()
        if ctx is None:
            section("Where sales come from")
            st.markdown('<div class="plain">Item-level sales appear when you connect item data.</div>',
                        unsafe_allow_html=True)
        else:
            section("Product performance", "Revenue, last 4 weeks")
            t = dash.product_table(s, "28d")
            other = float(dash._slice(ctx.history.recon, *_bounds28())["other_items_revenue"].sum())
            df = pd.concat([t[["product", "revenue", "units_sold"]],
                            pd.DataFrame([{"product": "Other items", "revenue": other, "units_sold": np.nan}])])
            df["share_pct"] = df["revenue"] / df["revenue"].sum() * 100
            df["label"] = [f"{u:,.0f} sold" if pd.notna(u) else "sides, add-ons" for u in df["units_sold"]]
            st.altair_chart(charts.product_revenue(df), width="stretch")
            st.button("All products →", key="go_all", on_click=go, args=("My Business",),
                      kwargs={"seg_mb_section": "Products", "prod_view": False})

    a1, a2 = st.columns([1, 1.15], gap="medium")
    with a1, st.container(key="panel_costs"):
        section("Expense breakdown", "This month")
        e = dash.expense_breakdown(s, "mtd")
        e["pct_label"] = e["share_pct"].map(lambda v: f"{v:.0f}%")
        st.altair_chart(charts.expense_bars(e), width="stretch")
    with a2, st.container(key="card_decision"):
        opportunity_card()


def _bounds28():
    w = dash.window(s.history, "28d")
    return w.start, w.end


def plain_action(a: dict) -> str:
    sc = a.get("scenario") or {}
    name = a["product"].lower()
    if sc.get("price_change_pct"):
        p = sc["price_change_pct"]
        return f"{'Raise' if p > 0 else 'Lower'} {name} prices by {abs(p):g}%"
    if sc.get("supply_change_pct"):
        p = sc["supply_change_pct"]
        return f"Order {'more' if p > 0 else 'less'} for {name} ({abs(p):g}%)"
    return a["action"]


def impact_block(change: float, lo: float, hi: float, p: float, h: int, label: str = "Expected profit") -> None:
    sign = "pos" if change > 0.5 else "neg" if change < -0.5 else ""
    st.markdown(f'<div class="eyebrow">{T.esc(label)} · {weeks(h)}</div>'
                f'<div class="big-number {sign}">{T.signed_money(change)}</div>'
                f'<div class="plain">{T.esc(T.confidence_words(p, lo, hi))}</div>'
                + T.range_bar(lo, change, hi), unsafe_allow_html=True)


def estimate_note(lo: float, hi: float, p: float) -> str:
    return (f"We replayed the coming weeks many times using your sales history: how much our past forecasts "
            f"missed by, and a range of possible customer reactions. **9 in 10 runs** landed between "
            f"**{T.signed_money(lo)}** and **{T.signed_money(hi)}**; the change beat doing nothing in "
            f"**{p:.0%}** of them. The ranges were checked against past weeks the forecasts had not seen. "
            "How customers react to price is our estimate, not measured from your sales. Not a guarantee.")


def opportunity_card() -> None:
    a = b.get("action")
    st.markdown('<div class="eyebrow brand">Recommended decision to review</div>', unsafe_allow_html=True)
    if a is None or not a.get("impact"):
        st.markdown('<div class="idea">Test a change before you make it</div><div class="plain">Prices, orders, '
                    'supplier costs or a stock budget: see the likely effect on profit and cash first.</div>',
                    unsafe_allow_html=True)
        st.button("Open the Decision Lab", key="cta_lab", on_click=go, args=("Decision Lab",))
        return
    i = a["impact"]
    st.markdown(f'<div class="idea">{T.esc(plain_action(a))}</div>', unsafe_allow_html=True)
    impact_block(i["profit_change"], i["range_90"][0], i["range_90"][1], i["probability_better_pct"] / 100,
                 i["horizon_days"])
    st.markdown(f'<div class="soft"><b>Why:</b> {T.esc(a["why"])}</div>'
                + (f'<div class="soft"><b>Key risk:</b> {T.esc(a["risks"][-1] if len(a["risks"]) > 1 else a["risks"][0])}</div>'
                   if a.get("risks") else ""), unsafe_allow_html=True)
    sc = a["scenario"]
    b1, b2 = st.columns([1.5, 1], vertical_alignment="center")
    b1.button("Explore in Decision Lab", key="cta_brief_lab", on_click=go, args=("Decision Lab",),
              kwargs=lab_link(a["product"], "Change a price" if sc["price_change_pct"] else "Order more or less",
                              sl_price=sc["price_change_pct"], sl_supply=sc["supply_change_pct"]))
    with b2:
        how(estimate_note(i["range_90"][0], i["range_90"][1], i["probability_better_pct"] / 100))
    with st.expander("Before you decide"):
        checklist(a)


def checklist(a: dict, key: str | None = None) -> None:
    """Approval-first checklist: what could go wrong, what to check, what we assumed."""
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
    st.markdown("**Needs your approval.** Nothing changes automatically: if you go ahead, you make the change "
                "yourself in your menu, POS or supplier order.")


# ================================================================================ MY BUSINESS
def page_my_business() -> None:
    st.markdown("# My Business")
    sec = seg("Section", MB_SECTIONS, "seg_mb_section", "Sales & profit")
    ctx = product_ctx()
    stop = pd.Timestamp(s.history["date"].max())
    start = stop - pd.Timedelta(days=89)
    {"Sales & profit": lambda: section_sales(ctx, stop), "Products": lambda: section_products(ctx),
     "Costs": section_costs, "Stock": lambda: section_stock(ctx, start, stop),
     "Cash & bills": lambda: section_cash(stop), "Outlook": section_outlook}[sec]()


def section_sales(ctx, stop) -> None:
    inv_ = st.session_state.get("investigate") or {}
    if inv_.get("kind") == "sales_decline":
        y = b["yesterday"]
        with st.container(key="card_investigate_sales"):
            st.markdown(f'<div class="eyebrow" style="color:#5B4A1A">Why this was flagged</div>'
                        f'<div class="idea">{T.esc(inv_["title"])}</div>'
                        f'<div class="plain">{T.esc(inv_["detail"])}</div>', unsafe_allow_html=True)
            rows = [(p["product"], f"{p['units_typical']:.0f}", f"{p['units_sold']}",
                     T.delta(p["change_pct"]) if p["change_pct"] is not None else "") for p in b["products"]]
            if rows:
                st.markdown(T.before_after(rows).replace("Today&#x27;s plan", "Usual " + T.esc(b["weekday"]))
                            .replace("Today's plan", "Usual " + T.esc(b["weekday"]))
                            .replace("With your change", "Yesterday"), unsafe_allow_html=True)
            st.markdown(f'<div class="soft">Customers yesterday: {y["guests"]} (usual about {y["guests_typical"]:.0f}). '
                        'This shows what changed, not why: weather, events and staffing are not in the data.</div>',
                        unsafe_allow_html=True)
            c1, c2, _ = st.columns([1.2, 1, 3])
            c1.button("Clear", key="act2_inv_clear", on_click=lambda: st.session_state.update(investigate=None))
            if c2.button("Ask AI", key="act2_inv_sales_ask"):
                _ask("Why did revenue fall yesterday?")
                st.rerun()
    span = seg("Range", [30, 90, 365], "seg_mb_range", 90, lambda d: {30: "30 days", 90: "90 days", 365: "12 months"}[d])
    start = stop - pd.Timedelta(days=int(span) - 1)
    h = s.history.copy()
    h["profit"] = h["revenue"] - h["variable_costs"] - h["fixed_costs"]
    vol = "Customers" if s.industry == "restaurant" else "Items sold"
    metrics = {"Sales": ("revenue", "sum", "$"), "Profit": ("profit", "sum", "$")}
    if "units_sold" in h:
        metrics[vol] = ("units_sold", "sum", "#")
        metrics["Spend per customer" if s.industry == "restaurant" else "Sales per item"] = ("per", "ratio", "$2")
    if ctx is not None:
        rc = ctx.history.recon.set_index("date")["food_cost"]
        h["gross_profit"] = h["revenue"] - h["date"].map(rc).fillna(0)
        metrics["After ingredients"] = ("gross_profit", "sum", "$")
    days = (stop - start).days + 1
    cur = dash._slice(h, start, stop)
    prev = dash._slice(h, start - pd.Timedelta(days=days), start - pd.Timedelta(days=1))

    def value(frame, col, how_):
        if how_ == "ratio":
            return frame["revenue"].sum() / frame["units_sold"].sum() if frame["units_sold"].sum() else None
        return frame[col].sum()

    def fmt(v, kind):
        return "n/a" if v is None else T.money(v, 2) if kind == "$2" else T.money(v) if kind == "$" else f"{v:,.0f}"

    names = list(metrics)
    cols = st.columns(len(names), gap="small")
    for j, (col, n) in enumerate(zip(cols, names)):
        c_, how_, kind = metrics[n]
        v, pv = value(cur, c_, how_), value(prev, c_, how_)
        chg = (v - pv) / abs(pv) * 100 if v is not None and pv else None
        with col, st.container(key=f"kpi_mb_{j}"):
            st.markdown(T.kpi_card(n, fmt(v, kind), delta_html=T.delta(chg), sub="vs the period before"),
                        unsafe_allow_html=True)
    with st.container(key="panel_metric"):
        head = st.columns([1.3, 3], vertical_alignment="center")
        head[0].markdown('<div class="section-title" style="margin-top:0">Trend</div>', unsafe_allow_html=True)
        with head[1]:
            pick = seg("Metric", names, "seg_mb_metric", "Sales")
        col, how_, kind = metrics[pick]
        d = cur[["date"]].copy()
        d["v"] = (cur["revenue"] / cur["units_sold"].replace(0, np.nan)) if how_ == "ratio" else cur[col]
        d["avg7"] = d["v"].rolling(7, min_periods=1).mean()
        st.markdown(f"#### {pick}")
        yf = "$,.2f" if kind == "$2" else "$,.0f" if kind == "$" else ",.0f"
        st.altair_chart(charts.line_hover(d, {"Each day": "v", "Weekly average": "avg7"},
                                          colors={"Each day": "#CFCDC6", "Weekly average": T.COBALT},
                                          height=280, y_format=yf, zero_rule=pick == "Profit"), width="stretch")
    if ctx is not None:
        left, right = st.columns([1.6, 1], gap="medium")
        with left, st.container(key="panel_contrib"):
            section("Sales by item", "Weekly")
            w = views.weekly_product_revenue(ctx.history.sales, ctx.history.recon, start, stop, None)
            st.altair_chart(charts.stacked_area(w), width="stretch")
        with right, st.container(key="panel_mix"):
            section("Revenue breakdown", f"Last {span} days" if span < 365 else "Last 12 months")
            sc = dash._slice(ctx.history.sales, start, stop)
            other = float(dash._slice(ctx.history.recon, start, stop)["other_items_revenue"].sum())
            g = sc.groupby("product").agg(revenue=("revenue", "sum")).reset_index()
            g = pd.concat([g, pd.DataFrame([{"product": "Other items", "revenue": other}])])
            g["share"] = g["revenue"] / g["revenue"].sum() * 100
            g = g.sort_values("revenue", ascending=False)
            st.dataframe(g, hide_index=True, width="stretch", column_config={
                "product": "Item", "revenue": st.column_config.NumberColumn("Sales", format="$%.0f"),
                "share": st.column_config.ProgressColumn("Share", format="%.0f%%", min_value=0, max_value=100)})


def section_products(ctx) -> None:
    if ctx is None:
        return empty_state("Item-level numbers are not available for this business.")
    t = dash.product_table(s, "28d")
    st.session_state.setdefault("prod_pick", t.iloc[0]["product"])
    if st.session_state.get("prod_view"):
        return product_detail(ctx, t)
    st.markdown('<div class="soft">Last 4 weeks. Select an item for its detail view.</div>', unsafe_allow_html=True)
    for chunk in (t.iloc[:3], t.iloc[3:]):
        cols = st.columns(3, gap="medium")
        for col, (_, p) in zip(cols, chunk.iterrows()):
            keep = p["price"] - p["unit_cost"]
            stat = (T.status("May sell out this week", "warn") if p["stockout_risk_7d"] >= 0.25
                    else T.status("Stock OK", "pos"))
            with col, st.container(key=f"card_prod_{p['product_id']}"):
                st.markdown(f'<div style="display:flex;justify-content:space-between;align-items:center">'
                            f'<div class="prod-name">{T.esc(p["product"])}</div>{stat}</div>'
                            f'<div class="prod-price">{T.money(p["price"], 2)}</div>'
                            f'<div class="plain">You keep <b>{T.money(keep, 2)}</b> per order after ingredients '
                            f'({p["gross_margin_pct"]:.0f}%)</div>'
                            f'<div class="soft">{p["units_sold"]:,.0f} sold · {T.money(p["revenue"])} in sales · '
                            f'~{p["forecast_demand_7d"]:,.0f} orders expected next week</div>', unsafe_allow_html=True)
                st.button("View details →", key=f"go_prod_{p['product_id']}", on_click=go, args=("My Business",),
                          kwargs={"prod_pick": p["product"], "seg_mb_section": "Products", "prod_view": True})
    section("All items")
    show = t.assign(stockout_risk_7d=t["stockout_risk_7d"] * 100)[
        ["product", "price", "unit_cost", "gross_margin_pct", "units_sold", "revenue", "forecast_demand_7d",
         "days_of_supply", "stockout_risk_7d"]]
    ev = st.dataframe(show, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                      key="prod_table", column_config={
                          "product": "Item", "price": st.column_config.NumberColumn("Price", format="$%.2f"),
                          "unit_cost": st.column_config.NumberColumn("Ingredients", format="$%.2f"),
                          "gross_margin_pct": st.column_config.ProgressColumn("Margin", format="%.0f%%", min_value=0, max_value=100),
                          "units_sold": st.column_config.NumberColumn("Sold (4 wks)", format="%d"),
                          "revenue": st.column_config.NumberColumn("Sales (4 wks)", format="$%.0f"),
                          "forecast_demand_7d": st.column_config.NumberColumn("Expected next week", format="%.0f"),
                          "days_of_supply": st.column_config.NumberColumn("Stock lasts (days)", format="%.1f"),
                          "stockout_risk_7d": st.column_config.NumberColumn("Sell-out risk", format="%.0f%%")})
    rows = (ev.selection.rows if ev is not None and hasattr(ev, "selection") else []) or []
    if rows:
        st.session_state["prod_pick"] = t.iloc[rows[0]]["product"]
        st.session_state["prod_view"] = True
        st.rerun()


def product_detail(ctx, table: pd.DataFrame) -> None:
    from decisiondesk.decision.product_scenario import baseline
    from decisiondesk.products import BOM, INGREDIENTS, PRODUCT_IDS
    top = st.columns([1.2, 2, 3], vertical_alignment="center")
    top[0].button("← All products", key="prod_back",
                  on_click=lambda: st.session_state.update(prod_view=False, investigate=None))
    name = top[1].selectbox("Item", list(views.NAMES.values()), key="prod_pick", label_visibility="collapsed")
    pid = next(k for k, v in views.NAMES.items() if v == name)
    i = PRODUCT_IDS.index(pid)
    row = table.set_index("product").loc[name]
    b28 = baseline(ctx, 28, pid)
    so = views.stockout_date(b28, pid)
    st.markdown(f"## {name}")
    inv_ = st.session_state.get("investigate") or {}
    if inv_.get("product") == name:
        investigate_panel(ctx, name, inv_)
    cards = [("Price", T.money(row["price"], 2), "", "Current menu price."),
             ("Kept per order", T.money(row["price"] - row["unit_cost"], 2), f"{row['gross_margin_pct']:.0f}% margin",
              "Price minus ingredient cost, before staff, rent and other costs."),
             ("Sold, last 4 weeks", f"{row['units_sold']:,.0f}", T.money(row["revenue"]) + " in sales", ""),
             ("Expected next week", f"{row['forecast_demand_7d']:,.0f} orders", "forecast from your sales history",
              "Forecast based on your historical sales, by day of the week and season."),
             ("Stock", f"{row['sellable_units']:,.0f} portions", f"lasts about {row['days_of_supply']:.1f} days"
              + (f" · may run out {so[5:]}" if so else ""), "What the ingredients on hand can make before the next delivery.")]
    cols = st.columns(5, gap="small")
    for j, (col, (lab_, v, sub, tt)) in enumerate(zip(cols, cards)):
        with col, st.container(key=f"kpi_pd_{j}"):
            st.markdown(T.kpi_card(lab_, v, sub=sub, tooltip=tt), unsafe_allow_html=True)
    with st.container(key="panel_pd"):
        section("Expected orders vs what you can make", "Next 4 weeks, with your usual Monday and Thursday deliveries")
        st.altair_chart(charts.demand_supply(views.demand_supply_frame(b28, pid), title=""), width="stretch")
        st.markdown('<div class="soft">Blue: orders we expect. Orange: what your stock lets you make. Red marks: days '
                    'it might run short.</div>', unsafe_allow_html=True)
    s_ = ctx.history.sales
    ps = s_[s_["product_id"] == pid].tail(120)
    left, right = st.columns(2, gap="medium")
    with left, st.container(key="panel_pd_hist"):
        section("Recent sales", "Last 120 days")
        st.altair_chart(charts.sales_demand_history(ps[["date", "units_sold", "demand_est"]]), width="stretch")
    with right, st.container(key="panel_pd_price"):
        section("Price and ingredient cost")
        st.altair_chart(charts.price_history(s_[s_["product_id"] == pid][["date", "price", "unit_cost"]]),
                        width="stretch")
    with st.expander("Ingredients this item uses"):
        use = pd.DataFrame({"ingredient": [k.name for k in INGREDIENTS], "per order": BOM[i],
                            "next 7 days": BOM[i] * row["forecast_demand_7d"], "unit": [k.unit for k in INGREDIENTS]})
        st.dataframe(use[use["per order"] > 0].round(2), hide_index=True, width="stretch")
    b1, b2, _ = st.columns([1.3, 1.3, 3])
    b1.button(f"Test a {name.lower()} price change", key="cta_pd_lab", on_click=go, args=("Decision Lab",),
              kwargs=lab_link(name))
    if b2.button(f"Ask AI about {name.lower()}", key="pd_ask"):
        _ask(f"Should I buy more {name.lower()} ingredients?")
        st.rerun()


def investigate_panel(ctx, name: str, alert: dict) -> None:
    """Observe -> explain: what was flagged and what the records show, then Simulate / Ask."""
    sales = ctx.history.sales
    ps = sales[sales["product"] == name].tail(42).copy()
    last = ps["date"].max()
    same = ps[ps["date"].dt.dayofweek == last.dayofweek]
    with st.container(key="card_investigate"):
        st.markdown(f'<div class="eyebrow" style="color:#5B4A1A">Why this was flagged</div>'
                    f'<div class="idea">{T.esc(alert["title"])}</div><div class="plain">{T.esc(alert["detail"])}</div>',
                    unsafe_allow_html=True)
        d = ps[["date", "units_sold"]].rename(columns={"units_sold": "Orders sold"})
        d["Typical for that weekday"] = ps.groupby(ps["date"].dt.dayofweek)["units_sold"].transform(
            lambda x: x.shift(1).rolling(4, min_periods=1).mean())
        st.altair_chart(charts.line_hover(d, {"Orders sold": "Orders sold", "Typical for that weekday": "Typical for that weekday"},
                                          colors={"Orders sold": T.INK, "Typical for that weekday": "#B9B3A6"},
                                          dashed=("Typical for that weekday",), y_format=",.0f", height=200), width="stretch")
        st.markdown(f'<div class="soft">Last {len(same)} {last.day_name()}s: '
                    + ", ".join(f"{int(v)}" for v in same["units_sold"]) + ". This shows what changed, not why: "
                    "weather, events or a menu change are not in the data.</div>", unsafe_allow_html=True)
        c1, c2, c3, _ = st.columns([1.3, 1.3, 1.1, 1.6])
        c1.button("Simulate a price change", key="act_inv_price", on_click=go, args=("Decision Lab",),
                  kwargs=lab_link(name, "Change a price"))
        c2.button("Simulate ordering more", key="act2_inv_order", on_click=go, args=("Decision Lab",),
                  kwargs=lab_link(name, "Order more or less", sl_supply=25.0))
        if c3.button("Ask AI", key="act2_inv_ask"):
            _ask(f"Which product is making the most money, and how is {name.lower()} doing?")
            st.rerun()


def section_costs() -> None:
    period = seg("Period", ["mtd", "28d", "90d", "365d"], "seg_mb_exp", "28d", lambda k: dash.PERIODS[k])
    e = dash.expense_breakdown(s, period)
    e["pct_label"] = e["share_pct"].map(lambda v: f"{v:.0f}%")
    left, right = st.columns([1, 1], gap="medium")
    with left, st.container(key="panel_exp"):
        section("Where the money goes", dash.PERIODS[period])
        st.altair_chart(charts.expense_bars(e), width="stretch")
    pc = dash.profit_change(s, 28)
    with right, st.container(key="panel_drivers"):
        ch = pc["profit"]["change"]
        section("What changed your profit", f"Last 4 weeks: {T.money(pc['profit']['current'])}, "
                f"{T.signed_money(ch)} vs the 4 weeks before")
        dfd = pd.DataFrame(pc["drivers"]).head(8)
        dfd["abs_effect"] = dfd["profit_effect"].abs()
        st.altair_chart(charts.driver_bars(dfd), width="stretch")
        st.markdown('<div class="soft">Bars to the right helped profit; bars to the left reduced it.</div>',
                    unsafe_allow_html=True)


def section_stock(ctx, start, stop) -> None:
    inv = dash.inventory_kpi(s)
    if ctx is None:
        st.metric("Stock value", T.money(inv["value"]))
        return empty_state("Ingredient stock is not available for this business.")
    t = dash.product_table(s, "28d")
    nd = dash.next_delivery_days(ctx.origin)
    left, right = st.columns([1.4, 1], gap="medium")
    with left, st.container(key="panel_inv"):
        section("Will anything sell out before the next delivery?", f"Next delivery in {nd} days")
        st.altair_chart(charts.inventory_health(views.inventory_frame(t, nd)), width="stretch")
        st.markdown('<div class="soft">Bar: how many you can make from what is on the shelf. Black mark: orders '
                    'expected before the next delivery. Colour: how likely it is to sell out.</div>',
                    unsafe_allow_html=True)
    with right, st.container(key="panel_ingr_now"):
        section("Ingredients on hand", f"{ctx.origin:%b %d}")
        items = pd.DataFrame(inv["items"])
        cols = [c for c in ("ingredient", "on_hand", "unit", "value", "days_of_supply") if c in items]
        st.dataframe(items[cols].round(1) if cols else items, hide_index=True, width="stretch")
    led = ctx.history.ledger
    led = led[(led["date"] >= start) & (led["date"] <= stop)].copy()
    avg_use = led.groupby("ingredient")["used"].transform(lambda x: x.rolling(28, min_periods=7).mean())
    led["days_of_supply"] = led["closing"] / avg_use.replace(0, np.nan)
    with st.expander("Stock levels over time and recipes"):
        st.altair_chart(charts.ingredient_history(led.dropna(subset=["days_of_supply"])), width="stretch")
        from decisiondesk.products import BOM, INGREDIENTS
        st.dataframe(pd.DataFrame(BOM, index=list(views.NAMES.values()),
                                  columns=[f"{k.name} ({k.unit})" for k in INGREDIENTS]), width="stretch")


def section_cash(stop) -> None:
    h = s.history
    li = dash.liabilities(s)
    k = dash.kpis(s, "mtd")
    cols = st.columns(3, gap="small")
    with cols[0], st.container(key="kpi_cash_now"):
        st.markdown(T.kpi_card("Cash on hand", T.money(k["cash"]["value"])), unsafe_allow_html=True)
    if li.get("payables") is not None:
        with cols[1], st.container(key="kpi_bills14"):
            st.markdown(T.kpi_card("Bills, next 2 weeks", T.money(li["due_14d"])), unsafe_allow_html=True)
        with cols[2], st.container(key="kpi_payables"):
            st.markdown(T.kpi_card("Unpaid supplier invoices", T.money(li["payables"]),
                                   sub=f"paid {li['payment_terms_days']} days after delivery"), unsafe_allow_html=True)
    if "cash_balance" in h:
        with st.container(key="panel_cash"):
            section("Cash over the last 6 months")
            d = dash._slice(h, stop - pd.Timedelta(days=179), stop)[["date", "cash_balance"]]
            st.altair_chart(charts.line_hover(d, {"Cash": "cash_balance"}, colors={"Cash": T.COBALT}, height=250),
                            width="stretch")
    if li.get("payables") is not None:
        with st.container(key="panel_bills"):
            section("Upcoming bills")
            st.dataframe(pd.DataFrame(li["scheduled_bills"]), hide_index=True, width="stretch")
    with st.container(key="panel_cash_risk"):
        section("Will cash run short in the next 4 weeks?")
        if st.button("Check my cash outlook", key="mb_cash_risk"):
            st.session_state["mb_cash"] = run_tool(s, "assess_cash_risk", {"starting_cash": None})
        cr = st.session_state.get("mb_cash")
        if cr:
            if not cr["available"]:
                st.warning(f"We need your current cash balance for this: {cr['reason']}.")
            else:
                lo, hi = cr["ending_cash_80pct_interval"]
                st.markdown(f'<div class="plain">In 4 weeks you would most likely have about '
                            f'<b>{T.money(cr["median_ending_cash"])}</b> (usually between {T.money(lo)} and '
                            f'{T.money(hi)}). Chance of running short: <b>{T.esc(cr["simulated_shortfall_frequency"])}</b>.</div>',
                            unsafe_allow_html=True)


def section_outlook() -> None:
    h = s.history
    names = {"revenue": "Sales", "units_sold": "Customers" if s.industry == "restaurant" else "Items sold",
             "variable_costs": "Costs that move with sales", "net_cash_flow": "Cash in minus cash out"}
    targets = [t for t in names if t == "net_cash_flow" or t in h.columns]
    c1, _ = st.columns([1, 2])
    target = c1.selectbox("Look ahead at", targets, key="fc_target", format_func=names.get)
    with st.spinner("Looking ahead..."):
        fc = run_tool(s, "forecast_business", {"target": target})
    v = fc["interval_80_total_validation"]
    money = target != "units_sold"
    f = (lambda x: T.money(x)) if money else (lambda x: f"{x:,.0f}")
    lo, hi = fc["interval_80_total"]
    with st.container(key="panel_fc"):
        st.markdown(f'<div class="eyebrow">{T.esc(names[target])} · next 4 weeks</div>'
                    f'<div class="big-number">{f(fc["point_total"])}</div>'
                    f'<div class="plain">Most likely between {f(lo)} and {f(hi)}. Forecast based on your historical '
                    'business performance, by day of the week and season.</div>', unsafe_allow_html=True)
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
        how(f"We tested several forecasting approaches on past weeks they had not seen and use the most accurate. "
            f"In that test, {v['total_hits']} of {v['windows']} past 4-week totals fell inside the 'most likely' "
            f"range. Synthetic demo data: real accuracy is measured once your own records are connected.")
    if v.get("windows") and v.get("assessable") and v["total_coverage"] < 0.65:
        st.warning("This range may be too narrow: fewer past totals than expected fell inside it.")
    elif not v.get("windows"):
        st.warning("There is not enough history yet to check this range.")


# ================================================================================ LOCAL MARKET
def page_local_market() -> None:
    from decisiondesk import market as mk
    from decisiondesk.products import PRODUCT_IDS
    st.markdown("# Local Market")
    ctx = product_ctx()
    if ctx is None:
        return empty_state("Local price comparison needs your item prices.")
    if "market" not in st.session_state:
        st.session_state["market"] = mk.load_market()
    m = st.session_state["market"]
    today = date.today()
    fr = mk.freshness(m, today)
    checked = pd.Timestamp(fr["newest_verification"])
    st.markdown(f'{T.status("Prices up to date" if not fr["stale"] else "Prices need re-checking", "pos" if not fr["stale"] else "warn")}'
                f' <span class="soft">&nbsp;Checked {checked:%b %d, %Y} from {fr["restaurants_with_verified_prices"]} '
                f'Austin restaurants\' own menus · next check by {pd.Timestamp(fr["next_reverification_due"]):%b %d}</span>',
                unsafe_allow_html=True)
    ov = {pid: mk.compare(pid, float(ctx.prices[i]), market=m, as_of=today) for i, pid in enumerate(PRODUCT_IDS)}

    section("Your prices vs nearby", "Same serving only: for example, three single tacos count as one plate of three")
    with st.container(key="card_positions"):
        for n_, (pid, c) in enumerate(ov.items()):
            name = views.NAMES[pid]
            if n_:
                st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
            row = st.columns([1.2, 0.9, 3.2, 1.8, 1.3], vertical_alignment="center")
            row[0].markdown(f'<div class="row-title">{T.esc(name)}</div><div class="soft">{T.esc(c["our_serving"])}</div>',
                            unsafe_allow_html=True)
            row[1].markdown(f'<div class="prod-price" style="font-size:20px">${c["our_price"]:.2f}</div>', unsafe_allow_html=True)
            if c["sufficient"]:
                lo_, hi_ = min(c["peer_min"], c["our_price"]) * 0.92, max(c["peer_max"], c["our_price"]) * 1.06

                def x(v, lo_=lo_, hi_=hi_):
                    return (v - lo_) / (hi_ - lo_) * 100
                row[2].markdown(
                    f'<div class="pp"><div class="pp-range" style="left:{x(c["peer_min"]):.1f}%;width:{x(c["peer_max"]) - x(c["peer_min"]):.1f}%"></div>'
                    f'<div class="pp-med" style="left:{x(c["peer_median"]):.1f}%"></div>'
                    f'<div class="pp-you" style="left:{x(c["our_price"]):.1f}%"></div></div>'
                    f'<div class="soft">Nearby ${c["peer_min"]:.2f}–${c["peer_max"]:.2f} · middle ${c["peer_median"]:.2f} · '
                    f'{c["peer_count"]} restaurants</div>', unsafe_allow_html=True)
                d = c["difference_vs_median_pct"]
                label = ("Below every nearby price" if c["position"].startswith("below") else
                         "Above every nearby price" if c["position"].startswith("above") else
                         f"{abs(d):.0f}% {'below' if d < 0 else 'above'} the middle")
                row[3].markdown(T.status(label, "neutral"), unsafe_allow_html=True)
            else:
                row[2].markdown(f'<div class="soft">Not enough comparable menus yet ({c["peer_count"]} found).</div>',
                                unsafe_allow_html=True)
                row[3].markdown(T.status("No fair comparison", "neutral"), unsafe_allow_html=True)
            row[4].button("Test a price →", key=f"lm_test_{pid}", on_click=go, args=("Decision Lab",),
                          kwargs=lab_link(name))

    ideas = []
    for pid, c in ov.items():
        if not c["sufficient"]:
            continue
        d, name = c["difference_vs_median_pct"], views.NAMES[pid]
        if d <= -10:
            ideas.append((pid, f"Your {name.lower()} are priced {abs(d):.0f}% below the local middle.",
                          "Worth testing whether a small increase would change how many people order.", 5.0))
        elif d >= 10:
            ideas.append((pid, f"Your {name.lower()} are priced {d:.0f}% above the local middle.",
                          "Worth watching orders; test what a small cut would do to profit.", -5.0))
    if ideas:
        section("Worth investigating", "Price position only; your customers' reaction still needs testing")
        cols = st.columns(len(ideas[:2]), gap="medium")
        for col, (pid, title, body, v) in zip(cols, ideas[:2]):
            with col, st.container(key=f"card_idea_{pid}"):
                st.markdown(f'<div class="idea">{T.esc(title)}</div><div class="plain">{T.esc(body)}</div>',
                            unsafe_allow_html=True)
                st.button("Test it in the Decision Lab →", key=f"go_idea_{pid}", on_click=go, args=("Decision Lab",),
                          kwargs=lab_link(views.NAMES[pid], sl_price=v))

    section("Compare one item")
    c1, _ = st.columns([1, 2])
    name = c1.selectbox("Item", list(views.NAMES.values()), key="lm_product")
    pid = next(k for k, v in views.NAMES.items() if v == name)
    concepts = sorted(m.prices["concept"].unique())
    with st.expander("Filters"):
        types = st.multiselect("Restaurant types", concepts, default=concepts, key="lm_types")
        similar = st.toggle("Also count similar (not identical) servings", key="lm_similar",
                            help="For example a smaller taquito. Off by default so only equivalent servings are compared.")
    our = float(ctx.prices[PRODUCT_IDS.index(pid)])
    c = mk.compare(pid, our, market=m, concepts=types or None, include_similar=similar, as_of=today)
    with st.container(key="card_market_summary"):
        if c["sufficient"]:
            pos = {"within the comparable range": "in the usual range",
                   "below the comparable range": "below everyone we checked",
                   "above the comparable range": "above everyone we checked"}[c["position"]]
            st.markdown(f'<div class="idea">Your {T.esc(c["our_serving"])}: ${our:.2f}</div>'
                        f'<div class="plain">{c["peer_count"]} nearby restaurants charge ${c["peer_min"]:.2f} to '
                        f'${c["peer_max"]:.2f} for the same thing (middle price ${c["peer_median"]:.2f}). Your price is '
                        f'{pos}.</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="idea">Your {T.esc(c["our_serving"])}: ${our:.2f}</div>'
                        f'<div class="plain">We found only {c["peer_count"]} restaurant(s) selling the same serving: not '
                        'enough to compare fairly.</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="soft">{T.esc(mk.NOT_A_RECOMMENDATION)}</div>', unsafe_allow_html=True)
        if c["comparables"]:
            df = pd.DataFrame(c["comparables"])
            st.altair_chart(charts.price_position(df, our, c.get("peer_median"), label=c["our_serving"]), width="stretch")
            st.dataframe(df[["restaurant", "item", "listed_price", "comparable_price", "verified_date", "source_url"]],
                         hide_index=True, width="stretch",
                         column_config={"restaurant": "Restaurant", "item": "Menu item",
                                        "listed_price": st.column_config.NumberColumn("Menu price", format="$%.2f"),
                                        "comparable_price": st.column_config.NumberColumn("Same serving as yours", format="$%.2f"),
                                        "verified_date": "Checked", "source_url": st.column_config.LinkColumn("Menu", display_text="View menu")})
        for w in c["warnings"]:
            if "Small sample" not in w:
                st.caption(w)
    b1, b2, b3 = st.columns(3)
    options = [("Lower 5%", -5.0), ("Raise 5%", 5.0)]
    if c["sufficient"] and abs(c["difference_vs_median_pct"]) <= 30:
        to_med = round((c["peer_median"] / our - 1) * 100, 1)
        options.append((f"Match the middle price ({to_med:+.1f}%)", to_med))
    for col, (lab_, v) in zip((b1, b2, b3), options):
        col.button(f"{lab_} →", key=f"lm_go_{lab_[:5]}", on_click=go, args=("Decision Lab",),
                   kwargs=lab_link(name, sl_price=float(v)))


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


def parse_what_if(text: str) -> dict | None:
    """Turn 'What if I increase taco prices by 5%?' into Decision Lab settings (None if unclear)."""
    ql = text.lower()
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:%|percent)", ql)
    if not m:
        return None
    v = float(m.group(1))
    if re.search(r"lower|cut|reduc|decreas|drop|fall|less|cheaper|quieter|slower|fewer", ql):
        v = -abs(v)
    prod = re.search(r"(taco|burrito|quesadilla|nacho|drink)s?", ql)
    name = (prod.group(1) + "s").capitalize() if prod else None
    if re.search(r"supplier|vendor|wholesale|ingredient (cost|price)s?|food cost", ql):
        mode, key, v = "Supplier prices change", "sl_cost", max(-20.0, min(40.0, v))
    elif re.search(r"order|buy|purchas|stock|restock", ql):
        mode, key, v = "Order more or less", "sl_supply", max(-100.0, min(100.0, v))
    elif re.search(r"busier|quieter|slower|demand|customers|traffic|footfall", ql):
        mode, key, v = "Busier or quieter days", "sl_shock", max(-50.0, min(50.0, v))
    elif re.search(r"marketing|advertis|promot", ql):
        mode, key, v = "Marketing spend", "sl_mkt", max(-100.0, min(100.0, v))
    else:
        mode, key, v = "Change a price", "sl_price", max(-30.0, min(30.0, v))
    return lab_link(name, mode, **{key: float(v)})


def _run_what_if() -> None:
    q = st.session_state.get("lab_q", "")
    kw = parse_what_if(q)
    if kw is None:
        st.session_state["lab_q_error"] = ("Try including a percentage, for example “What if I increase taco "
                                           "prices by 5%?” or “What if supplier costs rise 8%?”")
        return
    st.session_state.pop("lab_q_error", None)
    for k, v in kw.items():
        st.session_state[k] = v


def page_decision_lab() -> None:
    st.markdown("# Decision Lab")
    st.markdown('<div class="section-sub" style="font-size:15.5px">Test a change before you make it. See what would '
                'likely happen to profit, sales, cash and stock.</div>', unsafe_allow_html=True)
    ctx = product_ctx()
    if ctx is None:
        empty_state("Item-level what-ifs are not available for this business.")
    else:
        lab(ctx)
    with st.expander("Whole-business options", expanded=ctx is None):
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
    with st.form("lab_ask_form", border=False):
        f1, f2 = st.columns([6, 1], vertical_alignment="bottom")
        f1.text_input("Ask a what-if", key="lab_q", placeholder="What if I increase taco prices by 5%?",
                      label_visibility="collapsed")
        f2.form_submit_button("Run", on_click=_run_what_if, width="stretch")
    if st.session_state.get("lab_q_error"):
        st.caption(st.session_state["lab_q_error"])
    mode = seg("Kind of change", MODES, "seg_sl_mode", "Change a price", on_change=_mode_changed)
    show = MODE_KEYS[mode]
    with st.container(key="panel_controls"):
        left, right = st.columns([1, 1.7], gap="large")
        with left:
            name = st.selectbox("Item", list(views.NAMES.values()), key="sl_product")
            pid = next(k for k, v in views.NAMES.items() if v == name)
            i = PRODUCT_IDS.index(pid)
            h = seg("Look ahead", [7, 14, 28], "seg_sl_h", 28, lambda d: {7: "1 week", 14: "2 weeks", 28: "4 weeks"}[d])
            st.button("Reset", key="sl_reset", on_click=_reset_controls)
        with right:
            cap = (ctx.max_price_increase or 0.05) * 100
            if "sl_price" in show:
                st.slider(f"{name} price change (%)", -30.0, 30.0, step=1.0, key="sl_price")
                p = st.session_state["sl_price"]
                new_price = round(ctx.prices[i] * (1 + p / 100), 2)
                tag = (T.status("Above your limit", "neg") if p > cap + 1e-9 else T.status("within your limit", "pos") if p else "")
                st.markdown(f'<div class="plain">{T.money(ctx.prices[i], 2)} → <b>{T.money(new_price, 2)}</b> &nbsp;{tag}</div>'
                            f'<div class="soft">Your limit: raise prices by at most {cap:g}% at a time.</div>',
                            unsafe_allow_html=True)
            if "sl_supply" in show:
                st.slider(f"Regular {name.lower()} ingredient orders (%)", -100.0, 100.0, step=5.0, key="sl_supply")
                fc_units = float(ctx.bundle.targets[f"demand_{pid}"].point[:h].clip(0).sum())
                st.markdown(f'<div class="soft">About {st.session_state["sl_supply"] / 100 * 1.35 * fc_units:+,.0f} portions '
                            f'over the {weeks(h)} (we expect {fc_units:,.0f} orders).</div>', unsafe_allow_html=True)
            if "sl_extra" in show:
                st.number_input("One-time extra purchase (portions)", 0.0, 5000.0, step=25.0, key="sl_extra")
            if "sl_cost" in show:
                st.slider("Supplier ingredient prices (%)", -20.0, 40.0, step=1.0, key="sl_cost",
                          help="What your suppliers charge for all ingredients. Menu prices stay the same.")
            if "sl_shock" in show:
                st.slider("Customer traffic (%)", -50.0, 50.0, step=5.0, key="sl_shock",
                          help="For example weather, a local event or road works.")
                st.radio("Applies to", ["This item", "All items"], horizontal=True, key="sl_scope")
            if "sl_mkt" in show:
                st.slider("Marketing & software spending (%)", -100.0, 100.0, step=10.0, key="sl_mkt")
                st.markdown(f'<div class="soft">{T.money(ctx.marketing_monthly)}/month → '
                            f'{T.money(ctx.marketing_monthly * (1 + st.session_state["sl_mkt"] / 100))}/month</div>',
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
    rec = _cached(("rec", repr(sc)), lambda: recommend(ctx, sc))
    lo, hi = cmp_["profit"]["change_90pct_range"]
    pr = cmp_["probability_profit_improves"]
    sr, u, d_ = cmp_["stockout_risk"], cmp_["units_sold"], cmp_["demand_units"]
    inv = cmp_["ending_inventory_value"]

    left, right = st.columns([1.35, 1], gap="medium")
    with left, st.container(key="card_result"):
        if sc.is_baseline:
            st.markdown(f'<div class="eyebrow">Your current plan · {weeks(h)}</div>'
                        f'<div class="big-number">{T.money(cmp_["profit"]["baseline"])}</div>'
                        '<div class="plain">Expected profit if nothing changes. Move a slider or ask a what-if above '
                        'to compare a change.</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="soft">{T.esc(sc.describe()).capitalize()}</div>', unsafe_allow_html=True)
            if not cmp_["within_policy"]:
                st.markdown('<div class="callout bad"><b>Outside the owner\'s approved limits:</b> '
                            + T.esc("; ".join(cmp_["policy_violations"])) + ". You can explore it, but it won't be "
                            "suggested.</div>", unsafe_allow_html=True)
            impact_block(cmp_["profit"]["change"], lo, hi, pr, h, "Profit impact")
        rows = [("Revenue", T.money(cmp_["revenue"]["baseline"]), T.money(cmp_["revenue"]["scenario"]),
                 T.delta(cmp_["revenue"]["change_pct"])),
                ("Profit", T.money(cmp_["profit"]["baseline"]), T.money(cmp_["profit"]["scenario"]),
                 T.delta(cmp_["profit"]["change_pct"])),
                ("Cash", T.money(cmp_["cash_flow"]["baseline"]), T.money(cmp_["cash_flow"]["scenario"]),
                 T.delta(cmp_["cash_flow"]["change_pct"])),
                (f"{name} orders (demand)", f"{d_['baseline']:,.0f}", f"{d_['scenario']:,.0f}", T.delta(d_["change_pct"])),
                (f"{name} sold", f"{u['baseline']:,.0f}", f"{u['scenario']:,.0f}", T.delta(u["change_pct"])),
                ("Ingredients left at the end", T.money(inv["baseline"]), T.money(inv["scenario"]),
                 T.delta(inv["change_pct"], good_when_up=False)),
                (f"Chance {name.lower()} sells out", f"{sr['baseline']:.0%}", f"{sr['scenario']:.0%}",
                 T.delta((sr["scenario"] - sr["baseline"]) * 100, good_when_up=False, suffix=" pts", decimals=0)
                 if abs(sr["scenario"] - sr["baseline"]) >= 0.005 else '<span class="delta flat">same</span>')]
        st.markdown('<div style="height:8px"></div>' + T.before_after(rows), unsafe_allow_html=True)
    with right, st.container(key="panel_rec"):
        best = rec["recommended"]
        if sc.is_baseline:
            st.markdown('<div class="eyebrow brand">Best option we found</div>', unsafe_allow_html=True)
            if best:
                st.markdown(f'<div class="idea">{T.esc(best["action"])}</div>'
                            f'<div class="plain">About <b>{T.signed_money(best["profit_change"])}</b> profit over the '
                            f'{weeks(h)}. {T.esc(T.confidence_words(best["probability_profit_improves"], *best["profit_change_90pct_range"]))}</div>',
                            unsafe_allow_html=True)
                st.button("Try this option", key="cta_try_best", on_click=go, args=("Decision Lab",),
                          kwargs=lab_link_for(best["scenario"]))
            else:
                st.markdown(f'<div class="plain">{T.esc(rec["text"])}</div>', unsafe_allow_html=True)
        else:
            from decisiondesk.briefing import decision_checklist
            from decisiondesk.decision.product_scenario import sensitivity
            sens = _cached(("sens", repr(sc)), lambda: sensitivity(ctx, sc))
            lm = _local_market(pid, float(ctx.prices[i])) if sc.price_change_pct else None
            a = decision_checklist(ctx, sc, cmp_, sens, local_market=lm)
            st.markdown('<div class="eyebrow brand">Review action</div>', unsafe_allow_html=True)
            gain = cmp_["profit"]["change"] > 0 and cmp_["within_policy"]
            what = (a["action"] if gain else f"Not recommended as it stands: {a['action'].lower()}")
            step = (a["verify"][0] if gain else
                    "Don't make this change as it stands: it is expected to reduce profit." if cmp_["profit"]["change"] < 0
                    else "Stay within your own price limit, or change the limit deliberately first.")
            items = [("1. What to do", what), ("2. Why it may help", a["why"]),
                     ("3. Estimated impact", (f"About {T.signed_money(cmp_['profit']['change'])} profit over the {weeks(h)} "
                                             f"(downside {T.signed_money(lo)}, upside {T.signed_money(hi)}).")),
                     ("4. Main risks", " ".join(a["risks"][:2]) or "No major risks found in our tests."),
                     ("5. Check before acting", step)]
            st.markdown("".join(f'<div class="check-head">{T.esc(t_)}</div><div class="plain">{T.esc(v_)}</div>'
                                for t_, v_ in items), unsafe_allow_html=True)
            if best and not sc.ingredient_cost_change_pct and best["scenario"] != asdict(sc):
                st.markdown(f'<div class="soft" style="margin-top:6px">Best tested {T.esc(name.lower())} option: '
                            f'{T.esc(best["action"])} (about {T.signed_money(best["profit_change"])}).</div>',
                            unsafe_allow_html=True)
            with st.expander("Full checklist"):
                checklist(a, key="lab_chk")
            if gain and not sc.ingredient_cost_change_pct:
                ack = st.checkbox("I've reviewed the risks and checks", key="lab_ack")
                if st.button("Approve for my records", key="cta_approve", disabled=not ack, width="stretch"):
                    _record_decision(a, cmp_)
                st.caption("Approval records your decision. Nothing is changed automatically: you update the menu, "
                           "POS or supplier order yourself.")
            else:
                st.markdown('<div class="soft" style="margin-top:6px"><b>Needs your approval.</b> Nothing changes '
                            'automatically.</div>', unsafe_allow_html=True)
            if st.session_state.get("lab_approved"):
                st.success(st.session_state["lab_approved"])
        e1, e2 = st.columns(2)
        if e1.button("Ask AI about this", key="go_analyst", width="stretch"):
            _ask(f"What happens if {name.lower()} prices change {sc.price_change_pct:+g}%?" if sc.price_change_pct
                 else f"What if supplier costs rise {sc.ingredient_cost_change_pct:g}%?" if sc.ingredient_cost_change_pct
                 else f"Should I buy more {name.lower()} ingredients?" if sc.supply_change_pct or sc.extra_units
                 else "What should I do?")
            st.rerun()
        if e2.button("Use for iMessage answers", key="lab_publish", width="stretch"):
            _publish()
        if st.session_state.get("lab_published"):
            st.success(st.session_state["lab_published"])

    with st.expander("Charts: sales, profit and stock over time"):
        tabs = st.tabs(["Sales & profit", "Orders vs stock", "Other items"])
        with tabs[0]:
            c1, c2 = st.columns(2)
            c1.altair_chart(charts.band_compare(views.band_frame(scen, base, "revenue"), title="Daily sales"), width="stretch")
            c2.altair_chart(charts.band_compare(views.band_frame(scen, base, "profit"), title="Daily profit"), width="stretch")
            st.caption("Solid: with your change. Grey dashed: today's plan. Shaded: the likely range.")
        with tabs[1]:
            st.altair_chart(charts.demand_supply(views.demand_supply_frame(scen, pid),
                                                 title=f"{name}: expected orders vs what you can make"), width="stretch")
        with tabs[2]:
            st.altair_chart(charts.impact_bars(views.impact_frame(cmp_)), width="stretch")
            st.caption("Shared ingredients and customers switching dishes mean one item's change moves the others.")
    with st.expander("How we estimated this"):
        if not sc.is_baseline:
            st.markdown(estimate_note(lo, hi, pr))
            d = scen.profit.sum(1) - base.profit.sum(1)
            st.altair_chart(charts.delta_histogram(d, lo, hi), width="stretch")
        st.markdown("**Assumptions.** How customers react to a price change, how many switch dishes when one sells "
                    "out, and how much marketing brings in are estimates with ranges (below), not measured from "
                    "your sales.")
        st.dataframe(pd.DataFrame([{"assumption": n, "estimate": a_["value"], "low": a_["low"], "high": a_["high"]}
                                   for n, a_ in (("Customer reaction to price", asdict(ELASTICITY[pid])),
                                                 ("Customers who switch dishes", asdict(SUBSTITUTION)),
                                                 ("Extra business from marketing", asdict(MARKETING_RESPONSE)))]),
                     hide_index=True, width="stretch")
        if rec["sensitivity"]:
            st.markdown("**If an estimate is at its edge** (best option): " + "; ".join(
                f"{'stronger' if x['level'] == 'low' else 'weaker'} reaction to price → {T.signed_money(x['profit_change'])}"
                if "elasticity" in x["assumption"] else f"{x['assumption'].replace('_', ' ')} {x['level']} → "
                f"{T.signed_money(x['profit_change'])}" for x in rec["sensitivity"]) + ".")
        rv = cmp_["range_validation"]
        st.caption(f"Range check on past weeks: revenue {rv['revenue_total']['total_hits']}/{rv['revenue_total']['windows']}, "
                   f"profit {rv['profit_total']['total_hits']}/{rv['profit_total']['windows']} past periods inside the 80% "
                   "range. Not modelled: staff capacity, competitors' reactions. Data: sample (synthetic) records.")


def _record_decision(a: dict, cmp_: dict) -> None:
    """Append an approved decision to the local decision log (the owner acts on it themselves)."""
    import json as _json
    entry = {"approved_at": datetime.now().isoformat(timespec="seconds"), "business": biz_name(),
             "action": a["action"], "expected_profit_change": round(cmp_["profit"]["change"]),
             "downside": round(cmp_["profit"]["change_90pct_range"][0]), "horizon_days": cmp_["horizon_days"],
             "checks": a["verify"], "executed_automatically": False}
    path = Path(os.getenv("MSRD_DECISION_LOG") or "data/cache/decision_log.json")
    try:
        log = _json.loads(path.read_text()) if path.exists() else []
        log.append(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(log, indent=1))
        st.session_state["lab_approved"] = (f"Approved for your records: {a['action']} ({entry['approved_at'][:16]}). "
                                            "Make the change yourself when you're ready.")
    except OSError as exc:
        st.session_state["lab_approved"] = f"Could not save the decision log: {exc}"


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


# ================================================================================ SETTINGS: DATA
def data_section() -> None:
    section("Your data")
    st.markdown(f'<div class="plain">{T.esc(SAMPLE_NOTE)}</div>', unsafe_allow_html=True)
    with st.container(key="panel_upload"):
        section("Upload your own daily records", "CSV or Excel: date, sales and costs (optional: customers, cash)")
        up = st.file_uploader("Daily data file", type=["csv", "xlsx"], key="upload")
        if up is not None:
            dest = Path("data/private/uploads")
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / up.name
            path.write_bytes(up.getvalue())
            cols = list(pd.read_csv(path, nrows=0).columns) if path.suffix == ".csv" else \
                list(pd.read_excel(path, nrows=0).columns)
            st.caption("Match your columns (leave unused ones empty).")
            mapping = {}
            mc = st.columns(3)
            for j, role in enumerate(ROLES):
                pick = mc[j % 3].selectbox(role, ["—", *cols], key=f"map_{role}")
                if pick != "—":
                    mapping[pick] = role
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("Business name", key="up_name")
            industry = c2.selectbox("Industry", ["restaurant", "retail", "other"], key="up_ind")
            state = c3.selectbox("State", sorted(US_STATES), key="up_state")
            objective = c1.selectbox("Main goal", ["max_expected_profit", "min_operating_cost",
                                                   "min_cash_shortage_risk", "min_stockout_risk"], key="up_obj")
            max_price = c2.number_input("Max price increase (fraction)", 0.0, 0.5, 0.05, key="up_mp")
            opening = c3.number_input("Current cash balance (USD, optional)", value=0.0, key="up_cash")
            confirm = st.checkbox("I confirm the industry, state, goal and limits", key="up_ok")
            if st.button("Load my data", key="up_load", disabled=not (confirm and name)):
                try:
                    profile = BusinessProfile(name, industry, state, objective,
                                              {"max_price_increase": max_price}, confirmed_by_user=confirm)
                    s_ = BusinessSession.from_upload(path, ColumnMapping(mapping), profile,
                                                     opening_cash=opening or None, models=MODELS)
                    _reset_lab()
                    st.session_state.update(session=s_, agent=DecisionAgent(s_), chat=[],
                                            business_key=f"upload:{up.name}", last_decision=None, pending=None,
                                            view=None, nav="Overview")
                    st.rerun()
                except ValueError as exc:
                    st.error(f"Upload rejected: {exc}")
        st.caption("Files stay on this computer (data/private, never committed). Item sales, recipes and invoices "
                   "are the next step: see docs/ONBOARDING.md.")
    section("Where the numbers come from")
    st.dataframe(pd.DataFrame([
        {"source": "Business records", "what it is": "Sample (synthetic) records for the demo businesses",
         "used for": "every business number, forecast and what-if"},
        {"source": "Item layer", "what it is": "Sample items, recipes and ingredient stock",
         "used for": "products, stock, Decision Lab"},
        {"source": "Austin menu prices", "what it is": "Public prices from restaurants' own menus, checked by hand",
         "used for": "Local Market only"},
        {"source": "Capital One Nessie", "what it is": "Banking sandbox (simulated)",
         "used for": "banking import demo in Advanced diagnostics"},
        {"source": "Google Gemini", "what it is": "AI service", "used for": "explaining results in the assistant"},
        {"source": "Photon", "what it is": "Messaging service", "used for": "iMessage briefings, alerts and questions"},
    ]), hide_index=True, width="stretch")


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
