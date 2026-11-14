"""Main Street Risk Desk — Streamlit interface.

    streamlit run app/streamlit_app.py

Every number on screen comes from the same tool functions the analyst agent calls
(``decisiondesk.agent.tools``). Synthetic business data, observed FRED data, simulated Nessie
banking records and hypothetical scenario changes are labelled wherever they appear.
"""
from __future__ import annotations

import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from decisiondesk.agent.agent import DecisionAgent
from decisiondesk.agent.tools import BusinessSession, run_tool
from decisiondesk.profile import ROLES, US_STATES, BusinessProfile, ColumnMapping

load_dotenv(".env")
FAST = os.getenv("MSRD_FAST") == "1"
MODELS = ("seasonal_naive", "weekday_mean_12w", "historical_mean_28d", "ridge") if FAST else None
SERIES_1 = "#2a78d6"  # reference palette categorical slot 1 (validated light + dark)

st.set_page_config(page_title="Main Street Risk Desk", layout="wide")


def _session() -> BusinessSession | None:
    return st.session_state.get("session")


def _load_demo(key: str) -> None:
    with st.spinner("Loading the business (cached model evaluations load in under a second; "
                    "run `python -m decisiondesk.precompute` once to warm them)..."):
        s = BusinessSession.from_demo(key, models=MODELS, n_sims=1000 if FAST else 2000)
    st.session_state.update(session=s, agent=DecisionAgent(s), chat=[], business_key=key,
                            last_decision=None)


def _label_banner(s: BusinessSession) -> None:
    st.info(f"**{s.metadata.get('business_name')}** · {s.industry} · "
            f"{s.metadata.get('city', '')} {s.metadata.get('state', '')} · data: "
            f"**{s.data_label}** · last data date {s.origin.date()}"
            + (" · fast mode (4 models)" if FAST else ""), icon="🏪")


# ------------------------------------------------------------------------------ sidebar / business
st.title("Main Street Risk Desk")
st.caption("Forecasts, scenario simulation and ranked recommendations for U.S. small businesses. "
           "Recommendations are modeled under stated assumptions and need your approval; "
           "nothing is executed automatically.")

with st.sidebar:
    st.header("1 · Business")
    choice = st.radio("Data source", ["Demo restaurant (Austin, TX)", "Demo retailer (Columbus, OH)",
                                      "Upload CSV / Excel"], key="source")
    if choice.startswith("Demo"):
        key = "restaurant" if "restaurant" in choice else "retail"
        if st.session_state.get("business_key") != key:
            _load_demo(key)
    else:
        up = st.file_uploader("Daily data file", type=["csv", "xlsx"], key="upload")
        if up is not None:
            dest = Path("data/private/uploads")
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / up.name
            path.write_bytes(up.getvalue())
            cols = list(pd.read_csv(path, nrows=0).columns) if path.suffix == ".csv" else \
                list(pd.read_excel(path, nrows=0).columns)
            st.caption("Map your columns to roles (leave unused roles empty).")
            mapping = {}
            for role in ROLES:
                pick = st.selectbox(role, ["—"] + cols, key=f"map_{role}")
                if pick != "—":
                    mapping[pick] = role
            name = st.text_input("Business name", key="up_name")
            industry = st.selectbox("Industry", ["restaurant", "retail", "other"], key="up_ind")
            state = st.selectbox("State", sorted(US_STATES), key="up_state")
            objective = st.selectbox("Objective", ["max_expected_profit", "min_operating_cost",
                                                   "min_cash_shortage_risk", "min_stockout_risk"],
                                     key="up_obj")
            max_price = st.number_input("Max price increase (fraction)", 0.0, 0.5, 0.05, key="up_mp")
            opening = st.number_input("Current cash balance (USD, optional)", value=0.0, key="up_cash")
            confirm = st.checkbox("I confirm industry, state, objective and constraints", key="up_ok")
            if st.button("Load upload", key="up_load", disabled=not (confirm and name)):
                try:
                    profile = BusinessProfile(name, industry, state, objective,
                                              {"max_price_increase": max_price},
                                              confirmed_by_user=confirm)
                    s = BusinessSession.from_upload(path, ColumnMapping(mapping), profile,
                                                    opening_cash=opening or None, models=MODELS)
                    st.session_state.update(session=s, agent=DecisionAgent(s), chat=[],
                                            business_key=f"upload:{up.name}", last_decision=None)
                except ValueError as exc:
                    st.error(f"Upload rejected: {exc}")

