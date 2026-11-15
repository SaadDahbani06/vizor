"""Daily Owner Briefing and decision checklists (Milestones 5.3-5.4).

The briefing answers, in plain language: what happened yesterday, what changed, what needs
attention today, and one decision worth exploring. Alerts are few (at most ``MAX_ALERTS``), each
labelled by what it rests on: OBSERVED records, a model FORECAST, or a SIMULATION under stated
assumptions. Comparisons are with the average of the same weekday over the previous four weeks;
they describe what moved, not why (no causal claims). Every number in the text comes from the
returned figures, so it passes the grounding check. Nothing is executed: every action needs the
owner's approval.

``decision_checklist`` turns any Decision Lab comparison into the six things an owner needs before
deciding: what to do, why it might help, expected impact, how uncertain it is, what could go wrong,
and what to verify first.
"""
from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pandas as pd

from . import dashboard as dash

UNDERPERFORM_PCT = -15.0      # product units vs same-weekday average
MIX_GAP_PTS = -10.0           # ...and at least this much worse than the change in guests
MIN_UNITS = 10                # ignore tiny products for the underperformance flag
RISK_WATCH = 0.25             # 7-day simulated stock-out share worth an alert
COST_RATIO_PTS = 1.5          # ingredient cost share of sales up this many points (7 d vs 28 d)
MAX_ALERTS = 5
WEEKS = 4
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _m(x: float) -> str:
    return f"-${abs(x):,.0f}" if round(x) < 0 else f"${x:,.0f}"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.0f}%"


def _same_weekday_baseline(frame: pd.DataFrame, day: pd.Timestamp, cols: list[str],
                           closed: pd.Series | None = None) -> pd.Series:
    past = [day - pd.Timedelta(days=7 * k) for k in range(1, WEEKS + 1)]
    sub = frame[frame["date"].isin(past)]
    if closed is not None:
        sub = sub[~sub["date"].isin(closed[closed].index)]
    return sub[cols].mean()


def _alert(kind, severity, title, detail, basis, next_step, page=None, product=None) -> dict:
    return {"kind": kind, "severity": severity, "title": title, "detail": detail, "basis": basis,
            "next_step": next_step, "page": page, "product": product}


