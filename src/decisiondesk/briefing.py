"""Daily Owner Briefing (Milestone 5.3): yesterday, what changed, what needs attention today, and
one decision worth considering, as a short action checklist.

Every number comes from the business's own records or from the existing analytics (dashboard
metrics, product table, Scenario Lab simulation, local market comparison). The text is built from
the returned figures only, so it passes the grounding check. Comparisons describe WHAT changed
(against the average of the same weekday over the previous four weeks); they are not causal
explanations. Nothing is executed: every action needs the owner's approval.
"""
from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pandas as pd

from . import dashboard as dash

UNDERPERFORM_PCT = -15.0      # product units vs same-weekday average
MIX_GAP_PTS = -10.0           # ...and at least this much worse than the change in guests
MIN_UNITS = 10                # ignore tiny products for the underperformance flag
RISK_WATCH = 0.15             # 7-day simulated stock-out share worth a look
WEEKS = 4


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
           "volume_label": volume,
           "comparison": f"average of the previous {WEEKS} {day.day_name()}s",
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
           "attention": [], "action": None,
           "note": "Changes are descriptive (what moved, compared with a normal day); they are not "
                   "causal explanations. Every action needs your approval; nothing is executed."}

    # Products (only when product records exist)
    if session.has_products:
        ctx = session.product_context()
        s = ctx.history.sales
        yd = s[s["date"] == day].set_index("product")
        past = s[s["date"].isin([day - pd.Timedelta(days=7 * k) for k in range(1, WEEKS + 1)])]
        typ = past.groupby("product")["units_sold"].mean()
        for name, row in yd.iterrows():
            t = float(typ.get(name, np.nan))
            c = (row["units_sold"] - t) / t * 100 if t else None
            rec = {"product": name, "units_sold": int(row["units_sold"]), "units_typical": round(t, 1),
                   "change_pct": None if c is None else round(c, 1), "revenue": round(float(row["revenue"]), 2),
                   "sold_out": bool(row["stockout"])}
            out["products"].append(rec)
            g = out["yesterday"]["guests_change_pct"] or 0.0
            rec["vs_guests_pts"] = None if c is None else round(c - g, 1)
            # Flag only products that fell clearly MORE than overall traffic (a mix shift), so a
            # quiet day does not flag every product.
            if c is not None and c <= UNDERPERFORM_PCT and c - g <= MIX_GAP_PTS and t >= MIN_UNITS:
                out["underperforming"].append(rec)
        table = dash.product_table(session, "28d")
        inv = dash.inventory_kpi(session)
        for it in inv.get("items", []):
            if it["days_of_supply"] < inv["next_delivery_in_days"] + 1:
                out["stock_watch"].append({"what": it["ingredient"], "days_of_supply": round(it["days_of_supply"], 1),
                                           "next_delivery_in_days": inv["next_delivery_in_days"],
                                           "kind": "ingredient"})
        for _, r in table.iterrows():
            if r["stockout_risk_7d"] >= RISK_WATCH:
                out["stock_watch"].append({"what": r["product"], "stockout_risk_7d_pct": round(r["stockout_risk_7d"] * 100),
                                           "days_of_supply": round(r["days_of_supply"], 1), "kind": "product"})

    # Bills due within 7 days (scheduled from the bill ledger) and supplier payables
    li = dash.liabilities(session)
    if li.get("payables") is not None:
        for b in li["scheduled_bills"]:
            if pd.Timestamp(b["date"]) <= day + pd.Timedelta(days=7):
                out["bills_due_7d"].append({"date": b["date"], "payee": b["payee"], "amount": round(b["amount"], 2)})
        out["bills_due_7d_total"] = round(sum(b["amount"] for b in out["bills_due_7d"]), 2)
        out["supplier_payables"] = round(li["payables"], 2)
    k = dash.kpis(session, "28d")
    out["cash"] = {"balance": k["cash"]["value"], "source": k["cash"]["source"]}

    # Attention list, most important first
    att = out["attention"]
    cash = out["cash"]["balance"]
    due = out.get("bills_due_7d_total", 0.0) + out.get("supplier_payables", 0.0)
    if cash is not None and due and cash < due:
        att.append(f"Cash {_m(cash)} is below bills and supplier invoices due within 7 days ({_m(due)}).")
    for w in [x for x in out["stock_watch"] if x["kind"] == "ingredient"]:
        att.append(f"{w['what']}: about {w['days_of_supply']} days of supply; next delivery in "
                   f"{w['next_delivery_in_days']} days.")
    for w in [x for x in out["stock_watch"] if x["kind"] == "product"]:
        att.append(f"{w['what']}: {w['stockout_risk_7d_pct']}% of simulated next weeks run short "
                   f"(stock covers about {w['days_of_supply']} days).")
    gc = out["yesterday"]["guests_change_pct"]
    if gc is not None and gc <= UNDERPERFORM_PCT:
        att.append(f"{volume} were {_pct(gc)} vs a typical {out['weekday']}: that explains most "
                   "of yesterday's lower sales.")
    for p in out["underperforming"]:
        att.append(f"{p['product']} sold {p['units_sold']} units, {_pct(p['change_pct'])} vs a typical "
                   f"{out['weekday']} ({p['units_typical']}), {abs(p['vs_guests_pts']):.0f} points worse "
                   f"than the change in {volume.lower()}.")
    if out["bills_due_7d"]:
        att.append(f"{len(out['bills_due_7d'])} scheduled bills due in the next 7 days, "
                   f"{_m(out['bills_due_7d_total'])} in total.")

    if with_action and session.has_products:
        out["action"] = action_plan(session, market_as_of=market_as_of)
    out["text"] = briefing_text(out)
    return out