s = _session()
if s is None:
    st.warning("Choose a demo business or upload and confirm your data in the sidebar.")
    st.stop()
_label_banner(s)

tabs = st.tabs(["Overview", "Forecast", "Economy", "Decisions", "Analyst", "Alerts", "Messaging",
                "Banking (Nessie)"])

# ------------------------------------------------------------------------------ overview
with tabs[0]:
    ov = run_tool(s, "get_business_overview", {"period_days": 28})
    c, p = ov["current"], ov["previous_period"] or {}
    m = st.columns(4)
    m[0].metric("Revenue (28 d)", f"${c['revenue']:,.0f}",
                None if not p.get("revenue") else f"{c['revenue'] - p['revenue']:+,.0f} vs prior 28 d")
    m[1].metric("Operating profit (28 d)", f"${c['operating_profit']:,.0f}",
                f"{c['operating_margin_pct']}% margin")
    m[2].metric("Cash", "n/a" if ov["cash"]["balance"] is None else f"${ov['cash']['balance']:,.0f}")
    m[3].metric("Inventory on hand", "n/a" if ov.get("inventory_on_hand") is None else f"{ov['inventory_on_hand']:,.0f}")
    st.caption(f"Cash source: {ov['cash']['source']}")
    hist = s.history.tail(180)[["date", "revenue"]]
    st.altair_chart(alt.Chart(hist, title="Daily revenue, last 180 days (USD)").mark_line(
        color=SERIES_1, strokeWidth=2).encode(
        x=alt.X("date:T", title=None), y=alt.Y("revenue:Q", title=None),
        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("revenue:Q", format="$,.0f")]),
        width="stretch")
    if ov.get("bills_paid_in_period"):
        st.subheader("Bills paid in the last 28 days")
        st.dataframe(pd.Series(ov["bills_paid_in_period"], name="USD"), width="stretch")
    st.caption("Supported analyses for this data: " + ", ".join(ov["capabilities"]))

# ------------------------------------------------------------------------------ forecast
with tabs[1]:
    targets = [t for t in ("revenue", "units_sold", "variable_costs", "net_cash_flow")
               if t == "net_cash_flow" or t in s.history.columns]
    target = st.selectbox("Metric", targets, key="fc_target")
    with st.spinner("Running chronological validation..."):
        fc = run_tool(s, "forecast_business", {"target": target})
    a, b, c3 = st.columns(3)
    a.metric("Next 28 days (point)", f"{fc['point_total']:,.0f}")
    b.metric("80% interval", f"{fc['interval_80_total'][0]:,.0f} – {fc['interval_80_total'][1]:,.0f}")
    c3.metric("Final-test WAPE", f"{fc['final_test']['wape']}",
              f"coverage {fc['final_test']['interval_80_coverage']}", delta_color="off")
    bundle = s.bundle((target,))
    tf = bundle.targets[target]
    import numpy as np

    from decisiondesk.decision.baseline import make_draws
    sims = bundle.simulate(make_draws(bundle, 2000, seed=11))[target]
    future = pd.DataFrame({"date": tf.dates, "value": tf.point,
                           "lo": np.quantile(sims, .1, axis=0), "hi": np.quantile(sims, .9, axis=0)})
    y = s.history[target] if target != "net_cash_flow" else (
        s.history.revenue - s.history.variable_costs - s.history.fixed_costs)
    past = pd.DataFrame({"date": s.history["date"], "value": y}).tail(84)
    base = alt.Chart(future)
    band = base.mark_area(color=SERIES_1, opacity=0.18).encode(x="date:T", y="lo:Q", y2="hi:Q")
    line = alt.Chart(pd.concat([past.assign(kind="actual"), future[["date", "value"]].assign(kind="forecast")])
                     ).mark_line(color=SERIES_1, strokeWidth=2).encode(
        x=alt.X("date:T", title=None), y=alt.Y("value:Q", title=None),
        strokeDash=alt.StrokeDash("kind:N", legend=alt.Legend(title=None, orient="top")),
        tooltip=["date:T", alt.Tooltip("value:Q", format=",.0f"), "kind:N"])
    st.altair_chart((band + line).properties(title=f"{target}: last 84 days and 28-day forecast "
                                                   "(band = 80% interval)"), width="stretch")
    st.write(f"Selected by validation: **{fc['selected_model']}** — {fc['selection_reason']}")
    st.dataframe(pd.DataFrame(fc["leaderboard"]), width="stretch", hide_index=True)
    st.caption(fc["caveat"])