def daily_briefing(session, *, with_action: bool = True, market_as_of=None) -> dict:
    h = session.history.copy()
    h["profit"] = h["revenue"] - h["variable_costs"] - h["fixed_costs"]
    day = pd.Timestamp(h["date"].max())
    y = h[h["date"] == day].iloc[0]
    closed = h.set_index("date")["closed"] if "closed" in h else None
    cols = [c for c in ("revenue", "profit", "units_sold") if c in h]
    base = _same_weekday_baseline(h, day, cols, closed)

    def chg(col):
        b = float(base[col])
        return None if not b else (float(y[col]) - b) / abs(b) * 100

    volume = "Guests" if session.industry == "restaurant" else "Units sold"
    out = {"date": str(day.date()), "weekday": day.day_name(), "data_label": session.data_label,
           "business": session.metadata.get("business_name", session.dataset),
           "volume_label": volume, "comparison": f"average of the previous {WEEKS} {day.day_name()}s",
           "yesterday": {"revenue": round(float(y["revenue"]), 2),
                         "revenue_typical": round(float(base["revenue"]), 2),
                         "revenue_change_pct": None if chg("revenue") is None else round(chg("revenue"), 1),
                         "operating_profit": round(float(y["profit"]), 2),
                         "operating_profit_typical": round(float(base["profit"]), 2),
                         "guests": int(y["units_sold"]) if "units_sold" in h else None,
                         "guests_typical": round(float(base["units_sold"]), 1) if "units_sold" in h else None,
                         "guests_change_pct": None if "units_sold" not in h or chg("units_sold") is None
                         else round(chg("units_sold"), 1)},
           "products": [], "underperforming": [], "stock_watch": [], "bills_due_7d": [],
           "alerts": [], "attention": [], "action": None,
           "note": "Changes are descriptive (what moved, compared with a normal day); they are not "
                   "causal explanations. Every action needs your approval; nothing is executed."}
    alerts = []
    yy = out["yesterday"]

    # Traffic / sales decline (observed)
    gc, rc = yy["guests_change_pct"], yy["revenue_change_pct"]
    if rc is not None and rc <= UNDERPERFORM_PCT:
        why = (f"{volume} were {_pct(gc)} vs a typical {out['weekday']}, which explains most of it."
               if gc is not None and gc <= UNDERPERFORM_PCT else "Fewer sales per guest or a product mix shift.")
        alerts.append(_alert("sales_decline", "medium", f"Sales {_pct(rc)} vs a typical {out['weekday']}",
                             f"{_m(yy['revenue'])} vs {_m(yy['revenue_typical'])}. {why}", "observed",
                             "Check whether this repeats today before acting; one slow day is common.",
                             page="My Business"))

    # Products (only when product records exist)
    if session.has_products:
        ctx = session.product_context()
        s = ctx.history.sales
        yd = s[s["date"] == day].set_index("product")
        past = s[s["date"].isin([day - pd.Timedelta(days=7 * k) for k in range(1, WEEKS + 1)])]
        typ = past.groupby("product")["units_sold"].mean()
        g = gc or 0.0
        for name, row in yd.iterrows():
            t = float(typ.get(name, np.nan))
            c = (row["units_sold"] - t) / t * 100 if t else None
            rec = {"product": name, "units_sold": int(row["units_sold"]), "units_typical": round(t, 1),
                   "change_pct": None if c is None else round(c, 1), "revenue": round(float(row["revenue"]), 2),
                   "sold_out": bool(row["stockout"]),
                   "vs_guests_pts": None if c is None else round(c - g, 1)}
            out["products"].append(rec)
            # Flag only products that fell clearly MORE than overall traffic (a mix shift).
            if c is not None and c <= UNDERPERFORM_PCT and c - g <= MIX_GAP_PTS and t >= MIN_UNITS:
                out["underperforming"].append(rec)
        for p in sorted(out["underperforming"], key=lambda r: r["vs_guests_pts"])[:2]:
            alerts.append(_alert("product_mix", "medium", f"{p['product']} sold less than usual",
                                 f"{p['units_sold']} units vs a typical {p['units_typical']} "
                                 f"({_pct(p['change_pct'])}), {abs(p['vs_guests_pts']):.0f} points worse "
                                 f"than the change in {volume.lower()}.", "observed",
                                 "Look at this product's recent days before changing anything.",
                                 page="My Business", product=p["product"]))
        # Ingredient cost share (observed): last 7 days vs the 28 days before
        r = ctx.history.recon
        r7 = r[(r["date"] > day - pd.Timedelta(days=7)) & (r["date"] <= day)]
        r28 = r[(r["date"] > day - pd.Timedelta(days=35)) & (r["date"] <= day - pd.Timedelta(days=7))]
        if r7["revenue"].sum() and r28["revenue"].sum():
            s7 = r7["food_cost"].sum() / r7["revenue"].sum() * 100
            s28 = r28["food_cost"].sum() / r28["revenue"].sum() * 100
            out["ingredient_cost_share"] = {"last_7_days_pct": round(s7, 1), "previous_28_days_pct": round(s28, 1),
                                            "change_points": round(s7 - s28, 1)}
            if s7 - s28 >= COST_RATIO_PTS:
                alerts.append(_alert("cost_increase", "medium", "Ingredient costs are a bigger share of sales",
                                     f"{s7:.1f}% of sales in the last 7 days vs {s28:.1f}% before.",
                                     "observed", "Compare recent supplier invoices; test a supplier cost "
                                     "change in the Decision Lab.", page="Decision Lab"))
        # Stock (observed stock + forecast usage; simulated risk)
        table = dash.product_table(session, "28d")
        inv = dash.inventory_kpi(session)
        for it in inv.get("items", []):
            if it["days_of_supply"] < inv["next_delivery_in_days"] + 1:
                out["stock_watch"].append({"what": it["ingredient"], "days_of_supply": round(it["days_of_supply"], 1),
                                           "next_delivery_in_days": inv["next_delivery_in_days"], "kind": "ingredient"})
                alerts.append(_alert("low_stock", "high", f"{it['ingredient']} may run out before the next delivery",
                                     f"About {it['days_of_supply']:.1f} days of supply at forecast use; next "
                                     f"delivery in {inv['next_delivery_in_days']} days.", "forecast",
                                     "Consider a top-up order (test it in the Decision Lab first).",
                                     page="Decision Lab"))
        risky = table[table["stockout_risk_7d"] >= RISK_WATCH].sort_values("stockout_risk_7d", ascending=False)
        for _, rr in risky.iterrows():
            out["stock_watch"].append({"what": rr["product"], "stockout_risk_7d_pct": round(rr["stockout_risk_7d"] * 100),
                                       "days_of_supply": round(rr["days_of_supply"], 1), "kind": "product"})
        if len(risky):
            rr = risky.iloc[0]
            alerts.append(_alert("stockout_risk", "medium", f"{rr['product']} could run short this week",
                                 f"{round(rr['stockout_risk_7d'] * 100)}% of simulated weeks run short under "
                                 "the current ordering plan.", "simulated",
                                 "Test a small increase in ingredient orders in the Decision Lab.",
                                 page="Decision Lab", product=rr["product"]))

    # Bills and cash (observed ledger + scheduled bills)
    li = dash.liabilities(session)
    if li.get("payables") is not None:
        for b in li["scheduled_bills"]:
            if pd.Timestamp(b["date"]) <= day + pd.Timedelta(days=7):
                out["bills_due_7d"].append({"date": b["date"], "payee": b["payee"], "amount": round(b["amount"], 2)})
        out["bills_due_7d_total"] = round(sum(b["amount"] for b in out["bills_due_7d"]), 2)
        out["supplier_payables"] = round(li["payables"], 2)
    k = dash.kpis(session, "28d")
    out["cash"] = {"balance": k["cash"]["value"], "source": k["cash"]["source"]}
    cash = out["cash"]["balance"]
    due = out.get("bills_due_7d_total", 0.0) + out.get("supplier_payables", 0.0)
    if cash is not None and due and cash < due:
        alerts.append(_alert("cash", "high", "Cash may not cover this week's bills",
                             f"Cash {_m(cash)} vs {_m(due)} of bills and supplier invoices due within 7 days.",
                             "observed", "Review which payments can move, and check the cash forecast.",
                             page="My Business"))
    if out["bills_due_7d"]:
        alerts.append(_alert("bills", "low", f"{len(out['bills_due_7d'])} bills due in the next 7 days",
                             f"{_m(out['bills_due_7d_total'])} in total.", "observed",
                             "No action needed if cash covers them.", page="My Business"))

    alerts.sort(key=lambda a: SEVERITY_ORDER[a["severity"]])
    out["alerts"] = alerts[:MAX_ALERTS]
    out["alerts_suppressed"] = max(0, len(alerts) - MAX_ALERTS)
    out["attention"] = [f"{a['title']}: {a['detail']}" for a in out["alerts"]]

    if with_action and session.has_products:
        out["action"] = action_plan(session, market_as_of=market_as_of)
    out["text"] = briefing_text(out)
    return out


