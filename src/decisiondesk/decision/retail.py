"""Retail inventory decisions: weekly-review replenishment POLICIES over the forecast horizon.

Simulation per path (daily demand D_t from the selected units_sold model + out-of-sample error
paths; lost sales are NOT backordered):
    every ``review_days`` (t = 0, 7, ...) the policy decides an order q_t, which arrives at the
    start of day t + L (lead time) if that is inside the horizon; orders arriving later are still
    paid for and show up as cash outlay, not as sellable stock
    sales_t = min(I_t, D_t); lost_t = D_t - sales_t; holding on end-of-day inventory
Policies (the candidate actions):
    no_order (baseline)       never order
    order_up_to (reorder)     q = max(0, S - inventory position), S = service-level quantile of
                              demand over L + review days (sized on an independent draw set)
    replace_sales (maintain)  q = units sold since the last review (t = 0, when nothing has been
                              sold yet: one review period of expected demand), so on-hand stock
                              cycles around today's level instead of drifting
    replace_sales x f (reduce) q = f * units sold since the last review
Metrics: profit = sales * (price - cost) - holding - order costs (unsold stock stays at cost);
operating_cost = holding + order costs; stockout_any / stockout_after_arrival (any lost sale on
any day / on days >= L, the part ordering now can influence); lost_units; fill_rate;
cash_outlay = sum(q) * cost + order costs; ending_inventory; peak_inventory.

Simplifications: no supplier capacity limits, no price response, deterministic lead time.
The synthetic inventory column has no receipts modelled, so on-hand levels are illustrative.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .baseline import ForecastBundle, SimulationDraws, make_draws
from .types import Action, Assumption, Constraint, Unsupported

REQUIRED_FIELDS = ("units_sold", "inventory_on_hand")
UNITS = {"profit": "USD", "operating_cost": "USD", "revenue": "USD", "stockout_any": "probability",
         "stockout_after_arrival": "probability", "lost_units": "units", "fill_rate": "ratio",
         "cash_outlay": "USD", "ending_inventory": "units", "peak_inventory": "units"}


@dataclass(frozen=True)
class RetailInputs:
    current_inventory: float
    inventory_date: pd.Timestamp
    unit_price: Assumption
    unit_cost: Assumption


HOLDING_RATE = Assumption(
    "holding_cost_annual_rate", 0.25, "fraction of unit cost per year",
    "default_needs_confirmation", 0.15, 0.35, "uniform",
    "Capital, storage, shrinkage. Typical planning range, confirm for your business.")
ORDER_COST = Assumption(
    "order_fixed_cost", 75.0, "USD per order", "default_needs_confirmation",
    note="Delivery / handling charge per purchase order.")


@dataclass(frozen=True)
class RetailDecisionConfig:
    lead_time_days: int = 7
    review_days: int = 7
    service_level: float = 0.95
    reduce_fraction: float = 0.5  # share of sales replaced under "reduce purchasing"
    holding_rate: Assumption = HOLDING_RATE
    order_cost: Assumption = ORDER_COST
    constraints: tuple[Constraint, ...] = field(default_factory=lambda: (
        Constraint("28-day purchase budget", "metric:cash_outlay:mean", "max", 40000.0,
                   "USD"),
        Constraint("storage capacity", "metric:peak_inventory:p95", "max", 1200.0, "units"),
        Constraint("stockout tolerance after arrival", "metric:stockout_after_arrival:mean",
                   "max", 0.25, "probability"),
    ))


def retail_inputs(history: pd.DataFrame, lookback_days: int = 84) -> RetailInputs | Unsupported:
    missing = [c for c in REQUIRED_FIELDS if c not in history.columns]
    if missing:
        return Unsupported("retail_inventory", missing,
                           "Inventory decisions need daily units_sold and inventory_on_hand; "
                           "these fields are absent, so no inventory recommendation is made.")
    recent = history.tail(lookback_days)
    units = float(recent["units_sold"].sum())
    if units <= 0 or float(recent["revenue"].sum()) <= 0:
        return Unsupported("retail_inventory", ["unit_price", "unit_cost"],
                           "Cannot derive unit price/cost: no units sold in the lookback window. "
                           "Provide unit price and unit cost explicitly.")
    note = f"sum over the last {len(recent)} days of uploaded history / units sold"
    return RetailInputs(
        current_inventory=float(history["inventory_on_hand"].iloc[-1]),
        inventory_date=pd.Timestamp(history["date"].iloc[-1]),
        unit_price=Assumption("unit_price", float(recent["revenue"].sum()) / units, "USD/unit",
                              "derived_from_data", note="revenue " + note),
        unit_cost=Assumption("unit_cost", float(recent["variable_costs"].sum()) / units,
                             "USD/unit", "derived_from_data", note="variable costs " + note))


def policy_levels(bundle: ForecastBundle, inputs: RetailInputs, cfg: RetailDecisionConfig,
                  sizing_seed: int = 7) -> dict[str, float]:
    """Order-up-to level and first-period replacement, sized on an independent draw set."""
    sizing = bundle.simulate(make_draws(bundle, n=4000, seed=sizing_seed))["units_sold"]
    cover = min(cfg.lead_time_days + cfg.review_days, bundle.horizon)
    point = bundle.targets["units_sold"].point
    return {
        "order_up_to_level": float(math.ceil(np.quantile(sizing[:, :cover].sum(axis=1),
                                                         cfg.service_level))),
        "first_review_demand": float(math.ceil(point[:cfg.review_days].sum())),
    }


def retail_actions(bundle: ForecastBundle, inputs: RetailInputs,
                   cfg: RetailDecisionConfig) -> tuple[list[Action], dict[str, float]]:
    lv = policy_levels(bundle, inputs, cfg)
    common = (inputs.unit_price, inputs.unit_cost, cfg.holding_rate, cfg.order_cost)
    base = {"lead_time_days": cfg.lead_time_days, "review_days": cfg.review_days,
            "first_review_demand": lv["first_review_demand"]}
    actions = [
        Action("no_order", "Place no orders (baseline)", "inventory",
               {**base, "policy": "none"}, common, is_baseline=True),
        Action("reorder_service_level",
               f"Reorder weekly up to {lv['order_up_to_level']:.0f} units "
               f"({cfg.service_level:.0%} service target)", "inventory",
               {**base, "policy": "order_up_to", "level": lv["order_up_to_level"]}, common),
        Action("maintain_stock", "Maintain current stock: order one week of expected demand "
               "now, then replace units sold each week",
               "inventory", {**base, "policy": "replace_sales", "fraction": 1.0}, common),
        Action("reduce_purchasing",
               f"Reduce purchasing: replace {cfg.reduce_fraction:.0%} of units sold each week",
               "inventory", {**base, "policy": "replace_sales", "fraction": cfg.reduce_fraction},
               common),
    ]
    return actions, {**lv, "current_inventory": inputs.current_inventory}


def simulate_retail(bundle: ForecastBundle, action: Action, draws: SimulationDraws,
                    inputs: RetailInputs) -> dict[str, np.ndarray]:
    D = bundle.simulate(draws)["units_sold"]
    n, H = D.shape
    prm = action.parameters
    L, R, policy = int(prm["lead_time_days"]), int(prm["review_days"]), prm["policy"]

    def draw(name: str) -> np.ndarray:
        return action.assumption(name).sample(draws.uniform(name))

    price, cost = draw("unit_price"), draw("unit_cost")
    h_daily = draw("holding_cost_annual_rate") * cost / 365.0
    fixed_order = draw("order_fixed_cost")
    inv = np.full(n, inputs.current_inventory, dtype=float)
    arrivals = np.zeros((n, H + L + 1))
    ordered_units, order_costs = np.zeros(n), np.zeros(n)
    sold_since_review = np.zeros(n)
    sales_total, lost_total, lost_after, holding = (np.zeros(n) for _ in range(4))
    peak = inv.copy()
    for t in range(H):
        inv += arrivals[:, t]
        peak = np.maximum(peak, inv)
        if t % R == 0 and policy != "none":
            if policy == "order_up_to":
                position = inv + arrivals[:, t + 1:].sum(axis=1)
                q = np.maximum(0.0, float(prm["level"]) - position)
            elif policy == "replace_sales":
                replace = prm["first_review_demand"] if t == 0 else sold_since_review
                q = float(prm["fraction"]) * np.asarray(replace, dtype=float) * np.ones(n)
            else:
                raise ValueError(f"unknown inventory policy {policy!r}")
            q = np.ceil(q)
            arrivals[:, t + L] += q
            ordered_units += q
            order_costs += fixed_order * (q > 0)
            sold_since_review = np.zeros(n)
        sales = np.minimum(inv, D[:, t])
        lost = D[:, t] - sales
        inv -= sales
        sold_since_review += sales
        sales_total += sales
        lost_total += lost
        if t >= L:
            lost_after += lost
        holding += inv * h_daily
    demand_total = D.sum(axis=1)
    return {
        "profit": sales_total * (price - cost) - holding - order_costs,
        "operating_cost": holding + order_costs,
        "revenue": sales_total * price,
        "stockout_any": (lost_total > 1e-9).astype(float),
        "stockout_after_arrival": (lost_after > 1e-9).astype(float),
        "lost_units": lost_total,
        "fill_rate": np.divide(sales_total, demand_total, out=np.ones(n), where=demand_total > 0),
        "cash_outlay": ordered_units * cost + order_costs,
        "ending_inventory": inv,
        "peak_inventory": peak,
    }