# ------------------------------------------------------------------------------ economy
with tabs[2]:
    live = st.button("Refresh from FRED API (live)", key="fred_live")
    econ = run_tool(s, "get_economic_indicators", {"refresh": bool(live)})
    st.caption(f"Observed U.S. data (FRED/ALFRED), as published on or before {econ['as_of']}. "
               + econ["not_used_in_forecasts"])
    if econ["observations"]:
        st.dataframe(pd.DataFrame(econ["observations"])[[
            "series_id", "title", "observed_at", "value", "units", "change_12m", "change_12m_unit",
            "change_12m_period", "published_at", "retrieval", "retrieved_at_utc", "source_url"]],
            width="stretch", hide_index=True)
    for n in econ["notes"]:
        st.warning(n)
    st.caption("This product uses the FRED® API but is not endorsed or certified by the Federal "
               "Reserve Bank of St. Louis.")

# ------------------------------------------------------------------------------ decisions
with tabs[3]:
    st.caption("Scenario changes are HYPOTHETICAL; action effects are modeled assumptions, not "
               "proven causal effects. Shares are frequencies across simulated scenarios, not "
               "guarantees.")
    if s.industry == "restaurant":
        c1, c2, c4, c5 = st.columns(4)
        food = c1.slider("Food-cost change (%)", -10.0, 30.0, float(s.state.food_cost_change_pct), 1.0, key="d_food")
        cash_in = c5.number_input("Starting cash for the scenario (USD, 0 = ledger balance)", 0.0,
                                  1e7, float(s.state.starting_cash or 0), 500.0, key="d_cash",
                                  help="A HYPOTHETICAL cash position, e.g. after an unexpected repair bill.")
        el = c2.slider("Price elasticity range", -2.5, -0.1,
                       (float(s.state.elasticity_low), float(s.state.elasticity_high)), 0.1, key="d_el")
        mpi = c4.number_input("Max price increase", 0.0, 0.3,
                              float(s.state.max_price_increase or 0.05), 0.01, key="d_mpi")
        if st.button("Run scenario", key="d_run"):
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
        tol = c4.slider("Stockout tolerance after first delivery", 0.0, 1.0,
                        float(s.state.max_stockout_probability or 0.25), 0.05, key="d_tol")
        if st.button("Run scenario", key="d_run"):
            with st.spinner("Simulating inventory policies..."):
                st.session_state["last_decision"] = run_tool(s, "evaluate_inventory_policies", {
                    "purchase_budget_usd": budget or None, "storage_units": storage or None,
                    "max_stockout_probability": tol, "objective": "max_expected_profit"})
    out = st.session_state.get("last_decision")
    if out and out.get("supported", True):
        rec = out["recommended_action"]
        (st.success if rec else st.warning)(
            f"**{'Recommended: ' + rec if rec else 'No recommendation'}** · strength: "
            f"{out['strength']} · objective: {out['objective']} · run {out['run_id']}")
        st.write(out["rationale"])
        table = pd.DataFrame(out["actions"])
        st.dataframe(table, width="stretch", hide_index=True)
        with st.expander("Assumptions, constraints, sensitivity and limitations"):
            st.json({"scenario": out["scenario"], "assumptions": out["assumptions"],
                     "constraints": out["constraints"], "sensitivity": out["sensitivity"],
                     "stress_test": out["stress_test"], "limitations": out["limitations"]})
    elif out:
        st.error(out.get("reason"))

# ------------------------------------------------------------------------------ analyst
with tabs[4]:
    agent: DecisionAgent = st.session_state["agent"]
    model = f" ({agent.gemini.model})" if agent.gemini else ""
    st.markdown(f"**Active analyst: {agent.label}{model}**")
    if agent.mode == "gemini":
        st.caption("Gemini calls the forecasting and decision tools; it never computes figures "
                   "itself. Free-tier prompts may be used by Google to improve its products, so "
                   "do not use real private data on the free tier.")
    elif agent.mode == "offline":
        st.caption("Set GEMINI_API_KEY for free-form questions.")
    if agent.last_fallback:
        st.warning(f"Last Gemini request failed ({agent.last_fallback}); that answer came from the "
                   "offline analyst.")
    st.caption("Every number in an answer is checked against tool outputs.")
    for msg in st.session_state["chat"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["text"])
    q = st.chat_input("Ask about forecasts, risks or decisions", key="chat_in")
    if q:
        st.session_state["chat"].append({"role": "user", "text": q})
        with st.spinner("Running tools..."):
            reply = agent.ask(q)
        tools_used = ", ".join(f"{c['name']} (run {c['output']['run_id']})" for c in reply.tool_calls
                               if "output" in c) or "none"
        st.session_state["chat"].append({"role": "assistant", "text": reply.text + (
            f"\n\n_Tools: {tools_used} · grounded: {reply.grounded}_")})
        st.rerun()