# ------------------------------------------------------------------------------ checklists
def _reason(sc, profit_change: float) -> str:
    up = profit_change > 0
    if sc.price_change_pct > 0:
        return ("Each order earns more, and the simulation expects that to outweigh the fewer orders."
                if up else "The fewer orders outweigh the higher price per order.")
    if sc.price_change_pct < 0:
        return ("The extra orders outweigh the lower price per order." if up else
                "The lower price per order outweighs the extra orders.")
    if sc.supply_change_pct > 0 or sc.extra_units:
        return "More stock means fewer sell-outs." if up else "Extra stock costs more than the sales it saves."
    if sc.supply_change_pct < 0:
        return "Less stock bought." if up else "Smaller orders lead to more sell-outs and lost sales."
    if sc.ingredient_cost_change_pct:
        return "Ingredient costs change the cost of every order."
    if sc.marketing_change_pct:
        return ("Lower marketing spend saves money" if sc.marketing_change_pct < 0 else
                "More marketing may bring more customers") + "; the demand response is assumed."
    if sc.demand_shock_pct:
        return "A hypothetical change in customer demand."
    return "No change from the current plan."


def decision_checklist(ctx, sc, cmp_: dict, sens: list[dict] | None = None, *, local_market: dict | None = None) -> dict:
    """The six things to know before deciding, from one Decision Lab comparison."""
    p = cmp_["profit"]
    lo, hi = p["change_90pct_range"]
    name = cmp_["products"][[x["product_id"] for x in cmp_["products"]].index(sc.product_id)]["product"]
    plan = {"product_id": sc.product_id, "product": name, "action": sc.describe(),
            "scenario": asdict(sc), "requires_approval": True,
            "why": _reason(sc, p["change"]),
            "impact": {"profit_change": round(p["change"]), "range_90": [round(lo), round(hi)],
                       "range_level_pct": 90, "probability_better_pct": round(cmp_["probability_profit_improves"] * 100),
                       "revenue_change": round(cmp_["revenue"]["change"]), "cash_change": round(cmp_["cash_flow"]["change"]),
                       "units_sold_change": round(cmp_["units_sold"]["change"]), "horizon_days": cmp_["horizon_days"]},
            "assumptions": [], "risks": [], "verify": []}
    plan["uncertainty"] = (
        f"In 9 of 10 simulated months the profit change was between {_m(lo)} and {_m(hi)}; it beat the "
        f"current plan in {plan['impact']['probability_better_pct']}% of them."
        + (" It could go either way." if lo < 0 < hi else ""))
    relevant = {"elasticity": bool(sc.price_change_pct), "substitution": bool(sc.price_change_pct),
                "marketing": bool(sc.marketing_change_pct)}
    for a in cmp_["assumptions"]:
        key = next((k for k in relevant if k in a["name"]), None)
        if a.get("low") is not None and (key is None or relevant[key]):
            plan["assumptions"].append(f"{a['name'].replace('_', ' ')}: {a['value']} (range {a['low']} to {a['high']}; assumed)")
    if cmp_["units_sold"]["change"] < -0.5:
        plan["risks"].append(f"Fewer {name.lower()} sold (about {round(-cmp_['units_sold']['change'])} units).")
    sr = cmp_["stockout_risk"]
    if sr["scenario"] > sr["baseline"] + 0.02:
        plan["risks"].append(f"More sell-outs: risk {sr['baseline']:.0%} to {sr['scenario']:.0%}.")
    if lo < 0:
        plan["risks"].append(f"The likely range includes a loss ({_m(lo)}).")
    if not cmp_["within_policy"]:
        plan["risks"].append("Outside your approved limits: " + "; ".join(cmp_["policy_violations"]) + ".")
    for f in sens or []:
        if np.sign(f["profit_change"]) != np.sign(p["change"]) and abs(p["change"]) > 0.5:
            plan["risks"].append(f"Reverses if {f['assumption'].replace('_', ' ')} is at its {f['level']} "
                                 f"bound ({_m(f['profit_change'])}).")
    if sc.price_change_pct:
        plan["verify"].append("Price sensitivity is an assumption, not measured: try the new price for "
                              "a few weeks and watch orders before making it permanent.")
        plan["verify"].append("Check that portion sizes are comparable before reading anything into "
                              "competitors' prices.")
        if local_market and local_market.get("sufficient"):
            plan["verify"].append(f"Local context: {local_market['peer_count']} verified comparable menus, "
                                  f"median ${local_market['peer_median']:.2f} (yours ${local_market['our_price']:.2f}); "
                                  "this shows position, not how your customers react.")
    if sc.supply_change_pct or sc.extra_units:
        plan["verify"].append("Confirm delivery days, minimum orders and shelf life with your supplier.")
    if sc.ingredient_cost_change_pct:
        plan["verify"].append("Check the new prices on your latest supplier invoices.")
    if sc.marketing_change_pct:
        plan["verify"].append("The demand response to marketing is assumed; watch weekly guests after the change.")
    plan["verify"].append("Look at recent customer demand for this product.")
    return plan


