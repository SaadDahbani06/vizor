"""Main Street Risk Desk — Streamlit interface (Milestone 5 redesign).

    streamlit run app/streamlit_app.py

Pages: Overview (what is happening), Revenue & Operations (why), Scenario Lab (what if), AI
Analyst, Economy, Messaging, Evidence & Data. Every number on screen comes from the analytics
modules (``decisiondesk.dashboard``, ``decisiondesk.decision.product_scenario`` and the analyst
tools in ``decisiondesk.agent.tools``); this file only lays them out. Synthetic business data,
real FRED data, simulated Nessie records, model forecasts, simulations and assumptions are
labelled wherever they appear.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import timedelta
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
PAGES = ["Overview", "Revenue & Operations", "Scenario Lab", "AI Analyst", "Economy", "Messaging",
         "Evidence & Data"]
RO_SECTIONS = ["Revenue", "Products", "Inventory", "Profit & costs", "Cash & obligations", "Forecasts"]

st.set_page_config(page_title="Main Street Risk Desk", layout="wide", page_icon="🏪")
st.markdown(T.CSS, unsafe_allow_html=True)


@st.cache_resource
def _executor() -> ThreadPoolExecutor:
    """Background threads for slow work (Gemini replies, message polling)."""
    return ThreadPoolExecutor(max_workers=4, thread_name_prefix="msrd")


def go(page: str, **state) -> None:
    """Navigation callback: switch page and preset widgets on the target page."""
    st.session_state["nav"] = page
    for k, v in state.items():
        st.session_state[k] = v


def _session() -> BusinessSession | None:
    return st.session_state.get("session")


def _reset_lab() -> None:
    for k in [k for k in st.session_state if k.startswith(("sl_", "lab_", "ov_rec"))]:
        del st.session_state[k]


def _load_demo(key: str) -> None:
    with st.spinner("Loading the business (cached model evaluations load in seconds; run "
                    "`python -m decisiondesk.precompute` once to warm them)..."):
        s = BusinessSession.from_demo(key, models=MODELS, n_sims=1000 if FAST else 2000)
    _reset_lab()
    st.session_state.update(session=s, agent=DecisionAgent(s), chat=[], business_key=key,
                            last_decision=None, pending=None)


# ------------------------------------------------------------------------------ sidebar / business
with st.sidebar:
    st.markdown("### Business")
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
                    _reset_lab()
                    st.session_state.update(session=s, agent=DecisionAgent(s), chat=[],
                                            business_key=f"upload:{up.name}", last_decision=None,
                                            pending=None)
                except ValueError as exc:
                    st.error(f"Upload rejected: {exc}")
    st.markdown("---")
    st.markdown("**How to read labels**")
    st.markdown(" ".join(T.origin(k) for k in ("observed", "real", "forecast", "simulated", "assumed")),
                unsafe_allow_html=True)
    st.caption("Observed = what the (synthetic) records say happened. Model forecast = what a "
               "validated model expects under the current plan. Simulated = what may happen if "
               "something changes, under stated assumptions. Nothing is executed automatically.")

s = _session()
if s is None:
    st.warning("Choose a demo business or upload and confirm your data in the sidebar.")
    st.stop()

st.markdown(T.masthead(s.metadata.get("business_name", s.dataset),
                       s.data_label, str(s.origin.date()),
                       f" · {s.metadata.get('city', '')} {s.metadata.get('state', '')}"
                       + (" · fast mode (4 models)" if FAST else "")), unsafe_allow_html=True)
st.info(f"**{s.metadata.get('business_name')}** · {s.industry} · data: **{s.data_label}** · "
        f"last data date {s.origin.date()}. Business records are synthetic unless you uploaded "
        "your own; nothing here is validated on a real business.", icon="🏪")
if "nav" not in st.session_state:  # deep links: ?page=Scenario%20Lab
    st.session_state["nav"] = st.query_params.get("page") if st.query_params.get("page") in PAGES else PAGES[0]
    if st.query_params.get("section") in RO_SECTIONS:
        st.session_state["seg_ro_section"] = st.query_params.get("section")
page = st.radio("Navigate", PAGES, horizontal=True, key="nav", label_visibility="collapsed")


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


# ================================================================================ OVERVIEW
def page_overview() -> None:
    top = st.columns([3, 2])
    top[0].markdown('<div class="section-kicker">Page 1 · What is happening now</div>', unsafe_allow_html=True)
    top[0].markdown("# Business Overview")
    with top[1]:
        period = seg("Period", list(dash.PERIODS), "seg_ov_period", "mtd", lambda k: dash.PERIODS[k])
    k = dash.kpis(s, period)
    st.caption(f"{k['period_label']} compared with {k['comparison_label']}. "
               "Click a card's link for the detailed view.")
    r1 = st.columns(3)
    r2 = st.columns(3)
    rev, prof, exp = k["revenue"], k["net_profit"], k["expenses"]
    with r1[0], st.container(key="kpi_revenue"):
        st.markdown(T.kpi_html("Revenue", T.money(rev["value"]), chip_html=T.chip(rev["change_pct"]),
                               sub=f"vs {T.money(rev['previous'])}", tag=T.origin("observed")),
                    unsafe_allow_html=True)
        st.altair_chart(charts.sparkline(pd.DataFrame(rev["spark"]).assign(
            date=lambda d: pd.to_datetime(d["date"])), "revenue"), width="stretch")
        st.button("Revenue details →", key="go_revenue", on_click=go,
                  args=("Revenue & Operations",), kwargs={"seg_ro_section": "Revenue"})
    with r1[1], st.container(key="kpi_profit"):
        st.markdown(T.kpi_html("Net operating profit", T.money(prof["value"]),
                               chip_html=T.chip(prof["change_pct"]),
                               sub=f"{T.pct(prof['margin_pct'])} margin (was {T.pct(prof['previous_margin_pct'])})",
                               tag=T.origin("observed")), unsafe_allow_html=True)
        st.caption(prof["definition"])
        st.button("Profit drivers →", key="go_profit", on_click=go,
                  args=("Revenue & Operations",), kwargs={"seg_ro_section": "Profit & costs"})
    with r1[2], st.container(key="kpi_cash"):
        c = k["cash"]
        run = ("runway n/a: cash flow positive" if c.get("runway_days") is None
               else f"runway ≈ {c['runway_days']:.0f} days")
        st.markdown(T.kpi_html("Cash on hand", T.money(c["value"]), sub=run, tag=T.origin("observed")),
                    unsafe_allow_html=True)
        st.caption(c["source"] + ". " + c.get("runway_note", ""))
        st.button("Cash & obligations →", key="go_cash", on_click=go,
                  args=("Revenue & Operations",), kwargs={"seg_ro_section": "Cash & obligations"})
    with r2[0], st.container(key="kpi_expenses"):
        st.markdown(T.kpi_html("Total expenses", T.money(exp["value"]),
                               chip_html=T.chip(exp["change_pct"], good_when_up=False),
                               sub=f"fixed {T.money(exp['fixed'])} · variable {T.money(exp['variable'])}",
                               tag=T.origin("observed")), unsafe_allow_html=True)
        st.caption("Accrual costs: variable scale with sales; fixed follow the bill schedule.")
        st.button("Expense breakdown →", key="go_expenses", on_click=go,
                  args=("Revenue & Operations",), kwargs={"seg_ro_section": "Profit & costs"})
    with r2[1], st.container(key="kpi_inventory"):
        inv = k["inventory"]
        low = inv.get("low_stock") or []
        warn = (f'<span class="chip warn">⚠ low: {", ".join(x["ingredient"] for x in low)}</span>'
                if low else '<span class="chip up">no low-stock items</span>')
        st.markdown(T.kpi_html("Inventory (at cost)", T.money(inv["value"]), chip_html=warn,
                               sub="", tag=T.origin("observed")), unsafe_allow_html=True)
        st.caption(inv["basis"] + (f"; next delivery in {inv['next_delivery_in_days']} days."
                                   if inv.get("next_delivery_in_days") else "."))
        st.button("Inventory health →", key="go_inventory", on_click=go,
                  args=("Revenue & Operations",), kwargs={"seg_ro_section": "Inventory"})
    with r2[2], st.container(key="kpi_liabilities"):
        li = k["liabilities"]
        if li.get("payables") is None:
            st.markdown(T.kpi_html("Liabilities", "Unavailable", small=True), unsafe_allow_html=True)
            st.caption(li["note"])
        else:
            st.markdown(T.kpi_html("Supplier payables", T.money(li["payables"]),
                                   sub=f"due in next 14 days: <b>{T.money(li['due_14d'])}</b>",
                                   tag=T.origin("observed")), unsafe_allow_html=True)
            st.caption(f"Net-{li['payment_terms_days']} supplier invoices plus scheduled bills. "
                       "Loan principal is not in the data, so total debt is unavailable.")
        st.button("Obligations →", key="go_liabilities", on_click=go,
                  args=("Revenue & Operations",), kwargs={"seg_ro_section": "Cash & obligations"})

    st.markdown("### Revenue and profit over time")
    win = seg("Window", ["7D", "30D", "90D", "1Y"], "seg_ov_window", "90D")
    days = {"7D": 7, "30D": 30, "90D": 90, "1Y": 365}[win]
    ts = dash.revenue_profit_series(s.history, days)
    with st.container(key="panel_rp"):
        st.altair_chart(charts.line_hover(ts, {"Revenue": "revenue", "Operating profit": "profit"},
                                          colors={"Revenue": T.COBALT, "Operating profit": T.ORANGE},
                                          zero_rule=True, height=300), width="stretch")
        st.caption(f"{ts['granularity'].iloc[0].capitalize()} values · operating profit = revenue − "
                   "variable − fixed costs (accrual). Days with negative profit are normal for a "
                   "restaurant early in the week.")
    ctx = product_ctx()
    c1, c2 = st.columns(2)
    with c1, st.container(key="panel_prodrev"):
        st.markdown("#### Revenue by product " + T.origin("observed"), unsafe_allow_html=True)
        if ctx is None:
            empty_state("No product breakdown for this dataset.")
        else:
            t = dash.product_table(s, period)
            other = float(dash._slice(ctx.history.recon, *_bounds(period))["other_items_revenue"].sum())
            df = pd.concat([t[["product", "revenue", "units_sold"]],
                            pd.DataFrame([{"product": "Other items", "revenue": other, "units_sold": np.nan}])])
            df["share_pct"] = df["revenue"] / df["revenue"].sum() * 100
            df["label"] = [f"{u:,.0f} units" if pd.notna(u) else "sides, add-ons" for u in df["units_sold"]]
            st.altair_chart(charts.product_revenue(df), width="stretch")
            st.caption("Five tracked products plus 'other items' (sides, add-ons, desserts); together "
                       "they equal total revenue.")
    with c2, st.container(key="panel_exp"):
        st.markdown("#### Expense breakdown " + T.origin("observed"), unsafe_allow_html=True)
        e = dash.expense_breakdown(s, period)
        e["pct_label"] = e["share_pct"].map(lambda v: f"{v:.0f}%")
        st.altair_chart(charts.expense_bars(e), width="stretch")
    c3, c4 = st.columns([1, 1])
    with c3, st.container(key="panel_inv"):
        st.markdown("#### Inventory health " + T.origin("simulated"), unsafe_allow_html=True)
        if ctx is None:
            empty_state("No product inventory for this dataset.")
        else:
            t = dash.product_table(s, period)
            nd = dash.next_delivery_days(ctx.origin)
            st.altair_chart(charts.inventory_health(views.inventory_frame(t, nd)), width="stretch")
            st.caption(f"Bars: units the ingredients on hand can make at the forecast menu mix "
                       f"(physical stock). Black tick: forecast demand until the next delivery in "
                       f"{nd} days (prediction). Colour: share of simulated weeks with unmet demand — a "
                       "simulation frequency, not a validated probability (sell-outs are too rare in "
                       "the history to check it).")
    with c4, st.container(key="panel_bi"):
        st.markdown("#### Business intelligence")
        if ctx is None:
            empty_state("Product insights are unavailable.")
        else:
            for ins in dash.insights(s, "28d"):
                kind = "simulated" if "simulated" in ins["basis"] else "observed"
                st.markdown(f'<div class="insight-title">{T.esc(ins["title"])} {T.origin(kind)}</div>'
                            f'<div class="insight-text">{T.esc(ins["text"])}</div>', unsafe_allow_html=True)
    if s.industry == "restaurant":
        rec = _engine_recommendation()
        with st.container(key="card_rec"):
            st.markdown('<div class="insight-title">Recommended action (decision engine, next 28 '
                        f'days) {T.origin("simulated")}</div>', unsafe_allow_html=True)
            top = next((a for a in rec["actions"] if a["action_id"] == rec["recommended_action"]), None)
            if top:
                lo, hi = top["change_90pct_range"]
                st.markdown(f'<div class="insight-text">{T.esc(top["label"])}: expected profit change '
                            f'{T.signed_money(top["change_vs_no_action"])} vs doing nothing (90% range '
                            f'{T.money(lo)} to {T.money(hi)}); strength: {T.esc(rec["strength"])}.</div>',
                            unsafe_allow_html=True)
            st.caption("Ranked by the existing restaurant decision engine under the current scenario. "
                       "Action effects are assumptions, not proven causal effects; your approval is required.")
            st.button("Test it in the Scenario Lab →", key="go_lab", on_click=go, args=("Scenario Lab",))


def _bounds(period):
    w = dash.window(s.history, period)
    return w.start, w.end


def _engine_recommendation() -> dict:
    key = ("ov_rec", repr(asdict(s.state)))
    if st.session_state.get("ov_rec_key") != key:
        st.session_state["ov_rec"] = run_tool(s, "evaluate_restaurant_actions", {})
        st.session_state["ov_rec_key"] = key
    return st.session_state["ov_rec"]


# ================================================================================ REVENUE & OPS
def page_revenue_ops() -> None:
    st.markdown('<div class="section-kicker">Page 2 · Why it is happening</div>', unsafe_allow_html=True)
    st.markdown("# Revenue & Operations")
    section = seg("Section", RO_SECTIONS, "seg_ro_section", "Revenue")
    ctx = product_ctx()
    h = s.history
    end = h["date"].max().date()
    f1, f2 = st.columns([2, 3])
    dr = f1.date_input("Date range", value=(end - timedelta(days=89), end),
                       min_value=h["date"].min().date(), max_value=end, key="ro_dates")
    start, stop = (pd.Timestamp(dr[0]), pd.Timestamp(dr[1])) if isinstance(dr, tuple) and len(dr) == 2 \
        else (pd.Timestamp(end) - pd.Timedelta(days=89), pd.Timestamp(end))
    prods = None
    if ctx is not None:
        names = [p for p in views.NAMES.values()]
        prods = f2.multiselect("Products", names, default=names, key="ro_products")
    cur = dash._slice(h, start, stop)
    span = (stop - start).days + 1
    prev = dash._slice(h, start - pd.Timedelta(days=span), start - pd.Timedelta(days=1))

    if section == "Revenue":
        rev, prev_rev = cur["revenue"].sum(), prev["revenue"].sum()
        gp_cur = gp_prev = None
        if ctx is not None:
            sc = dash._slice(ctx.history.sales, start, stop)
            sp = dash._slice(ctx.history.sales, start - pd.Timedelta(days=span), start - pd.Timedelta(days=1))
            if prods:
                sc, sp = sc[sc["product"].isin(prods)], sp[sp["product"].isin(prods)]
            rc = dash._slice(ctx.history.recon, start, stop)
            gp_cur = rev - rc["food_cost"].sum()
            gp_prev = prev_rev - dash._slice(ctx.history.recon, start - pd.Timedelta(days=span),
                                             start - pd.Timedelta(days=1))["food_cost"].sum()
        m = st.columns(5)
        m[0].metric("Revenue", T.money(rev), T.pct((rev - prev_rev) / prev_rev * 100 if prev_rev else None, signed=True),
                    help="Total sales in the range vs the preceding range of equal length.")
        guests = cur["units_sold"].sum() if "units_sold" in cur else None
        m[1].metric("Guests served" if s.industry == "restaurant" else "Units sold",
                    f"{guests:,.0f}" if guests is not None else "n/a")
        m[2].metric("Average revenue per guest" if s.industry == "restaurant" else "Revenue per unit",
                    T.money(rev / guests, 2) if guests else "n/a",
                    help="No transaction-level data exists, so this is revenue / guests (covers), "
                         "not a true average ticket per transaction.")
        if gp_cur is not None:
            m[3].metric("Gross profit", T.money(gp_cur),
                        T.pct((gp_cur - gp_prev) / gp_prev * 100 if gp_prev else None, signed=True),
                        help="Revenue minus food (ingredient) cost. Before labor, rent and overhead.")
            m[4].metric("Gross margin", T.pct(gp_cur / rev * 100), help="Gross profit / revenue.")
        else:
            gp = rev - cur["variable_costs"].sum()
            m[3].metric("Revenue minus variable costs", T.money(gp))
            m[4].metric("Contribution margin", T.pct(gp / rev * 100 if rev else None))
        with st.container(key="panel_revtrend"):
            d = cur[["date", "revenue"]].copy()
            d["avg7"] = d["revenue"].rolling(7, min_periods=1).mean()
            st.altair_chart(charts.line_hover(d, {"Daily revenue": "revenue", "7-day average": "avg7"},
                                              colors={"Daily revenue": T.BASELINE, "7-day average": T.COBALT},
                                              height=280), width="stretch")
        if ctx is not None:
            with st.container(key="panel_contrib"):
                st.markdown("#### Revenue contribution by product (weekly) " + T.origin("observed"),
                            unsafe_allow_html=True)
                w = views.weekly_product_revenue(ctx.history.sales, ctx.history.recon, start, stop,
                                                 (prods or []) + ["Other items"] if prods else None)
                st.altair_chart(charts.stacked_area(w), width="stretch")
                tot = sc.groupby("product")[["revenue", "units_sold"]].sum().reset_index()
                tot["share"] = tot["revenue"] / tot["revenue"].sum() * 100
                st.dataframe(tot.sort_values("revenue", ascending=False), hide_index=True, width="stretch",
                             column_config={"product": "Product",
                                            "revenue": st.column_config.NumberColumn("Revenue", format="$%.0f"),
                                            "units_sold": st.column_config.NumberColumn("Units sold", format="%d"),
                                            "share": st.column_config.ProgressColumn(
                                                "Share of tracked revenue", format="%.1f%%", min_value=0, max_value=100)})

    elif section == "Products":
        if ctx is None:
            return empty_state("No product table for this dataset.")
        period = st.radio("Sales period for the table", ["28d", "90d", "365d"], horizontal=True,
                          key="seg_ro_tbl", format_func=lambda k: dash.PERIODS[k])
        t = dash.product_table(s, period)
        show = t.assign(stockout_risk_7d=t["stockout_risk_7d"] * 100)[
            ["product", "price", "unit_cost", "units_sold", "revenue", "gross_margin_pct",
             "sellable_units", "forecast_demand_7d", "stockout_risk_7d", "days_of_supply"]]
        st.markdown("#### Five tracked products — select a row for the product view")
        ev = st.dataframe(show, hide_index=True, width="stretch", on_select="rerun",
                          selection_mode="single-row", key="prod_table", column_config={
                              "product": st.column_config.TextColumn("Product"),
                              "price": st.column_config.NumberColumn("Price", format="$%.2f", help="Current menu price"),
                              "unit_cost": st.column_config.NumberColumn("Unit cost", format="$%.2f", help="Ingredient cost per unit (recipe x current ingredient prices)"),
                              "units_sold": st.column_config.NumberColumn("Units sold", format="%d", help="Recorded sales in the period"),
                              "revenue": st.column_config.NumberColumn("Revenue", format="$%.0f"),
                              "gross_margin_pct": st.column_config.ProgressColumn("Gross margin", format="%.1f%%", min_value=0, max_value=100, help="(revenue − ingredient cost) / revenue"),
                              "sellable_units": st.column_config.NumberColumn("Available stock (units)", format="%.0f", help="Units the ingredients on hand can make at the forecast menu mix"),
                              "forecast_demand_7d": st.column_config.NumberColumn("Forecast demand (7 d)", format="%.0f", help="Model forecast of customer demand, next 7 days"),
                              "stockout_risk_7d": st.column_config.ProgressColumn("Stock-out risk (7 d)", format="%.0f%%", min_value=0, max_value=100, help="Share of simulated weeks with ≥1% of demand unmet under the current ordering plan. A simulation frequency, not a validated probability."),
                              "days_of_supply": st.column_config.NumberColumn("Days of supply", format="%.1f")})
        rows = (ev.selection.rows if ev is not None and hasattr(ev, "selection") else []) or []
        if rows:
            st.session_state["prod_pick"] = t.iloc[rows[0]]["product"]
        st.session_state.setdefault("prod_pick", t.iloc[0]["product"])
        name = st.selectbox("Product view", list(views.NAMES.values()), key="prod_pick")
        product_detail(ctx, name, t)

    elif section == "Inventory":
        if ctx is None:
            inv = dash.inventory_kpi(s)
            st.metric("Inventory value", T.money(inv["value"]), help=inv["basis"])
            return empty_state("No ingredient ledger for this dataset.")
        inv = dash.inventory_kpi(s)
        st.markdown(f"#### Ingredients on hand (end of {ctx.origin.date()}) " + T.origin("observed"),
                    unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(inv["items"]).round(2), hide_index=True, width="stretch")
        st.caption(f"Next delivery in {inv['next_delivery_in_days']} days (Mon/Thu, order-up-to 1.35× "
                   "expected usage). Days of supply use the forecast usage of all five products.")
        led = ctx.history.ledger
        led = led[(led["date"] >= start) & (led["date"] <= stop)].copy()
        avg_use = led.groupby("ingredient")["used"].transform(lambda x: x.rolling(28, min_periods=7).mean())
        led["days_of_supply"] = led["closing"] / avg_use.replace(0, np.nan)
        with st.container(key="panel_ingr"):
            st.altair_chart(charts.ingredient_history(led.dropna(subset=["days_of_supply"])), width="stretch")
            st.caption("Recorded stock after each day, in days of recent average usage. Deliveries "
                       "make the saw-tooth; drops toward zero are near sell-outs.")
        from decisiondesk.products import BOM, INGREDIENTS
        st.markdown("#### Recipes (bill of materials) " + T.origin("assumed"), unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(BOM, index=list(views.NAMES.values()),
                                  columns=[f"{k.name} ({k.unit})" for k in INGREDIENTS]),
                     width="stretch")

    elif section == "Profit & costs":
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
            st.dataframe(pd.DataFrame(pc["products"]), hide_index=True, width="stretch")
        e = dash.expense_breakdown(s, "28d")
        st.markdown("#### Expenses, last 28 days")
        st.dataframe(e.round(1), hide_index=True, width="stretch")

    elif section == "Cash & obligations":
        if "cash_balance" not in h:
            st.info("No cash ledger in this dataset.")
        else:
            with st.container(key="panel_cash"):
                d = cur[["date", "cash_balance"]]
                st.altair_chart(charts.line_hover(d, {"Cash balance (ledger)": "cash_balance"},
                                                  colors={"Cash balance (ledger)": T.COBALT}, height=260),
                                width="stretch")
                st.caption("SYNTHETIC cash ledger: declared opening balance + recorded inflows and "
                           "outflows (including owner distributions). Not a bank balance.")
        li = dash.liabilities(s)
        if li.get("payables") is not None:
            m = st.columns(3)
            m[0].metric("Supplier payables", T.money(li["payables"]), help=f"Net-{li['payment_terms_days']} invoices not yet paid")
            m[1].metric("Due in next 14 days", T.money(li["due_14d"]))
            m[2].metric("Loan principal", "not in data")
            st.markdown("#### Scheduled bills, next 14 days " + T.origin("forecast"), unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(li["scheduled_bills"]), hide_index=True, width="stretch")
            st.caption(li["note"])

    elif section == "Forecasts":
        targets = [t for t in ("revenue", "units_sold", "variable_costs", "net_cash_flow")
                   if t == "net_cash_flow" or t in h.columns]
        target = st.selectbox("Metric", targets, key="fc_target")
        with st.spinner("Running chronological validation..."):
            fc = run_tool(s, "forecast_business", {"target": target})
        v = fc["interval_80_total_validation"]
        a, b, c3, c4 = st.columns(4)
        a.metric("Next 28 days (point)", f"{fc['point_total']:,.0f}")
        b.metric("80% range for the 28-day total", f"{fc['interval_80_total'][0]:,.0f} – {fc['interval_80_total'][1]:,.0f}",
                 help=fc["interval_method"])
        c3.metric("Range check: past 28-day totals inside", f"{v['total_hits']}/{v['windows']}",
                  f"{v['total_coverage']:.0%} (target 80%)", delta_color="off", delta_arrow="off",
                  help=v["scheme"] + ". 95% uncertainty of this coverage estimate: "
                       f"{v['total_coverage_95ci'][0]:.0%}–{v['total_coverage_95ci'][1]:.0%}.")
        c4.metric("Final-test WAPE (point accuracy)", f"{fc['final_test']['wape']}")
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
        st.caption(f"Band: 10th–90th percentile of simulated days (daily ranges cover "
                   f"{v['daily_coverage']:.0%} of past out-of-sample days). The 28-day total range "
                   "above is a different, wider quantity: a sum of correlated days.")
        st.write(f"Selected by validation: **{fc['selected_model']}** — {fc['selection_reason']}")
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
        st.markdown("#### Recorded sales vs estimated demand (last 120 days) " + T.origin("observed"),
                    unsafe_allow_html=True)
        st.altair_chart(charts.sales_demand_history(ps[["date", "units_sold", "demand_est"]]), width="stretch")
        n_so = int(ps["stockout"].sum())
        st.caption(f"{n_so} sell-out day(s) in this window. On a sell-out day recorded sales are capped by "
                   "stock; estimated demand corrects them using the POS sell-out time and an assumed "
                   "intraday demand curve. Demand and sales are not the same thing.")
    with c2, st.container(key="panel_pd_price"):
        st.markdown("#### Menu price and ingredient cost " + T.origin("observed"), unsafe_allow_html=True)
        st.altair_chart(charts.price_history(s_[s_["product_id"] == pid][["date", "price", "unit_cost"]]),
                        width="stretch")
        st.caption("Black: menu price (step changes). Orange dashed: ingredient cost per unit.")
    with st.container(key="panel_pd_ds"):
        st.altair_chart(charts.demand_supply(views.demand_supply_frame(b28, pid),
                                             title=f"{name}: forecast demand vs supply, next 28 days (current plan)"),
                        width="stretch")
        st.caption("Blue: model forecast of customer demand (band = 10th–90th percentile of simulated "
                   "paths). Orange: units this product's share of the ingredients on hand each morning "
                   "allows (shared ingredients split at that day's menu mix), including planned Mon/Thu "
                   "deliveries. Black dashed: expected sales. Red markers: days when at least a quarter "
                   "of simulated paths run short. Estimated stock-out date: none within 28 days unless "
                   "shown above (month-day).")
    use = pd.DataFrame({"ingredient": [k.name for k in INGREDIENTS], "per unit": BOM[i],
                        "next 7 days (forecast)": BOM[i] * row["forecast_demand_7d"],
                        "unit": [k.unit for k in INGREDIENTS]})
    st.markdown("#### Expected ingredient consumption " + T.origin("forecast"), unsafe_allow_html=True)
    st.dataframe(use[use["per unit"] > 0].round(2), hide_index=True, width="stretch")
    b = ctx.boards[pid]
    from decisiondesk.calibration import product_validation
    v7, v28 = product_validation(ctx, pid, 7), product_validation(ctx, pid, 28)
    st.caption(f"Demand model: {b['selected_model']} — {b['selection_reason']}. Final-test MAE "
               f"{b['test_mae']:.1f} units/day (seasonal-naive {b['seasonal_naive_test_mae']:.1f}). "
               f"Monte Carlo demand ranges, checked out of sample: {v7.total_hits}/{v7.windows} past "
               f"7-day totals and {v28.total_hits}/{v28.windows} past 28-day totals inside their 80% "
               f"range; daily coverage {v28.daily_coverage:.0%}. Synthetic data: this tests the "
               "software, not real-world accuracy.")


# ================================================================================ SCENARIO LAB
def _sync(src: str, dst: str, lo: float, hi: float) -> None:
    st.session_state[dst] = float(min(hi, max(lo, st.session_state[src])))


def page_scenario_lab() -> None:
    st.markdown('<div class="section-kicker">Page 3 · What happens if I change something</div>',
                unsafe_allow_html=True)
    st.markdown("# Scenario Lab")
    ctx = product_ctx()
    if ctx is None:
        empty_state("The product Scenario Lab is unavailable.")
    else:
        lab(ctx)
    st.markdown("---")
    business_wide_actions()


def _apply_link_params() -> None:
    """Shareable scenario links, e.g. ?page=Scenario%20Lab&product=Tacos&price=10 (first load only)."""
    if st.session_state.get("lab_link_applied"):
        return
    st.session_state["lab_link_applied"] = True
    q = st.query_params
    if q.get("product") in views.NAMES.values():
        st.session_state["sl_product"] = q.get("product")
    for name, key, lo, hi in (("price", "sl_price", -30, 30), ("supply", "sl_supply", -100, 100),
                              ("extra", "sl_extra", 0, 5000), ("shock", "sl_shock", -50, 50),
                              ("marketing", "sl_mkt", -100, 100)):
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
    for k, v in {"sl_price": 0.0, "sl_price_num": 0.0, "sl_supply": 0.0, "sl_extra": 0.0,
                 "sl_shock": 0.0, "sl_mkt": 0.0}.items():
        st.session_state.setdefault(k, v)
    left, right = st.columns([1, 2.3], gap="large")
    with left, st.container(key="panel_controls"):
        st.markdown("#### Scenario controls")
        name = st.selectbox("Product", list(views.NAMES.values()), key="sl_product")
        pid = next(k for k, v in views.NAMES.items() if v == name)
        i = PRODUCT_IDS.index(pid)
        cap = (ctx.max_price_increase or 0.05) * 100
        st.slider("Price change (%)", -30.0, 30.0, step=1.0, key="sl_price",
                  on_change=_sync, args=("sl_price", "sl_price_num", -30.0, 30.0))
        st.number_input("Price change (%), exact", -30.0, 30.0, step=0.5, key="sl_price_num",
                        on_change=_sync, args=("sl_price_num", "sl_price", -30.0, 30.0))
        p = st.session_state["sl_price"]
        new_price = round(ctx.prices[i] * (1 + p / 100), 2)
        tag = ('<span class="chip down">outside approved limit</span>' if p > cap + 1e-9
               else '<span class="chip up">within policy</span>')
        st.markdown(f'<div class="kpi-sub">{T.money(ctx.prices[i], 2)} → <b>{T.money(new_price, 2)}</b> '
                    f'{tag}<br>Owner limit: price increases up to {cap:g}%.</div>', unsafe_allow_html=True)
        st.slider("Planned ingredient orders for this product (%)", -100.0, 100.0, step=5.0, key="sl_supply",
                  help="Scales this product's share of every planned Mon/Thu ingredient order.")
        h = st.session_state.get("seg_sl_h", 28)
        fc_units = float(ctx.bundle.targets[f"demand_{pid}"].point[:h].clip(0).sum())
        st.caption(f"≈ {st.session_state['sl_supply'] / 100 * 1.35 * fc_units:+,.0f} units of "
                   f"{name.lower()} ingredients over {h} days (forecast demand {fc_units:,.0f} units).")
        st.number_input("One-time extra ingredient purchase (units)", 0.0, 5000.0, step=25.0, key="sl_extra",
                        help="Bought today, arrives tomorrow. The order-up-to rule then orders less.")
        st.caption(f"Cost {T.money(st.session_state['sl_extra'] * ctx.unit_cost[i])} at current ingredient prices.")
        st.slider("Demand shock (%)", -50.0, 50.0, step=5.0, key="sl_shock",
                  help="A hypothetical change in customer demand (weather, an event, a competitor).")
        scope = st.radio("Shock applies to", ["This product", "All products"], horizontal=True, key="sl_scope")
        st.slider("Marketing & software spend (%)", -100.0, 100.0, step=10.0, key="sl_mkt")
        st.caption(f"Ledger spend {T.money(ctx.marketing_monthly)}/month → "
                   f"{T.money(ctx.marketing_monthly * (1 + st.session_state['sl_mkt'] / 100))}/month.")
        st.markdown("**Forecast horizon**")
        h = seg("Horizon", [7, 14, 28], "seg_sl_h", 28, lambda d: f"{d} days")
        st.button("Reset to current plan", key="sl_reset", on_click=_reset_controls)
    sc = ProductScenario(pid, float(st.session_state["sl_price"]), float(st.session_state["sl_supply"]),
                         float(st.session_state["sl_extra"]), float(st.session_state["sl_shock"]),
                         "product" if scope == "This product" else "all",
                         float(st.session_state["sl_mkt"]), int(h))
    s.state.product_scenario = asdict(sc)
    t0 = time.perf_counter()
    base = baseline(ctx, h, pid)
    scen = simulate(ctx, sc)
    cmp_ = compare(ctx, scen, base)
    ms = (time.perf_counter() - t0) * 1000
    with right:
        st.markdown(f"#### {T.esc(sc.describe()).capitalize()} · next {h} days "
                    f"({cmp_['forecast_start']} to {cmp_['forecast_end']}) " + T.origin("simulated"),
                    unsafe_allow_html=True)
        if not cmp_["within_policy"]:
            st.markdown('<div class="callout bad"><b>Outside the owner\'s approved limits:</b> '
                        + T.esc("; ".join(cmp_["policy_violations"])) + ". Shown for exploration only; "
                        "it is excluded from recommendations.</div>", unsafe_allow_html=True)
        cards = [
            ("Revenue", cmp_["revenue"], True, T.money),
            ("Operating profit", cmp_["profit"], True, T.money),
            (f"{name} demand (units)", cmp_["demand_units"], True, lambda v: f"{v:,.0f}"),
            (f"{name} units sold", cmp_["units_sold"], True, lambda v: f"{v:,.0f}"),
        ]
        g = st.columns(4)
        for j, (col, (lab_, blk, up_good, f)) in enumerate(zip(g, cards)):
            with col, st.container(key=f"card_cmp_a{j}"):
                chg = blk["change"]
                st.markdown(T.compare_html(lab_, f(blk["baseline"]), f(blk["scenario"]),
                                           T.chip(blk["change_pct"], good_when_up=up_good),
                                           f"{'+' if chg > 0 else ''}{f(chg)}"), unsafe_allow_html=True)
        g2 = st.columns(4)
        gm = cmp_["gross_margin_pct"]
        sr = cmp_["stockout_risk"]
        inv = cmp_["ending_inventory_value"]
        cf = cmp_["cash_flow"]
        extra = [
            ("Gross margin", f"{gm['baseline']:.1f}%", f"{gm['scenario']:.1f}%",
             f'<span class="chip flat">{gm["change_points"]:+.2f} pts</span>', "after ingredients, packaging, card fees"),
            (f"{name} stock-out risk", f"{sr['baseline']:.0%}", f"{sr['scenario']:.0%}",
             T.chip((sr["scenario"] - sr["baseline"]) * 100, good_when_up=False, suffix=" pts")
             if sr["scenario"] != sr["baseline"] else '<span class="chip flat">no change</span>',
             f"lost units {cmp_['lost_units']['baseline']:,.0f} → {cmp_['lost_units']['scenario']:,.0f}"),
            ("Ingredient inventory left", T.money(inv["baseline"]), T.money(inv["scenario"]),
             T.chip(inv["change_pct"]), f"≈ {cmp_['ending_makeable_units_selected']:,.0f} {name.lower()} makeable"),
            ("Operating cash flow", T.money(cf["baseline"]), T.money(cf["scenario"]),
             T.chip(cf["change_pct"]), f"{T.signed_money(cf['change'])} cash vs plan"),
        ]
        for j, (col, (lab_, b_, n_, ch_, note)) in enumerate(zip(g2, extra)):
            with col, st.container(key=f"card_cmp_b{j}"):
                st.markdown(T.compare_html(lab_, b_, n_, ch_, T.esc(note)), unsafe_allow_html=True)
        lo, hi = cmp_["profit"]["change_90pct_range"]
        st.markdown(f'<div class="callout{" good" if cmp_["profit"]["change"] > 0 else ""}">Profit change '
                    f'<b>{T.signed_money(cmp_["profit"]["change"])}</b> (90% simulation range '
                    f'{T.signed_money(lo)} to {T.signed_money(hi)}); profit improves in '
                    f'<b>{cmp_["probability_profit_improves"]:.0%}</b> of {cmp_["n_simulations"]:,} simulated '
                    f'futures. Cash and profit differ because ingredient purchases are paid net-7 and '
                    f'unsold stock stays on the shelf. <span class="legend-row">Computed in {ms:.0f} ms.</span></div>',
                    unsafe_allow_html=True)
        rv = cmp_["range_validation"]
        st.caption(f"Range check ({h}-day totals, forecast-error part, out of sample): revenue "
                   f"{rv['revenue_total']['total_hits']}/{rv['revenue_total']['windows']}, profit "
                   f"{rv['profit_total']['total_hits']}/{rv['profit_total']['windows']} past windows inside "
                   f"their 80% range. NOT validated: the assumption ranges and the difference between "
                   f"options (no counterfactual outcomes exist).")
        tabs = st.tabs(["Revenue & profit", "Demand vs supply", "Product impact", "Uncertainty"])
        with tabs[0]:
            c1, c2 = st.columns(2)
            c1.altair_chart(charts.band_compare(views.band_frame(scen, base, "revenue"), title="Daily revenue"),
                            width="stretch")
            c2.altair_chart(charts.band_compare(views.band_frame(scen, base, "profit"), title="Daily operating profit"),
                            width="stretch")
            st.caption("Lines: mean of simulated paths; bands: 10th–90th percentile of simulated days "
                       f"(daily ranges cover {rv['revenue_total']['daily_coverage']:.0%} of past out-of-sample "
                       "days for tracked revenue). Grey dashed = current plan.")
        with tabs[1]:
            st.altair_chart(charts.demand_supply(views.demand_supply_frame(scen, pid),
                                                 title=f"{name}: demand vs supply under the scenario"),
                            width="stretch")
            st.caption("When demand (blue) is above what the morning stock allows (orange), sales are "
                       "capped; a share of the unmet demand switches to other main dishes and the rest is lost.")
        with tabs[2]:
            st.altair_chart(charts.impact_bars(views.impact_frame(cmp_)), width="stretch")
            st.caption("Change vs the current plan for each product (mean of simulated paths). Shared "
                       "ingredients and substitution mean one product's change moves the others.")
        with tabs[3]:
            d = scen.profit.sum(1) - base.profit.sum(1)
            st.altair_chart(charts.delta_histogram(d, lo, hi), width="stretch")
            st.caption(cmp_["interval_meaning"] + " It combines (1) the demand models' out-of-sample "
                       "back-test errors and (2) the assumption ranges below, using the same "
                       f"{cmp_['n_simulations']:,} paths for the plan and the scenario (seed {cmp_['seed']}). "
                       + rv["note"])

    # Recommendation
    rec = _cached_recommend(ctx, sc, recommend)
    with st.container(key="panel_rec"):
        st.markdown("### Decision recommendation " + T.origin("simulated"), unsafe_allow_html=True)
        st.markdown(f'<div class="insight-text">{T.esc(rec["text"])}</div>', unsafe_allow_html=True)
        c1, c2 = st.columns([3, 2])
        with c1:
            st.markdown("**Options tested (each vs the current plan, within the owner's limits)**")
            if rec["ranked"]:
                st.dataframe(pd.DataFrame([{
                    "action": r["action"], "profit change": round(r["profit_change"]),
                    "90% range": f"{T.money(r['profit_change_90pct_range'][0])} to {T.money(r['profit_change_90pct_range'][1])}",
                    "P(better)": f"{r['probability_profit_improves']:.0%}",
                    "units sold change": round(r["units_sold_change"]),
                    "stock-out risk": f"{r['stockout_risk']:.0%}"} for r in rec["ranked"]]),
                    hide_index=True, width="stretch")
            for r in rec["outside_policy"]:
                st.caption(f"Excluded: {r['action']} — {'; '.join(r['policy_violations'])} "
                           f"(would change profit by {T.signed_money(r['profit_change'])}).")
        with c2:
            st.markdown("**Risks of this scenario**")
            risks = []
            if cmp_["units_sold"]["change"] < -0.5:
                risks.append(f"Fewer {name.lower()} sold ({cmp_['units_sold']['change']:,.0f} units).")
            if cmp_["stockout_risk"]["scenario"] > cmp_["stockout_risk"]["baseline"] + 0.02:
                risks.append("Higher stock-out risk than the current plan.")
            if cmp_["probability_profit_improves"] < 0.8 and not sc.is_baseline:
                risks.append(f"Profit improves in only {cmp_['probability_profit_improves']:.0%} of simulated futures.")
            st.markdown("\n".join(f"- {r}" for r in risks) or "- No major risks flagged.")
            if rec["sensitivity"]:
                st.markdown("**Sensitivity of the recommended action**")
                st.markdown("\n".join(
                    f"- {x['assumption']} at its {x['level']} bound ({x['value']}): profit change "
                    f"{T.signed_money(x['profit_change'])}" for x in rec["sensitivity"]))
        e1, e2, e3 = st.columns(3)
        if e1.button("Use this scenario for iMessage answers", key="lab_publish"):
            _publish()
        e2.button("Ask the AI analyst about this →", key="go_analyst", on_click=go, args=("AI Analyst",),
                  kwargs={"analyst_prefill": f"What happens if {name.lower()} prices change "
                                             f"{sc.price_change_pct:+g}%?" if sc.price_change_pct else
                          "Which of my five products is most profitable?"})
        if st.session_state.get("lab_published"):
            e3.success(st.session_state["lab_published"])
    with st.expander("Evidence and calculations"):
        st.markdown("**Assumptions (counterfactual effects are assumed, never estimated)**")
        st.dataframe(pd.DataFrame([{k: a.get(k) for k in ("name", "value", "low", "high", "distribution", "source", "note")}
                                   for a in [asdict(ELASTICITY[pid]), asdict(SUBSTITUTION), asdict(MARKETING_RESPONSE)]]),
                     hide_index=True, width="stretch")
        st.markdown("**Demand forecast models (chronological validation, synthetic data)**")
        st.dataframe(pd.DataFrame([{"product": views.NAMES[p], **{k: b[k] for k in (
            "selected_model", "validation_mae", "test_mae", "seasonal_naive_test_mae",
            "test_interval_coverage_80")}} for p, b in ctx.boards.items()]).round(3), hide_index=True, width="stretch")
        from decisiondesk.decision import product_scenario as ps_mod
        st.code(ps_mod.__doc__.split("Per simulated path", 1)[1].strip(), language="text")
        st.caption(f"Simulations: {ctx.n_sims:,} paths, seed {cmp_['seed']}; forecasts are 28-day models "
                   "(7 and 14 days are the first days of the same forecast). Data: SYNTHETIC.")


def _reset_controls() -> None:
    for k in ("sl_price", "sl_price_num", "sl_supply", "sl_extra", "sl_shock", "sl_mkt"):
        st.session_state[k] = 0.0


def _cached_recommend(ctx, sc, recommend) -> dict:
    key = repr(sc)
    cache = st.session_state.setdefault("lab_rec_cache", {})
    if key not in cache:
        cache.clear()
        cache[key] = recommend(ctx, sc)
    return cache[key]


def _publish() -> None:
    from decisiondesk.messaging.shared import ScenarioStore
    rec = ScenarioStore().publish(st.session_state.get("business_key", "restaurant"), asdict(s.state))
    st.session_state["lab_published"] = (f"Published scenario v{rec['version']}: {rec['summary']}. "
                                         "iMessage replies use it from the owner's next message.")


def business_wide_actions() -> None:
    st.markdown("### Business-wide actions — decision engine " + T.origin("simulated"), unsafe_allow_html=True)
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
        st.dataframe(pd.DataFrame(out["actions"]), width="stretch", hide_index=True)
        with st.expander("Assumptions, constraints, sensitivity and limitations"):
            st.json({"scenario": out["scenario"], "assumptions": out["assumptions"],
                     "constraints": out["constraints"], "sensitivity": out["sensitivity"],
                     "stress_test": out["stress_test"], "limitations": out["limitations"]})
    elif out:
        st.error(out.get("reason"))


# ================================================================================ AI ANALYST
SUGGESTIONS = ["Why did profit drop?", "Which of my five products is most profitable?",
               "What happens if burrito prices increase by 5%?", "Should I purchase more taco ingredients?",
               "What should I do?"]


def _ask(q: str) -> None:
    agent: DecisionAgent = st.session_state["agent"]
    st.session_state["chat"].append({"role": "user", "text": q})
    if agent.mode == "offline":
        _append_reply(agent.ask(q))
    else:  # slow language model: run in the background so the dashboard stays usable
        st.session_state["pending"] = {"future": _executor().submit(agent.ask, q), "q": q,
                                       "t0": time.time()}


def _append_reply(reply) -> None:
    tools_used = ", ".join(f"{c['name']} (run {c['output']['run_id']})" for c in reply.tool_calls
                           if "output" in c) or "none"
    st.session_state["chat"].append({"role": "assistant", "text": reply.text + (
        f"\n\n_Tools: {tools_used} · grounded: {reply.grounded}_")})


def page_analyst() -> None:
    agent: DecisionAgent = st.session_state["agent"]
    st.markdown('<div class="section-kicker">Explains the numbers; never computes them</div>', unsafe_allow_html=True)
    st.markdown("# AI Analyst")
    model = f" ({agent.gemini.model})" if agent.gemini else ""
    st.markdown(f"**Active analyst: {agent.label}{model}**")
    if agent.mode == "gemini":
        st.caption("Gemini calls the forecasting, product and decision tools; it never computes "
                   "figures itself. Replies run in the background (live replies have taken 2–3 "
                   "minutes), so you can keep using the other pages. Free-tier prompts may be used "
                   "by Google to improve its products, so do not use real private data on the free tier.")
    elif agent.mode == "offline":
        st.caption("Rule-based analyst using the same tools. Set GEMINI_API_KEY for free-form questions.")
    if agent.last_fallback:
        st.warning(f"Last Gemini request failed ({agent.last_fallback}); that answer came from the "
                   "offline analyst.")
    st.caption("Every number in an answer is checked against tool outputs, including what each "
               "number is said to be (e.g. an elasticity must match an elasticity).")
    with st.container(horizontal=True, wrap=True, key="suggestions"):
        for j, q in enumerate(SUGGESTIONS):
            if st.button(q, key=f"suggest_{j}", width="content",
                         disabled=bool(st.session_state.get("pending"))):
                _ask(q)
    for msg in st.session_state["chat"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["text"])
    pending = st.session_state.get("pending")

    @st.fragment(run_every="2s" if pending else None)
    def waiting():
        p = st.session_state.get("pending")
        if not p:
            return
        if p["future"].done():
            try:
                _append_reply(p["future"].result())
            except Exception as exc:  # noqa: BLE001 - shown to the user, never silent
                st.session_state["chat"].append({"role": "assistant", "text": f"Analyst error: {exc}"})
            st.session_state["pending"] = None
            st.rerun()
        st.info(f"Gemini is working on “{p['q']}” ({time.time() - p['t0']:.0f} s). You can use other "
                "pages meanwhile; the answer appears here when it is ready.")
    waiting()
    pre = st.session_state.pop("analyst_prefill", None)
    if pre:
        st.info(f"Suggested from the Scenario Lab: “{pre}” — click below to ask.")
        if st.button(f"Ask: {pre}", key="ask_prefill"):
            _ask(pre)
            st.rerun()
    q = st.chat_input("Ask about forecasts, products, risks or decisions", key="chat_in",
                      disabled=bool(pending))
    if q:
        _ask(q)
        st.rerun()


# ================================================================================ ECONOMY
def page_economy() -> None:
    st.markdown('<div class="section-kicker">Real U.S. economic context</div>', unsafe_allow_html=True)
    st.markdown("# Economy " + T.origin("real"), unsafe_allow_html=True)
    live = st.button("Refresh from FRED API (live)", key="fred_live")
    econ = run_tool(s, "get_economic_indicators", {"refresh": bool(live)})
    st.caption(f"Observed U.S. data (FRED/ALFRED), as published on or before {econ['as_of']}. "
               + econ["not_used_in_forecasts"])
    if econ["observations"]:
        obs = pd.DataFrame(econ["observations"])
        m = st.columns(len(obs))
        for c, (_, o) in zip(m, obs.iterrows()):
            c.metric(o["series_id"], f"{o['value']:,.1f}", f"{o['change_12m']:+.2f} {'pts' if o['change_12m_unit'] != '%' else '%'} / 12 mo"
                     if o["change_12m"] is not None else None, delta_color="off", help=o["title"])
        st.dataframe(obs[["series_id", "title", "observed_at", "value", "units", "change_12m",
                          "change_12m_unit", "change_12m_period", "published_at", "retrieval",
                          "retrieved_at_utc", "source_url"]], width="stretch", hide_index=True)
    for n in econ["notes"]:
        st.warning(n)
    st.markdown('<div class="callout">How FRED is used: as real economic context and to size '
                'hypothetical stress tests (e.g. food costs moved by the latest food-at-home CPI '
                'change). FRED features did not improve the forecasts in chronological validation, '
                'so they are not model inputs. Kwick365 restaurant benchmarks power the separate '
                '<code>us_demo</code>/<code>us_validation</code> workflows, not this dashboard.</div>',
                unsafe_allow_html=True)
    st.caption("This product uses the FRED® API but is not endorsed or certified by the Federal "
               "Reserve Bank of St. Louis.")


# ================================================================================ MESSAGING
def page_messaging() -> None:
    from decisiondesk.messaging.gateway import PhotonGateway, PhotonUnavailable
    from decisiondesk.messaging.risk_signals import scan
    from decisiondesk.messaging.service import (
        ALERT_KINDS,
        MessagingService,
        MessagingStore,
        SimulatorGateway,
    )
    from decisiondesk.messaging.shared import PollerLock, describe_holder

    st.markdown('<div class="section-kicker">Photon iMessage</div>', unsafe_allow_html=True)
    st.markdown("# Messaging")
    agent: DecisionAgent = st.session_state["agent"]
    st.markdown(f"**Replies are written by: {agent.label}**")
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
    st.markdown(f"**Channel: {svc.channel_label}**")
    src = svc.scenario_source(bkey)
    with st.container(key="panel_msg_scn"):
        st.markdown("#### Scenario used for replies")
        st.markdown(f"**{'Published v' + str(src['version']) if src['version'] else 'Default'}:** "
                    f"{T.esc(src['summary'])}" + (f" · published {src['published_at']}" if src["published_at"] else ""))
        st.caption("The worker and the dashboard read the same published scenario. Publish from the "
                   "Scenario Lab, or publish the dashboard's current scenario here.")
        c1, c2 = st.columns(2)
        if c1.button("Publish the dashboard's current scenario", key="msg_publish"):
            _publish()
            st.rerun()
        if c2.button("Reset to the default scenario", key="msg_unpublish", disabled=not src["version"]):
            from decisiondesk.messaging.shared import ScenarioStore
            ScenarioStore().clear(bkey)
            st.rerun()
    if live:
        st.caption("Message poller: " + describe_holder(holder))
    if viewer:
        st.warning(f"Another process is answering messages ({describe_holder(holder)}). This page is "
                   "read-only to avoid two pollers; stop that process to manage messaging here.")
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
                          value=o.alerts_opt_in and not o.stopped, key=f"optin_{o.handle}_{skey}",
                          disabled=viewer)
        if not viewer and opt != (o.alerts_opt_in and not o.stopped):
            svc.set_alert_consent(o.handle, opt, "dashboard")
        o.alert_prefs = st.multiselect("Alert types", list(ALERT_KINDS), default=o.alert_prefs,
                                       key=f"prefs_{o.handle}_{skey}", disabled=viewer)
        if o.stopped:
            st.warning("This owner replied STOP; nothing will be sent until they reply START.")
        b1, b2 = st.columns(2)
        confirm = b1.checkbox("I confirm this is my authorized test phone", key=f"conf_{o.handle}_{skey}",
                              disabled=viewer)
        if b1.button("Send test message", key=f"test_{o.handle}_{skey}", disabled=not confirm or viewer):
            try:
                rec = svc.send_test(o.handle)
                st.success(f"Submitted to Photon: status {rec['status']} (delivery not yet confirmed)"
                           if not svc.simulated else "Simulated message stored (not sent to a phone).")
            except (PermissionError, PhotonUnavailable) as exc:
                st.error(str(exc))
        if b2.button("Scan risks and send opted-in alerts", key=f"scan_{o.handle}_{skey}", disabled=viewer):
            with st.spinner("Scanning with the forecasting and decision tools..."):
                sigs = scan(svc.agent_for(o).session)  # the scenario replies use (published/default)
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
                     disabled=not owners or viewer)
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
            st.markdown("**Notification history** (delivery is shown only when confirmed by Photon)")
            st.dataframe(pd.DataFrame([{k: r.get(k) for k in ("created_at", "kind", "to", "status",
                                                               "delivered_at", "acceptance",
                                                               "simulated", "failure")}
                                       | {"scenario": (r.get("evidence") or {}).get("scenario"),
                                          "model": (r.get("evidence") or {}).get("model")}
                                       for r in reversed(svc.store.outbound[-20:])]),
                         width="stretch", hide_index=True)
        if svc.store.events:
            with st.expander("Messaging events and errors"):
                for e in reversed(svc.store.events[-15:]):
                    st.caption(f"{e['at']} {e['kind']}: {e['detail']}")
        st.caption(f"Messages today: {svc.store.sends_today()} / {svc.daily_limit} (daily cap). "
                   "iMessage sends carry no Photon per-message fee; SMS would.")

    conversation()


# ================================================================================ EVIDENCE & DATA
def page_evidence() -> None:
    from decisiondesk.monitor import MonitorStore, TelegramNotifier, run_checks
    st.markdown('<div class="section-kicker">Show your work</div>', unsafe_allow_html=True)
    st.markdown("# Evidence & Data")
    section = seg("Section", ["Data sources", "Model validation", "Uncertainty calibration",
                              "Reconciliation", "Assumptions", "Banking (Nessie)", "Alerts"],
                  "seg_ev_section", "Data sources")
    if section == "Data sources":
        st.table(pd.DataFrame([
            {"source": "Business records (Lone Star Taqueria / Maple Street Hardware)", "origin": "SYNTHETIC",
             "used for": "every business KPI, product table, forecast and scenario", "in this dashboard": "yes"},
            {"source": "Product layer (5 products, recipes, ingredient ledger)", "origin": "SYNTHETIC (derived)",
             "used for": "product analytics, inventory, Scenario Lab", "in this dashboard": "yes (demo restaurant)"},
            {"source": "FRED / ALFRED (St. Louis Fed)", "origin": "REAL published economic data",
             "used for": "economic context and stress-test sizing; not a forecast input", "in this dashboard": "yes"},
            {"source": "Kwick365 Independent Restaurant Index", "origin": "REAL aggregate benchmark",
             "used for": "separate us_demo / us_validation workflows", "in this dashboard": "no"},
            {"source": "Capital One Nessie", "origin": "SIMULATED banking sandbox",
             "used for": "banking import demo; never business evidence", "in this dashboard": "yes (Evidence → Banking)"},
            {"source": "Google Gemini", "origin": "AI service", "used for": "explaining tool results",
             "in this dashboard": "AI Analyst / iMessage"},
            {"source": "Photon", "origin": "messaging service", "used for": "iMessage replies and alerts",
             "in this dashboard": "Messaging"}]).set_index("source"))
    elif section == "Model validation":
        rows = []
        for t in ("revenue", "variable_costs", "fixed_costs", "net_cash_flow"):
            b = s.board(t)
            sel = next(m for m in b.models if m.name == b.selected)
            rows.append({"target": t, "selected": b.selected, "validation WAPE": sel.validation["wape"],
                         "test WAPE": sel.holdout["wape"], "test MAE": sel.holdout["mae"],
                         "test 80% coverage": sel.holdout_interval_coverage, "reason": b.selection_reason})
        st.markdown("#### Business-level forecasts (rolling-origin validation, untouched final test)")
        st.dataframe(pd.DataFrame(rows).round(3), hide_index=True, width="stretch")
        ctx = product_ctx()
        if ctx is not None:
            st.markdown("#### Product demand forecasts (target: estimated demand)")
            st.dataframe(pd.DataFrame([{"product": views.NAMES[p], **b} for p, b in ctx.boards.items()])
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
    elif section == "Alerts":
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
                    store.add_rule(bkey, kind, series if kind == "fred_release" else None, thr or None, tg)
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


def calibration_section() -> None:
    from decisiondesk.calibration import (
        METHODS,
        cash_validation,
        chronological_windows,
        evaluate,
        lab_errors,
    )
    st.markdown("#### Do the 80% Monte Carlo ranges hold out of sample?")
    st.caption("Each past forecast window is judged only by a range built from EARLIER windows "
               "(chronological, expanding). Daily = share of single days inside their daily range; "
               "total = share of windows whose 7/14/28-day total fell inside the range for totals. "
               "Old = Milestone 5 weekly blocks; new = whole historical windows (current).")
    key = ("calib", s.dataset, MODELS)
    if st.session_state.get("calib_key") != key:
        targets = {t: chronological_windows(s.board(t))[0] for t in ("revenue", "net_cash_flow")}
        ctx = product_ctx()
        if ctx is not None:
            le = lab_errors(ctx)
            targets["Scenario Lab revenue (tracked)"] = le["lab_revenue_tracked"]
            targets["Scenario Lab profit (tracked − fixed)"] = le["lab_profit_tracked_minus_fixed"]
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
    st.warning("Small samples: about 23 evaluated windows per row, so a coverage estimate is uncertain "
               "by roughly ±15 points. New 28-day ranges are conservative (early backtest windows had "
               "larger errors). Synthetic data: this checks the method, not real-world calibration. "
               "Details: docs/MILESTONE_5_1_CALIBRATION.md.")


{"Overview": page_overview, "Revenue & Operations": page_revenue_ops, "Scenario Lab": page_scenario_lab,
 "AI Analyst": page_analyst, "Economy": page_economy, "Messaging": page_messaging,
 "Evidence & Data": page_evidence}[page]()