# ------------------------------------------------------------------------------ alerts
with tabs[5]:
    from decisiondesk.monitor import MonitorStore, TelegramNotifier, run_checks
    store = MonitorStore(s.monitor_path)
    bkey = s.metadata.get("key", s.dataset)
    with st.form("new_rule"):
        kind = st.selectbox("Rule", ["fred_release", "cash_shortfall"], key="r_kind")
        series = st.text_input("FRED series (for fred_release)", "CUSR0000SAF11", key="r_series")
        thr = st.number_input("Threshold (|12-month % change| or shortfall frequency 0-1; 0 = any)",
                              0.0, 100.0, 1.0, key="r_thr")
        tg_ok = TelegramNotifier().configured
        tg = st.checkbox("Also send to Telegram (opt-in)", disabled=not tg_ok, key="r_tg",
                         help=None if tg_ok else "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable")
        if st.form_submit_button("Save rule"):
            try:
                store.add_rule(bkey, kind, series if kind == "fred_release" else None,
                               thr or None, tg)
                st.success("Rule saved.")
            except ValueError as exc:
                st.error(str(exc))
    if st.button("Run checks now", key="r_run"):
        new = run_checks(MonitorStore(s.monitor_path), {bkey: s},
                         notifier=TelegramNotifier() if TelegramNotifier().configured else None)
        st.success(f"{len(new)} new alert(s).")
    store = MonitorStore(s.monitor_path)
    st.dataframe(pd.DataFrame([r.__dict__ for r in store.rules]), width="stretch", hide_index=True)
    for al in reversed(store.alerts[-20:]):
        st.warning(f"**{al.title}** ({al.created_at}, {al.category}) — {al.message}")
    st.caption(f"Last check: {store.last_run or 'never'}. Duplicate alerts are suppressed. "
               "Alerts never execute business actions.")