def action_plan(session, product_id: str | None = None, *, market_as_of=None) -> dict:
    """One decision worth considering: the Scenario Lab's best tested action for the best seller
    (by revenue, last 28 days), as a checklist."""
    from .decision.product_scenario import ProductScenario, baseline, compare, recommend, simulate
    ctx = session.product_context()
    table = dash.product_table(session, "28d")
    if product_id is None:
        product_id = str(table.loc[table["revenue"].idxmax(), "product_id"])
    rec = recommend(ctx, ProductScenario(product_id))
    best = rec["recommended"]
    name = table.set_index("product_id").loc[product_id, "product"]
    if best is None:
        return {"product_id": product_id, "product": name, "requires_approval": True,
                "action": f"Keep the current {name.lower()} plan", "why": rec["text"], "impact": None,
                "risks": [], "verify": [], "assumptions": [], "uncertainty": ""}
    sc = ProductScenario(**best["scenario"])
    cmp_ = compare(ctx, simulate(ctx, sc), baseline(ctx, sc.horizon_days, sc.product_id))
    lm = None
    if sc.price_change_pct:
        try:
            from . import market as mk
            lm = mk.compare(product_id, float(ctx.prices[list(table["product_id"]).index(product_id)]),
                            as_of=market_as_of)
        except (FileNotFoundError, ValueError):
            lm = None
    plan = decision_checklist(ctx, sc, cmp_, rec["sensitivity"], local_market=lm)
    if lm and lm.get("sufficient"):
        plan["local_market"] = {k: lm[k] for k in ("peer_count", "peer_median", "peer_min", "peer_max",
                                                   "our_price", "position")}
    plan["method"] = rec["method"]
    return plan


