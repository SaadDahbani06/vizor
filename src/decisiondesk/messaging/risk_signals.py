"""Business risk signals for proactive alerts. Every figure comes from an executed tool.

Each signal carries the tool outputs it was built from, so the messaging service can run the
numeric grounding check before sending, plus a dedup key so the same event is sent only once.
Thresholds are explicit constants (documented, adjustable), never learned from outcomes.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pandas as pd

from ..agent.tools import BusinessSession, run_tool

SHORTFALL_ALERT = 0.10        # >= 10% of simulated 28-day paths run out of cash
RUNWAY_ALERT_DAYS = 28        # cash covers fewer days than the evaluated horizon
COST_SHOCK_ALERT_PCT = 5.0    # hypothetical/observed food-cost change worth an alert
EXPENSE_JUMP_PCT = 20.0       # a bill category up >= 20% vs the prior 28 days
REVENUE_DROP_PCT = 8.0        # next-28-day revenue forecast >= 8% below the last 28 days
CPI_ALERT_PCT = 3.0           # observed food-at-home CPI 12-month change


@dataclass
class RiskSignal:
    kind: str
    severity: str
    title: str
    text: str
    dedup_key: str
    tool_outputs: list[dict] = field(default_factory=list)

    @property
    def run_ids(self) -> list[str]:
        return [o.get("run_id") for o in self.tool_outputs]


def _key(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


def _money(x) -> str:
    """$1,234 / -$1,234 (the sign before the dollar sign, so the grounding check reads it)."""
    if x is None:
        return "n/a"
    return f"-${abs(x):,.0f}" if round(x) < 0 else f"${x:,.0f}"


def scan(session: BusinessSession) -> list[RiskSignal]:
    name = session.metadata.get("business_name", session.dataset).replace(" (fictional)", "")
    asof = str(session.origin.date())
    out: list[RiskSignal] = []
    st = session.state

    if session.industry == "restaurant":
        dec = run_tool(session, "evaluate_restaurant_actions", {
            "food_cost_change_pct": None, "elasticity_mode": None, "elasticity_low": None,
            "elasticity_high": None, "max_price_increase": None, "starting_cash": None,
            "objective": "max_expected_profit"})
        base = next(a for a in dec["actions"] if a["action_id"] == "no_action")
        cash = dec["scenario"]["starting_cash"]
        profit = dec["baseline_expected_profit"]
        shortage = base.get("expected_cash_shortage")
        scen = (f"with food costs {st.food_cost_change_pct:+g}% (hypothetical)"
                if st.food_cost_change_pct else "under current forecasts")
        top = dec["recommended_action"]
        top_row = next((a for a in dec["actions"] if a["action_id"] == top), None)
        if shortage is not None and shortage >= SHORTFALL_ALERT:
            text = (f"⚠️ {name}: {scen} and {_money(cash)} starting cash, "
                    f"{shortage:.0%} of simulated 28-day scenarios run out of cash "
                    f"(expected 28-day profit {_money(profit)}). Reply OPTIONS to compare ways to "
                    "reduce the risk.")
            out.append(RiskSignal("cash_shortfall", "critical", "Projected cash shortfall", text,
                                  _key("cash_shortfall", name, asof, round(shortage, 2), cash,
                                       st.food_cost_change_pct), [dec]))
        if cash and profit is not None and profit < 0:
            daily = profit / dec["horizon_days"]
            runway = cash / -daily
            if runway < RUNWAY_ALERT_DAYS:
                daily_burn = round(-daily, 0)
                text = (f"⚠️ {name}: cash runway about {runway:.0f} days ({_money(cash)} cash, "
                        f"burning {_money(daily_burn)}/day {scen}). Reply OPTIONS for actions.")
                dec["runway_days"], dec["daily_burn"] = round(runway, 0), daily_burn
                out.append(RiskSignal("low_runway", "critical", "Low cash runway", text,
                                      _key("runway", name, asof, round(runway), cash), [dec]))
        if st.food_cost_change_pct >= COST_SHOCK_ALERT_PCT and top_row:
            text = (f"{name}: if food costs rise {st.food_cost_change_pct:g}% (hypothetical), "
                    f"expected 28-day profit with no action is {_money(profit)}. Top modeled "
                    f"option: {top_row['label']} ({_money(top_row['change_vs_no_action'])} vs no "
                    f"action, {dec['strength']}). Reply OPTIONS for details.")
            out.append(RiskSignal("cost_increase", "warning", "Rising food costs", text,
                                  _key("cost", name, asof, st.food_cost_change_pct, top), [dec]))

    econ = run_tool(session, "get_economic_indicators", {"refresh": False})
    for o in econ["observations"]:
        if o["series_id"] in ("CUSR0000SAF11", "CPIAUCSL") and o["change_12m"] is not None \
                and abs(o["change_12m"]) >= CPI_ALERT_PCT:
            text = (f"{name}: FRED {o['series_id']} rose {o['change_12m']}% over 12 months "
                    f"({o['change_12m_period']}), published {o['published_at']}. Reply OPTIONS to "
                    "see how that affects your plan.")
            out.append(RiskSignal("economic_release", "info", "Economic release", text,
                                  _key("cpi", o["series_id"], o["observed_at"], o["value"]), [econ]))

    ov = run_tool(session, "get_business_overview", {"period_days": 28})
    if session.bills is not None and len(session.bills):
        b = session.bills
        cur = b[b.date > session.origin - pd.Timedelta(days=28)]
        prev = b[(b.date <= session.origin - pd.Timedelta(days=28))
                 & (b.date > session.origin - pd.Timedelta(days=56))]
        c, p = cur.groupby("category").amount.sum(), prev.groupby("category").amount.sum()
        for cat in sorted(set(c.index) & set(p.index)):
            if cat == "owner_draw" or p[cat] <= 0:
                continue
            jump = 100 * (c[cat] / p[cat] - 1)
            if jump >= EXPENSE_JUMP_PCT:
                ev = {"tool": "bill_comparison", "run_id": ov["run_id"], "category": cat,
                      "last_28_days": round(float(c[cat]), 0), "prior_28_days": round(float(p[cat]), 0),
                      "change_pct": round(jump, 0)}
                text = (f"{name}: {cat} bills {_money(ev['last_28_days'])} in the last 28 days, up "
                        f"{ev['change_pct']:.0f}% from {_money(ev['prior_28_days'])}.")
                out.append(RiskSignal("expense_increase", "warning", "Expense increase", text,
                                      _key("bill", name, asof, cat, ev["last_28_days"]), [ov, ev]))

    fc = run_tool(session, "forecast_business", {"target": "revenue"})
    last = ov["current"]["revenue"]
    if last:
        drop = 100 * (1 - fc["point_total"] / last)
        if drop >= REVENUE_DROP_PCT:
            ev = {"tool": "revenue_comparison", "run_id": fc["run_id"], "drop_pct": round(drop, 1)}
            text = (f"{name}: revenue forecast for the next 28 days is {_money(fc['point_total'])} "
                    f"(80% range {_money(fc['interval_80_total'][0])}–{_money(fc['interval_80_total'][1])}), "
                    f"{ev['drop_pct']}% below the last 28 days ({_money(last)}).")
            out.append(RiskSignal("revenue_decline", "warning", "Revenue forecast decline", text,
                                  _key("rev", name, asof, round(drop)), [fc, ov, ev]))

    if session.industry == "retail" and st.purchase_budget_usd:
        inv = run_tool(session, "evaluate_inventory_policies", {
            "purchase_budget_usd": None, "storage_units": None, "max_stockout_probability": None,
            "objective": "max_expected_profit"})
        if inv.get("supported") and inv["recommended_action"] is None:
            text = (f"{name}: no inventory policy fits a {_money(st.purchase_budget_usd)} 28-day "
                    "purchase budget and your stockout limit. " + inv["rationale"].split(". ")[1]
                    + ". Reply OPTIONS to compare policies.")
            out.append(RiskSignal("inventory_constraint", "warning", "Inventory budget constraint",
                                  text, _key("inv", name, asof, st.purchase_budget_usd), [inv]))
    return out
