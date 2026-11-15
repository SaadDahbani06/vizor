"""Demo replay: SYNTHETIC "next four weeks" for the demo restaurant, so a decision follow-up can be
shown end to end before any real records exist after an approval.

Everything here is computer-generated and labelled "Demo replay · synthetic" wherever it is shown.
It shows that the follow-up and learning code works, not that Vizor works on a real business.

Independence from the forecast. The replay continues the demo data GENERATOR's own process
(``synthetic_v2.restaurant_cover_level`` for guests, ``products._mix`` for the menu mix, the same
Monday/Thursday order-up-to rule, rationing and sell-out estimate). It never calls Vizor's
forecasting models or the Decision Lab simulation, so a follow-up is not judged against data the
prediction produced itself.

The owner's change is applied with a HIDDEN true price sensitivity per product (``TRUE_ELASTICITY``)
that differs from the Decision Lab's assumed most-likely value, and a hidden switching share. The
follow-up and learning code never sees these values; the dashboard reveals them only in the demo
check, after the result is shown.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..products import (
    BOM,
    CARD_FEE_RATE,
    DELIVERY_WEEKDAYS,
    DRINKS_PER_GUEST,
    MAINS,
    MAINS_PER_GUEST,
    PACKAGING_PER_GUEST,
    PRODUCT_IDS,
    ProductHistory,
    _mix,
    days_until_next_delivery,
    estimate_demand,
    plan_order,
    ration,
)
from ..synthetic_v2 import (
    RESTAURANT_COVER_DISPERSION,
    BusinessDataset,
    _ar1,
    _negbin,
    restaurant_cover_level,
)
from .followup import FollowupActuals

REPLAY_SEED = 9090
REPLAY_LABEL = "Demo replay · synthetic"
# Hidden from the follow-up and learning code. Inside each product's assumed range, away from its
# assumed most likely value (tacos: assumed -1.1, range -1.8 to -0.6).
TRUE_ELASTICITY = {"tacos": -1.5, "burritos": -0.7, "quesadillas": -1.5, "nachos": -1.0, "drinks": -1.2}
TRUE_SUBSTITUTION = 0.25
MAIN_IDX = [PRODUCT_IDS.index(p) for p in MAINS]


def _apply_price_change(demand: np.ndarray, i: int, p: float, e: float, sub: float,
                        shares: np.ndarray, rng) -> np.ndarray:
    """Customers' reaction to product i's price change (+p): own demand x (1+p)^e; a share
    ``sub`` of the demand it loses (or gains) moves from (or to) the other main dishes."""
    d = demand.copy()
    if not p:
        return d
    f = (1 + p) ** e
    if f < 1:
        keep = rng.binomial(d[:, i], f)
        lost = d[:, i] - keep
        d[:, i] = keep
        if PRODUCT_IDS[i] in MAINS:
            moved = rng.binomial(lost, sub)
            others = [j for j in MAIN_IDX if j != i]
            for t in np.nonzero(moved)[0]:
                w = shares[t, [MAIN_IDX.index(j) for j in others]]
                d[t, others] += rng.multinomial(moved[t], w / w.sum())
    else:
        gained = rng.poisson(d[:, i] * (f - 1))
        d[:, i] += gained
        if PRODUCT_IDS[i] in MAINS:
            others = [j for j in MAIN_IDX if j != i]
            taken = rng.binomial(gained, sub)
            for t in np.nonzero(taken)[0]:
                w = d[t, others].astype(float)
                if w.sum() <= 0:
                    continue
                cut = rng.multinomial(taken[t], w / w.sum())
                d[t, others] = np.maximum(d[t, others] - cut, 0)
    return d


def demo_replay(history: ProductHistory, ds: BusinessDataset, receipt: dict, *,
                seed: int = REPLAY_SEED, true_elasticity: float | None = None,
                true_substitution: float = TRUE_SUBSTITUTION,
                change_made_on: str | None = None, schedule: list[bool] | None = None) -> FollowupActuals:
    """Synthetic records for the receipt's forecast window, with the owner's price change applied
    from ``change_made_on`` (default: the receipt's, else the first day of the window)."""
    if ds.metadata.get("key") != "restaurant":
        raise ValueError("the demo replay exists for the demo restaurant only")
    sc = receipt["scenario"]
    pid = sc["product_id"]
    i = PRODUCT_IDS.index(pid)
    p = float(sc.get("price_change_pct") or 0.0) / 100
    e_true = TRUE_ELASTICITY[pid] if true_elasticity is None else float(true_elasticity)
    rng = np.random.default_rng(seed + i)
    H = int(receipt["horizon_days"])
    dates = pd.date_range(receipt["forecast_start"], periods=H, freq="D")
    changed = pd.Timestamp(change_made_on or receipt.get("change_made_on") or dates[0])
    daily = ds.daily
    if dates[0] <= daily["date"].iloc[-1]:
        raise ValueError("the replay starts after the last recorded day")

    # Guests: the generator's level for these dates, a fresh day-to-day shock, overdispersed counts.
    price_index = np.full(H, float(daily["menu_price_index"].iloc[-1]))
    level, holiday = restaurant_cover_level(dates, daily["date"].iloc[0], price_index)
    closed = level <= 0
    covers = np.where(closed, 0, _negbin(rng, level * np.exp(_ar1(rng, H, 0.55, 0.07)),
                                          RESTAURANT_COVER_DISPERSION))
    dow = dates.day_of_week.to_numpy()

    # Orders: main dishes per guest and the generator's menu mix, then drinks.
    mains_rate = MAINS_PER_GUEST * np.where(np.isin(dow, (4, 5)), 1.03, 1.0) * rng.lognormal(0, 0.015, H)
    total_mains = np.round(covers * mains_rate).astype(int)
    all_dates = pd.DatetimeIndex(list(daily["date"]) + list(dates))
    all_hol = np.concatenate([daily["holiday"].to_numpy(object), holiday])
    shares = _mix(all_dates, all_hol, rng)[-H:]
    P = len(PRODUCT_IDS)
    demand = np.zeros((H, P), dtype=int)
    for t in range(H):
        if total_mains[t] > 0:
            demand[t, :4] = rng.multinomial(total_mains[t], shares[t])
    doy = dates.day_of_year.to_numpy()
    drinks = covers * DRINKS_PER_GUEST * (1 + 0.12 * np.sin(2 * np.pi * (doy - 105) / 365.25))
    demand[:, 4] = np.round(drinks * rng.lognormal(0, 0.02, H)).astype(int)
    demand[closed] = 0

    # The owner's change, from the day it was made (or on the days a switchback schedule says).
    after = dates >= changed
    if schedule is not None:
        after = after & np.asarray(schedule, dtype=bool)
    changed_demand = _apply_price_change(demand, i, p, e_true, true_substitution, shares, rng)
    demand = np.where(after[:, None], changed_demand, demand)

    # Ingredients: Monday/Thursday deliveries planned from recent same-weekday estimates; rationing.
    est_hist = history.wide("demand_est")[list(PRODUCT_IDS)].to_numpy(float)
    n0 = len(est_hist)
    est = np.vstack([est_hist, np.zeros((H, P))])
    stock = history.closing_stock().to_numpy(float)
    sold = np.zeros((H, P), dtype=int)
    for t in range(H):
        if dow[t] in DELIVERY_WEEKDAYS:
            span = days_until_next_delivery(int(dow[t]))
            use = []
            for j in range(span):
                same = list(range(n0 + t + j - 7, -1, -7))[:4]
                use.append(est[same].mean(axis=0) @ BOM)
            stock = stock + np.round(plan_order(stock, np.array(use)), 2)
        fill, _ = ration(demand[t].astype(float), stock)
        sold[t] = np.floor(demand[t] * fill + 1e-9).astype(int)
        stock = np.maximum(stock - sold[t] @ BOM, 0.0)
        est[n0 + t], _ = estimate_demand(sold[t], demand[t], est[:n0 + t])

    # Money, at the menu prices in force each day.
    s = history.sales
    last = s[s["date"] == s["date"].max()].set_index("product_id").reindex(list(PRODUCT_IDS))
    prices = np.tile(last["price"].to_numpy(float), (H, 1))
    prices[after, i] = float(receipt["new_price"])
    unit_cost = last["unit_cost"].to_numpy(float)
    r = history.recon[~history.recon["closed"]].tail(56)
    other_rev = float(r["other_items_revenue"].sum() / r["mains_sold"].sum())
    other_food = float(r["other_food_cost"].sum() / r["mains_sold"].sum())
    mains_sold = sold[:, :4].sum(axis=1)
    revenue = (sold * prices).sum(axis=1) + other_rev * mains_sold
    variable = sold @ unit_cost + other_food * mains_sold + PACKAGING_PER_GUEST * covers + CARD_FEE_RATE * revenue

    products = pd.concat([pd.DataFrame({
        "date": dates, "product_id": p_, "price": prices[:, j], "units_sold": sold[:, j],
        "demand_est": np.round(est[n0:, j], 1)}) for j, p_ in enumerate(PRODUCT_IDS)], ignore_index=True)
    daily_out = pd.DataFrame({"date": dates, "revenue": np.round(revenue, 2),
                              "variable_costs": np.round(variable, 2),
                              "contribution": np.round(revenue - variable, 2), "closed": closed})
    meta = {"generator": "decisiondesk.decision.replay.demo_replay", "seed": seed + i,
            "data_origin": "SYNTHETIC", "change_applied_from": str(changed.date()),
            "hidden_true_price_sensitivity": e_true, "hidden_true_switching_share": true_substitution,
            "note": ("Computer-generated continuation of the demo restaurant. Shows that the follow-up "
                     "works; says nothing about a real business.")}
    return FollowupActuals(products, daily_out, REPLAY_LABEL, "SYNTHETIC_REPLAY", meta)