def checklist_text(a: dict) -> list[str]:
    lines = [f"Decision to consider ({a['product']}): {a['action']}."]
    if a.get("impact"):
        i = a["impact"]
        lines.append(f"  Why it might help: {a['why']}")
        lines.append(f"  Estimated profit impact: {_m(i['profit_change'])} over {i['horizon_days']} days "
                     f"(9 in 10 simulated months between {_m(i['range_90'][0])} and {_m(i['range_90'][1])}; "
                     f"better in {i['probability_better_pct']}% of them).")
    for r in a["risks"]:
        lines.append(f"  Risk: {r}")
    for v in a["verify"]:
        lines.append(f"  Verify: {v}")
    lines.append("  Needs your approval; nothing is changed automatically.")
    return lines


def briefing_text(b: dict) -> str:
    y = b["yesterday"]
    lines = [f"Daily briefing for {b['weekday']} {b['date']} ({b['data_label']} records).",
             (f"Yesterday: revenue {_m(y['revenue'])} ({_pct(y['revenue_change_pct'])} vs a typical "
             f"{b['weekday']} of {_m(y['revenue_typical'])}); operating profit {_m(y['operating_profit'])} "
             f"(typical {_m(y['operating_profit_typical'])}).")]
    if y.get("guests") is not None:
        lines[-1] += f" {b['volume_label']} {y['guests']} (typical {y['guests_typical']})."
    if b["alerts"]:
        lines.append("Needs attention today:")
        lines += [f"  - {a['title']}: {a['detail']} Next step: {a['next_step']}" for a in b["alerts"]]
    else:
        lines.append("Nothing urgent flagged today.")
    if b.get("action"):
        lines += checklist_text(b["action"])
    lines.append(b["note"])
    return "\n".join(lines)


def briefing_sms(b: dict) -> str:
    """Short version for a text message (no attachments, under ~900 characters)."""
    y = b["yesterday"]
    lines = [f"Good morning from Main Street Risk Desk ({b['data_label']} demo data).",
             f"Yesterday: revenue {_m(y['revenue'])}, {_pct(y['revenue_change_pct'])} vs a typical {b['weekday']}."]
    for a in b["alerts"][:3]:
        lines.append(f"- {a['title']}")
    a = b.get("action")
    if a and a.get("impact"):
        lines.append(f"To consider: {a['action']} ({_m(a['impact']['profit_change'])} profit over "
                     f"{a['impact']['horizon_days']} days, simulated; needs your approval).")
    lines.append("Reply with a question, OPTIONS, or STOP.")
    return "\n".join(lines)
