"""Dashboard metrics (Milestone 5): every number is a documented calculation on the dataset.

Definitions (shown as tooltips in the UI):
  revenue           sum of daily sales in the period.
  operating profit  revenue - variable costs - fixed costs (accrual). Owner distributions are
                    equity, not expenses; income taxes are not modeled. Loan payments are inside
                    fixed costs as the dataset records them (principal and interest are not split).
  operating margin  operating profit / revenue.
  cash on hand      the dataset's cash ledger on its last day (declared opening balance + recorded
                    inflows and outflows). Not a bank balance.
  cash runway       cash / average daily net cash outflow over the last 90 days; not applicable
                    when net cash flow is positive.
  payables          supplier invoices received but not yet paid under the dataset's terms
                    (restaurant net-7, retail net-30).
  due soon          payables maturing in the next 14 days + recurring bills projected from the
                    bill ledger's own schedule at their last amount.
Comparisons: month-to-date compares with the same number of days at the start of the previous
month; rolling windows compare with the preceding window of equal length.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .synthetic_v2 import COST_LABELS, FIXED_COMPONENTS, load_business, restaurant_cost_components

PERIODS = {"mtd": "Month to date", "28d": "Last 28 days", "90d": "Last 90 days",
           "365d": "Last 365 days"}
PAYMENT_TERMS_DAYS = {"restaurant": 7, "retail": 30}


@dataclass(frozen=True)
class Window:
    start: pd.Timestamp
    end: pd.Timestamp
    prev_start: pd.Timestamp
    prev_end: pd.Timestamp
    label: str
    prev_label: str


def window(history: pd.DataFrame, period: str) -> Window:
    end = pd.Timestamp(history["date"].max())
    first = pd.Timestamp(history["date"].min())
    if period == "mtd":
        start = end.replace(day=1)
        days = (end - start).days + 1
        prev_start = (start - pd.offsets.MonthBegin(1))
        prev_end = prev_start + pd.Timedelta(days=days - 1)
        label = f"{start:%b} 1-{end.day}, {end.year}"
        prev_label = f"{prev_start:%b} 1-{prev_end.day}, {prev_end.year}"
    elif period in ("28d", "90d", "365d"):
        n = int(period[:-1])
        start = end - pd.Timedelta(days=n - 1)
        prev_end = start - pd.Timedelta(days=1)
        prev_start = prev_end - pd.Timedelta(days=n - 1)
        label = f"{start:%b %d} - {end:%b %d, %Y}"
        prev_label = f"previous {n} days ({prev_start:%b %d} - {prev_end:%b %d})"
    else:
        raise ValueError(f"unknown period {period!r}; choose from {list(PERIODS)}")
    if prev_start < first:
        raise ValueError("not enough history for the comparison period")
    return Window(start, end, prev_start, prev_end, label, prev_label)


def _slice(df: pd.DataFrame, a, b) -> pd.DataFrame:
    return df[(df["date"] >= a) & (df["date"] <= b)]


def _chg(cur: float, prev: float) -> float | None:
    return None if not prev else (cur - prev) / abs(prev) * 100


def _sum(df, col):
    return float(df[col].sum())


def _profit(df) -> float:
    return float((df["revenue"] - df["variable_costs"] - df["fixed_costs"]).sum())


def project_bills(bills: pd.DataFrame | None, after: pd.Timestamp, days: int = 14) -> pd.DataFrame:
    """Recurring bills due in (after, after + days], projected from each category's own payment
    schedule in the ledger (weekly / biweekly / monthly / quarterly) at its last amount."""
    cols = ["date", "payee", "category", "amount", "basis"]
    if bills is None or bills.empty:
        return pd.DataFrame(columns=cols)
    out = []
    horizon = after + pd.Timedelta(days=days)
    for cat, g in bills[bills["recurring"]].groupby("category"):
        g = g.sort_values("date")
        if len(g) < 3:
            continue
        gaps = g["date"].diff().dt.days.dropna()
        gap = float(gaps.median())
        last = g.iloc[-1]
        nxt = pd.Timestamp(last["date"])
        step = None
        if abs(gap - 7) <= 1 or abs(gap - 14) <= 1:
            step = pd.Timedelta(days=round(gap))
        elif 27 <= gap <= 32:
            step = pd.DateOffset(months=1)
        elif 88 <= gap <= 93:
            step = pd.DateOffset(months=3)
        if step is None:
            continue
        while True:
            nxt = nxt + step
            if nxt > horizon:
                break
            if nxt > after:
                out.append({"date": nxt, "payee": last["payee"], "category": cat,
                            "amount": float(last["amount"]),
                            "basis": f"projected: every {round(gap)} days, last amount"})
    return pd.DataFrame(out, columns=cols).sort_values("date") if out else pd.DataFrame(columns=cols)


def kpis(session, period: str = "mtd") -> dict:
    h = session.history
    w = window(h, period)
    cur, prev = _slice(h, w.start, w.end), _slice(h, w.prev_start, w.prev_end)
    rev, prev_rev = _sum(cur, "revenue"), _sum(prev, "revenue")
    prof, prev_prof = _profit(cur), _profit(prev)
    exp_v, exp_f = _sum(cur, "variable_costs"), _sum(cur, "fixed_costs")
    prev_exp = _sum(prev, "variable_costs") + _sum(prev, "fixed_costs")
    out = {
        "period": period, "period_label": w.label, "comparison_label": w.prev_label,
        "data_label": session.data_label, "as_of": str(w.end.date()),
        "revenue": {"value": rev, "previous": prev_rev, "change_pct": _chg(rev, prev_rev),
                    "spark": h.tail(30)[["date", "revenue"]].assign(date=lambda x: x["date"].astype(str))
                    .to_dict(orient="records")},
        "net_profit": {"value": prof, "previous": prev_prof, "change_pct": _chg(prof, prev_prof),
                       "margin_pct": prof / rev * 100 if rev else None,
                       "previous_margin_pct": prev_prof / prev_rev * 100 if prev_rev else None,
                       "definition": "Operating profit: revenue - variable - fixed costs (accrual); "
                                     "before owner distributions and income taxes."},
        "expenses": {"value": exp_v + exp_f, "variable": exp_v, "fixed": exp_f,
                     "previous": prev_exp, "change_pct": _chg(exp_v + exp_f, prev_exp)},
    }
    # Cash
    if "cash_balance" in h.columns:
        cash = float(h["cash_balance"].iloc[-1])
        flow = (h["cash_in"] - h["cash_out"]).tail(90).mean()
        runway = None if flow >= 0 else cash / -flow
        out["cash"] = {"value": cash, "avg_daily_net_cash_flow_90d": float(flow),
                       "runway_days": runway,
                       "runway_note": ("Net cash flow was positive over the last 90 days (after "
                                       "owner distributions); runway does not apply."
                                       if runway is None else
                                       "Days until cash reaches zero at the last 90 days' average "
                                       "net outflow."),
                       "source": "SYNTHETIC cash ledger: declared opening balance + recorded flows "
                                 "(not a bank balance)"}
    else:
        out["cash"] = {"value": None, "source": "no cash ledger in this dataset"}
    out["inventory"] = inventory_kpi(session)
    out["liabilities"] = liabilities(session)
    return out


def inventory_kpi(session) -> dict:
    key = session.metadata.get("key")
    if key == "restaurant" and session.data_label == "SYNTHETIC":
        from .products import INGREDIENTS
        ctx = session.product_context()
        value = float(ctx.start_stock @ ctx.ing_cost)
        usage = ctx.expected_usage()[:7].mean(axis=0)
        days = np.where(usage > 0, ctx.start_stock / np.maximum(usage, 1e-9), np.inf)
        rows = [{"ingredient": k.name, "on_hand": float(ctx.start_stock[j]), "unit": k.unit,
                 "value_usd": float(ctx.start_stock[j] * ctx.ing_cost[j]),
                 "days_of_supply": float(days[j])} for j, k in enumerate(INGREDIENTS)]
        nxt = next_delivery_days(ctx.origin)
        low = [r for r in rows if r["days_of_supply"] < nxt]
        return {"value": value, "basis": "tracked ingredients at current cost (five product groups)",
                "items": rows, "next_delivery_in_days": nxt, "low_stock": low,
                "note": "Ingredient ledger for the five tracked products (SYNTHETIC)."}
    h = session.history
    if "inventory_on_hand" in h.columns and "unit_cost" in h.columns:
        units = float(h["inventory_on_hand"].iloc[-1])
        return {"value": units * float(h["unit_cost"].iloc[-1]), "units": units,
                "basis": "units on hand x latest unit cost", "low_stock": [], "items": []}
    return {"value": None, "basis": "inventory value needs unit costs; not in this dataset",
            "low_stock": [], "items": []}


def next_delivery_days(origin: pd.Timestamp) -> int:
    from .products import DELIVERY_WEEKDAYS
    for j in range(1, 8):
        if (origin + pd.Timedelta(days=j)).day_of_week in DELIVERY_WEEKDAYS:
            return j
    return 7


def liabilities(session) -> dict:
    h = session.history
    key = session.metadata.get("key", "")
    terms = PAYMENT_TERMS_DAYS.get(key)
    end = pd.Timestamp(h["date"].max())
    if terms is None or "purchase_dollars" not in h.columns:
        return {"payables": None, "due_soon": None,
                "note": "payment terms are not recorded for this dataset"}
    unpaid = h.tail(terms)  # purchases on days T-terms+1..T are not yet paid
    payables = float(unpaid["purchase_dollars"].sum())
    mature = h.tail(terms).head(min(14, terms))  # those due within 14 days
    sched = project_bills(getattr(session, "bills", None), end, 14)
    due = float(mature["purchase_dollars"].sum()) + float(sched["amount"].sum())
    return {"payables": payables, "payment_terms_days": terms,
            "due_14d": due, "due_14d_payables": float(mature["purchase_dollars"].sum()),
            "scheduled_bills": sched.assign(date=sched["date"].astype(str)).to_dict(orient="records"),
            "loan_balance": None,
            "note": ("Supplier payables from the purchase ledger under the dataset's payment terms; "
                     "recurring bills projected from the bill ledger. The equipment loan's "
                     "outstanding principal is not in the data, so total debt is unavailable.")}


def revenue_profit_series(history: pd.DataFrame, days: int) -> pd.DataFrame:
    h = history.tail(days).copy()
    h["profit"] = h["revenue"] - h["variable_costs"] - h["fixed_costs"]
    h = h[["date", "revenue", "profit"]]
    if days > 120:  # weekly totals keep a year readable
        h = h.set_index("date").resample("W-SUN").sum().reset_index()
        h["granularity"] = "weekly total"
    else:
        h["granularity"] = "daily"
    return h


def expense_breakdown(session, period: str = "mtd") -> pd.DataFrame:
    """Accrual expenses by category for the period; sums to variable + fixed costs."""
    h = session.history
    w = window(h, period)
    if session.metadata.get("key") == "restaurant" and session.data_label == "SYNTHETIC":
        from .products import restaurant_products
        comp = restaurant_cost_components(load_business("restaurant"))
        comp = _slice(comp, w.start, w.end)
        rec = _slice(restaurant_products().recon, w.start, w.end)
        rows = [("Ingredients: five tracked products", float(rec["tracked_cogs"].sum()), "variable"),
                ("Ingredients: other menu items", float(rec["other_food_cost"].sum()), "variable"),
                (COST_LABELS["packaging"], float(comp["packaging"].sum()), "variable"),
                (COST_LABELS["card_fees"], float(comp["card_fees"].sum()), "variable")]
        rows += [(COST_LABELS[c], float(comp[c].sum()), "fixed") for c in FIXED_COMPONENTS]
        df = pd.DataFrame(rows, columns=["category", "amount", "type"])
    else:
        cur = _slice(h, w.start, w.end)
        df = pd.DataFrame([("Variable costs", _sum(cur, "variable_costs"), "variable"),
                           ("Fixed costs", _sum(cur, "fixed_costs"), "fixed")],
                          columns=["category", "amount", "type"])
    df["share_pct"] = df["amount"] / df["amount"].sum() * 100
    return df.sort_values("amount", ascending=False).reset_index(drop=True)


def product_table(session, period: str = "28d") -> pd.DataFrame:
    """Five-product table. Observed: price, unit cost, units, revenue, margin, stock-outs.
    Forecast: next-7-day demand. Simulated: sellable units and 7-day stock-out risk."""
    from .decision.product_scenario import baseline, stockout_risk
    from .products import BOM, PRODUCT_IDS
    ctx = session.product_context()
    w = window(session.history, period)
    s = _slice(ctx.history.sales, w.start, w.end)
    g = s.groupby("product_id").agg(units_sold=("units_sold", "sum"), revenue=("revenue", "sum"),
                                    cogs=("cogs", "sum"), demand_est=("demand_est", "sum"),
                                    stockout_days=("stockout", "sum")).reindex(list(PRODUCT_IDS))
    base7 = baseline(ctx, 7)
    fc7 = np.array([ctx.bundle.targets[f"demand_{p}"].point[:7].clip(0).sum() for p in PRODUCT_IDS])
    usage = ctx.expected_usage()[:7].mean(axis=0)
    cover = np.where(usage > 0, ctx.start_stock / np.maximum(usage, 1e-9), np.inf)
    rows = []
    names = ctx.history.sales.drop_duplicates("product_id").set_index("product_id")["product"]
    for i, p in enumerate(PRODUCT_IDS):
        days = float(np.min(cover[BOM[i] > 0]))
        rows.append({
            "product_id": p, "product": names[p], "price": float(ctx.prices[i]),
            "unit_cost": float(ctx.unit_cost[i]),
            "units_sold": int(g.loc[p, "units_sold"]), "revenue": float(g.loc[p, "revenue"]),
            "gross_profit": float(g.loc[p, "revenue"] - g.loc[p, "cogs"]),
            "gross_margin_pct": float((g.loc[p, "revenue"] - g.loc[p, "cogs"]) / g.loc[p, "revenue"] * 100)
            if g.loc[p, "revenue"] else None,
            "stockout_days": int(g.loc[p, "stockout_days"]),
            "estimated_unmet_demand": float(max(0.0, g.loc[p, "demand_est"] - g.loc[p, "units_sold"])),
            "sellable_units": float(fc7[i] / 7 * min(days, 28)),
            "days_of_supply": days,
            "forecast_demand_7d": float(fc7[i]),
            "stockout_risk_7d": stockout_risk(base7, i)})
    return pd.DataFrame(rows)


def insights(session, period: str = "28d") -> list[dict]:
    t = product_table(session, period)
    exp = expense_breakdown(session, period)
    best = t.loc[t["revenue"].idxmax()]
    most_units = t.loc[t["units_sold"].idxmax()]
    prof = t.loc[t["gross_profit"].idxmax()]
    low = t.loc[t["gross_margin_pct"].idxmin()]
    risk = t.sort_values(["stockout_risk_7d", "days_of_supply"], ascending=[False, True]).iloc[0]
    big = exp.iloc[0]
    return [
        {"kind": "best_seller", "title": "Best-selling product", "product": best["product"],
         "text": f"{best['product']}: ${best['revenue']:,.0f} revenue from {best['units_sold']:,} "
                 f"units ({PERIODS.get(period, period).lower()})"
                 + (f"; {most_units['product'].lower()} sell the most units "
                    f"({most_units['units_sold']:,})." if most_units["product"] != best["product"] else "."),
         "basis": "observed (synthetic records)"},
        {"kind": "most_profitable", "title": "Most profitable product", "product": prof["product"],
         "text": f"{prof['product']}: ${prof['gross_profit']:,.0f} gross profit at a "
                 f"{prof['gross_margin_pct']:.1f}% gross margin.",
         "basis": "observed (synthetic records); gross profit = revenue - ingredient cost"},
        {"kind": "lowest_margin", "title": "Lowest-margin product", "product": low["product"],
         "text": f"{low['product']}: {low['gross_margin_pct']:.1f}% gross margin "
                 f"(price ${low['price']:.2f}, ingredient cost ${low['unit_cost']:.2f}).",
         "basis": "observed (synthetic records)"},
        {"kind": "stockout_risk", "title": "Highest stock-out risk (next 7 days)",
         "product": risk["product"],
         "text": f"{risk['product']}: {risk['stockout_risk_7d']:.0%} of simulated weeks leave at "
                 f"least 1% of demand unmet; stock covers about {risk['days_of_supply']:.1f} days "
                 "before the next delivery.",
         "basis": "simulated (forecast + ordering plan + assumptions)"},
        {"kind": "biggest_expense", "title": "Biggest expense category", "product": None,
         "text": f"{big['category']}: ${big['amount']:,.0f} ({big['share_pct']:.0f}% of expenses).",
         "basis": "observed (synthetic ledger, accrual)"},
    ]


def profit_change(session, period_days: int = 28) -> dict:
    """Why profit changed: current vs previous window, by expense category and product."""
    period = f"{period_days}d" if f"{period_days}d" in PERIODS else "28d"
    h = session.history
    w = window(h, period)
    cur, prev = _slice(h, w.start, w.end), _slice(h, w.prev_start, w.prev_end)
    drivers = [{"driver": "Revenue", "current": _sum(cur, "revenue"), "previous": _sum(prev, "revenue"),
                "profit_effect": _sum(cur, "revenue") - _sum(prev, "revenue")}]
    if session.metadata.get("key") == "restaurant" and session.data_label == "SYNTHETIC":
        e_cur = expense_breakdown(session, period).set_index("category")["amount"]
        # previous window: recompute on the shifted slice
        from .products import restaurant_products
        comp = _slice(restaurant_cost_components(load_business("restaurant")), w.prev_start, w.prev_end)
        rec = _slice(restaurant_products().recon, w.prev_start, w.prev_end)
        e_prev = {"Ingredients: five tracked products": rec["tracked_cogs"].sum(),
                  "Ingredients: other menu items": rec["other_food_cost"].sum(),
                  COST_LABELS["packaging"]: comp["packaging"].sum(),
                  COST_LABELS["card_fees"]: comp["card_fees"].sum(),
                  **{COST_LABELS[c]: comp[c].sum() for c in FIXED_COMPONENTS}}
        for cat, v in e_cur.items():
            drivers.append({"driver": cat, "current": float(v), "previous": float(e_prev[cat]),
                            "profit_effect": -(float(v) - float(e_prev[cat]))})
        ctx = session.product_context()
        s = ctx.history.sales
        pc = _slice(s, w.start, w.end).groupby("product")[["revenue", "units_sold"]].sum()
        pp = _slice(s, w.prev_start, w.prev_end).groupby("product")[["revenue", "units_sold"]].sum()
        products = [{"product": p, "revenue_change": float(pc.loc[p, "revenue"] - pp.loc[p, "revenue"]),
                     "units_change": int(pc.loc[p, "units_sold"] - pp.loc[p, "units_sold"])}
                    for p in pc.index]
    else:
        for col, name in (("variable_costs", "Variable costs"), ("fixed_costs", "Fixed costs")):
            drivers.append({"driver": name, "current": _sum(cur, col), "previous": _sum(prev, col),
                            "profit_effect": -(_sum(cur, col) - _sum(prev, col))})
        products = []
    drivers.sort(key=lambda d: -abs(d["profit_effect"]))
    days_cur = cur.assign(dow=cur["date"].dt.day_name()).groupby("dow").size().to_dict()
    days_prev = prev.assign(dow=prev["date"].dt.day_name()).groupby("dow").size().to_dict()
    return {"period": w.label, "comparison": w.prev_label,
            "profit": {"current": _profit(cur), "previous": _profit(prev),
                       "change": _profit(cur) - _profit(prev)},
            "drivers": drivers, "products": products,
            "closed_days": {"current": int(cur["closed"].sum()) if "closed" in cur else None,
                            "previous": int(prev["closed"].sum()) if "closed" in prev else None},
            "weekday_mix_equal": days_cur == days_prev,
            "note": "Accounting decomposition of observed (synthetic) records; it shows WHERE "
                    "profit changed, not a causal explanation."}