def action_plan(session, product_id: str | None = None, *, market_as_of=None) -> dict:
    """One decision worth considering, as a checklist, from the Scenario Lab's own ranking.
    Default product: the best seller by revenue over the last 28 days."""
    from .decision.product_scenario import ProductScenario, recommend
    ctx = session.product_context()
    table = dash.product_table(session, "28d")
    if product_id is None:
        product_id = str(table.loc[table["revenue"].idxmax(), "product_id"])
    rec = recommend(ctx, ProductScenario(product_id))
    name = table.set_index("product_id").loc[product_id, "product"]
    best = rec["recommended"]
    plan = {"product_id": product_id, "product": name, "requires_approval": True,
            "method": rec["method"], "verify": [], "risks": []}
    if best is None:
        plan.update({"action": f"Keep the current {name.lower()} plan",
                     "why": rec["text"], "impact": None})
        return plan
    lo, hi = best["profit_change_90pct_range"]
    sc = ProductScenario(**best["scenario"])
    plan.update({
        "action": best["action"], "scenario": asdict(sc),
        "why": f"Projected 28-day operating profit change {_m(best['profit_change'])} versus the "
               f"current plan, better in {best['probability_profit_improves']:.0%} of simulated futures.",
        "impact": {"profit_change": round(best["profit_change"]), "range_90": [round(lo), round(hi)],
                   "range_level_pct": 90,
                   "probability_better_pct": round(best["probability_profit_improves"] * 100),
                   "units_sold_change": round(best["units_sold_change"])}})
    if best["units_sold_change"] < -0.5:
        plan["risks"].append(f"Fewer {name.lower()} sold (about {round(-best['units_sold_change'])} units over 28 days).")
    if lo < 0:
        plan["risks"].append(f"The 90% simulation range includes a loss ({_m(lo)}).")
    for f in rec["assumption_flips"]:
        plan["risks"].append(f"Reverses if {f['assumption']} is at its {f['level']} bound "
                             f"({_m(f['profit_change'])}).")
    if sc.price_change_pct:
        plan["verify"].append("Price sensitivity is an assumption, not measured: consider a short "
                              "in-store price test before a permanent change.")
        try:
            from . import market as mk
            c = mk.compare(product_id, float(ctx.prices[list(table["product_id"]).index(product_id)]),
                           as_of=market_as_of)
            if c["sufficient"]:
                plan["verify"].append(
                    f"Local context: {c['peer_count']} verified comparable menus, median "
                    f"${c['peer_median']:.2f} for {c['our_serving']} (yours "
                    f"${c['our_price']:.2f}); this shows position, not how customers react.")
                plan["local_market"] = {k: c[k] for k in ("peer_count", "peer_median", "peer_min",
                                                          "peer_max", "our_price", "position")}
        except (FileNotFoundError, ValueError):
            pass
    plan["verify"].append("Check that ingredient orders match the expected change in units.")
    return plan


def briefing_text(b: dict) -> str:
    y = b["yesterday"]
    lines = [f"Daily briefing for {b['weekday']} {b['date']} ({b['data_label']} records).",
             (f"Yesterday: revenue {_m(y['revenue'])} ({_pct(y['revenue_change_pct'])} vs a typical "
             f"{b['weekday']} of {_m(y['revenue_typical'])}); operating profit {_m(y['operating_profit'])} "
             f"(typical {_m(y['operating_profit_typical'])}).")]
    if y.get("guests") is not None:
        lines[-1] += f" {b['volume_label']} {y['guests']} (typical {y['guests_typical']})."
    if b["attention"]:
        lines.append("Needs attention today:")
        lines += [f"  - {a}" for a in b["attention"]]
    else:
        lines.append("Nothing urgent flagged today.")
    a = b.get("action")
    if a:
        lines.append(f"Decision to consider ({a['product']}): {a['action']}.")
        if a.get("impact"):
            i = a["impact"]
            lines.append(f"  Estimated profit impact: {_m(i['profit_change'])} over 28 days (90% range "
                         f"{_m(i['range_90'][0])} to {_m(i['range_90'][1])}; better in "
                         f"{i['probability_better_pct']}% of simulated futures).")
        for r in a["risks"]:
            lines.append(f"  Risk: {r}")
        for v in a["verify"]:
            lines.append(f"  Verify: {v}")
        lines.append("  Needs your approval; nothing is changed automatically.")
    lines.append(b["note"])
    return "\n".join(lines)