# ------------------------------------------------------------------------------ messaging
with tabs[6]:
    from decisiondesk.messaging.gateway import PhotonGateway, PhotonUnavailable
    from decisiondesk.messaging.risk_signals import scan
    from decisiondesk.messaging.service import (
        ALERT_KINDS,
        MessagingService,
        MessagingStore,
        SimulatorGateway,
    )

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
    svc: MessagingService = st.session_state[skey]
    st.markdown(f"**Channel: {svc.channel_label}**")
    try:
        if st.button("Refresh connection status", key="msg_status") or f"{skey}_status" not in st.session_state:
            st.session_state[f"{skey}_status"] = svc.gateway.status()
            svc.sync_recipients()
        status = st.session_state[f"{skey}_status"]
    except PhotonUnavailable as exc:
        status = {"connected": False, "problems": [str(exc)], "recipients": [], "sms_lines": []}
    c1, c2, c3 = st.columns(3)
    c1.metric("Photon connection", "connected" if status.get("connected") else "not connected")
    im = status.get("imessage") or {}
    c2.metric("iMessage line", im.get("mode", "—"))
    c3.metric("SMS lines", len(status.get("sms_lines", [])))
    for prob in status.get("problems", []):
        st.error(prob)
    if live and not status.get("sms_lines"):
        st.caption("SMS is not provisioned: US SMS needs a purchased Photon number and an approved "
                   "10DLC campaign. iMessage works on the shared line.")
    owners = [o for o in svc.store.owners.values() if o.authorized]
    if not owners:
        st.warning("No verified recipient yet. In the Photon dashboard (iMessage page), add your "
                   "phone as the sender, then text the assigned number from it.")
    for o in owners:
        o.business_key = bkey
        st.subheader(f"Owner {o.masked}")
        st.caption(f"Conversation started from phone: {'yes' if o.space_id else 'no — text the agent line first'}"
                   f" · last inbound {o.last_inbound_at or '—'} · alerts consent "
                   f"{o.consent_source or 'none'} {o.consent_at or ''}")
        opt = st.checkbox(f"Send proactive risk alerts to {o.masked} (explicit opt-in)",
                          value=o.alerts_opt_in and not o.stopped, key=f"optin_{o.handle}_{skey}")
        if opt != (o.alerts_opt_in and not o.stopped):
            svc.set_alert_consent(o.handle, opt, "dashboard")
        o.alert_prefs = st.multiselect("Alert types", list(ALERT_KINDS), default=o.alert_prefs,
                                       key=f"prefs_{o.handle}_{skey}")
        if o.stopped:
            st.warning("This owner replied STOP; nothing will be sent until they reply START.")
        b1, b2 = st.columns(2)
        confirm = b1.checkbox("I confirm this is my authorized test phone", key=f"conf_{o.handle}_{skey}")
        if b1.button("Send test message", key=f"test_{o.handle}_{skey}", disabled=not confirm):
            try:
                rec = svc.send_test(o.handle)
                st.success(f"Submitted to Photon: status {rec['status']} (delivery not yet confirmed)"
                           if not svc.simulated else "Simulated message stored (not sent to a phone).")
            except (PermissionError, PhotonUnavailable) as exc:
                st.error(str(exc))
        if b2.button("Scan risks and send opted-in alerts", key=f"scan_{o.handle}_{skey}"):
            with st.spinner("Scanning with the forecasting and decision tools..."):
                sigs = scan(s)
            st.session_state[f"signals_{skey}"] = [(x.kind, x.title, x.text) for x in sigs]
            for sig in sigs:
                try:
                    rec = svc.send_alert(o.handle, sig)
                except (PermissionError, PhotonUnavailable) as exc:
                    st.error(f"{sig.kind}: {exc}")
                    continue
                st.write(f"{sig.title}: {'sent (' + rec['status'] + ')' if rec else 'not sent (consent, preference or already sent)'}")
        if svc.simulated:
            sim_text = st.text_input("Simulate the owner's reply", "What should I do?", key=f"sim_{skey}")
            if st.button("Send simulated reply", key=f"simgo_{skey}"):
                svc.gateway.simulate_inbound(sim_text, o.handle)
                with st.spinner("Analyst running tools..."):
                    svc.poll_once()
    if signals := st.session_state.get(f"signals_{skey}"):
        with st.expander(f"Latest risk scan ({len(signals)} signal(s))", expanded=True):
            for kind, title, text in signals:
                st.write(f"**{title}** ({kind}): {text}")

    auto = st.toggle("Auto-check for replies every 5 seconds", key=f"auto_{skey}",
                     disabled=not owners)

    @st.fragment(run_every="5s" if auto else None)
    def conversation():
        if auto:
            try:
                svc.poll_once()
                svc.refresh_statuses()
            except PhotonUnavailable as exc:
                st.error(str(exc))
        for o in [x for x in svc.store.owners.values() if x.authorized]:
            st.markdown(f"**Conversation with {o.masked}**")
            for m in svc.store.conversations.get(o.handle, [])[-12:]:
                who = "assistant" if m["direction"] == "out" else "user"
                with st.chat_message(who):
                    meta = f" · {m.get('kind', '')} · {m.get('status', '')}" if who == "assistant" else ""
                    st.markdown(f"{m['text']}\n\n_{m['at']}{meta}_")
        if svc.store.outbound:
            st.markdown("**Notification history** (delivery is shown only when confirmed by Photon)")
            st.dataframe(pd.DataFrame([{k: r.get(k) for k in ("created_at", "kind", "to", "status",
                                                               "delivered_at", "acceptance",
                                                               "simulated", "failure")}
                                       for r in reversed(svc.store.outbound[-20:])]),
                         width="stretch", hide_index=True)
        if svc.store.events:
            with st.expander("Messaging events and errors"):
                for e in reversed(svc.store.events[-15:]):
                    st.caption(f"{e['at']} {e['kind']}: {e['detail']}")
        st.caption(f"Messages today: {svc.store.sends_today()} / {svc.daily_limit} (daily cap). "
                   "iMessage sends carry no Photon per-message fee; SMS would.")

    conversation()

# ------------------------------------------------------------------------------ banking
with tabs[7]:
    st.caption("Capital One Nessie sandbox: SIMULATED banking records, read-only import. "
               "Forecasts stay on the business dataset until enough consistent bank history exists.")
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
