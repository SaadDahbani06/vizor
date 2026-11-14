"""View models: shape engine outputs into chart-ready frames (pure functions, tested)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..decision.product_scenario import SimResult
from ..products import PRODUCT_IDS, PRODUCTS

NAMES = {p.product_id: p.name for p in PRODUCTS}


def band_frame(scen: SimResult, base: SimResult, what: str) -> pd.DataFrame:
    """Daily mean and 10th-90th percentile band of a money path (revenue or profit)."""
    a, b = getattr(scen, what), getattr(base, what)
    return pd.DataFrame({
        "date": scen.dates, "base": b.mean(0), "base_lo": np.percentile(b, 10, 0),
        "base_hi": np.percentile(b, 90, 0), "scen": a.mean(0), "scen_lo": np.percentile(a, 10, 0),
        "scen_hi": np.percentile(a, 90, 0)})


def demand_supply_frame(r: SimResult, product_id: str) -> pd.DataFrame:
    i = PRODUCT_IDS.index(product_id)
    d, s, lost = r.demand[:, :, i], r.sold[:, :, i], r.lost[:, :, i]
    short_share = (lost > 0.5).mean(0)
    return pd.DataFrame({"date": r.dates, "demand": d.mean(0), "demand_lo": np.percentile(d, 10, 0),
                         "demand_hi": np.percentile(d, 90, 0), "sold": s.mean(0),
                         "makeable": r.makeable.mean(0), "short_share": short_share,
                         "short": short_share >= 0.25})


def stockout_date(r: SimResult, product_id: str, share: float = 0.5) -> str | None:
    """First forecast day on which at least ``share`` of simulated paths leave demand unmet."""
    f = demand_supply_frame(r, product_id)
    hit = f[f["short_share"] >= share]
    return None if hit.empty else str(pd.Timestamp(hit["date"].iloc[0]).date())


def impact_frame(cmp: dict) -> pd.DataFrame:
    rows = []
    for p in cmp["products"]:
        rows.append({"product": p["product"], "metric": "Revenue change", "value": p["revenue_change"],
                     "units": p["units_sold_change"]})
        rows.append({"product": p["product"], "metric": "Gross profit change",
                     "value": p["gross_profit_change"], "units": p["units_sold_change"]})
    return pd.DataFrame(rows)


def risk_status(p: float) -> str:
    return "At risk" if p >= 0.30 else "Watch" if p >= 0.10 else "OK"


def inventory_frame(table: pd.DataFrame, next_delivery_days: int) -> pd.DataFrame:
    t = table.copy()
    t["demand_until_delivery"] = t["forecast_demand_7d"] / 7 * next_delivery_days
    t["risk_pct"] = t["stockout_risk_7d"] * 100
    t["status"] = t["stockout_risk_7d"].map(risk_status)
    return t[["product", "sellable_units", "demand_until_delivery", "days_of_supply", "risk_pct", "status"]]


def weekly_product_revenue(sales: pd.DataFrame, recon: pd.DataFrame, start, end,
                           products: list[str] | None = None) -> pd.DataFrame:
    s = sales[(sales["date"] >= start) & (sales["date"] <= end)]
    if products:
        s = s[s["product"].isin(products)]
    full = pd.date_range(start, end, freq="D").to_series().resample("W-SUN").count()
    weeks = set(full[full == 7].index)  # full weeks only (partial edge weeks would look like dips)
    w = s.set_index("date").groupby("product")["revenue"].resample("W-SUN").sum().reset_index()
    w = w[w["date"].isin(weeks)]
    if not products or "Other items" in products:
        r = recon[(recon["date"] >= start) & (recon["date"] <= end)]
        o = r.set_index("date")["other_items_revenue"].resample("W-SUN").sum().reset_index()
        o = o.rename(columns={"other_items_revenue": "revenue"}).assign(product="Other items")
        o = o[o["date"].isin(weeks)]
        w = pd.concat([w, o], ignore_index=True)
    order = {n: k for k, n in enumerate([*NAMES.values(), "Other items"])}
    w["order"] = w["product"].map(order)
    return w
