"""Restaurant financial scenarios on top of the selected-model baseline.

Per simulated day t (baseline paths R_t revenue, V_t variable costs, F_t fixed costs; an action
takes effect from day ``start_day``; implementation cost is paid on day 0):

  no_action      R, V, F unchanged.
  price          volume multiplier m = (1 + p) ** e, with price elasticity e < 0 (constant-
                 elasticity demand). R' = R (1 + p) m, V' = V m, F' = F.
                 Revenue does NOT rise one-for-one with price; with e < -1 it falls.
  supplier       V' = V (1 - s * share * success), success ~ Bernoulli(q) for the negotiation.
  discretionary  F' = F (1 - c * share); demand side effect k: R' = R (1 - k), V' = V (1 - k).

Outcome metrics over the horizon: profit = sum(R' - V' - F') - implementation cost,
operating_cost = sum(V' + F') + implementation cost, revenue, margin = profit / revenue,
and, ONLY when a user-confirmed starting cash balance is supplied, cash_shortage
(indicator that cumulative cash falls below zero on any day) and min_cash.

Elasticity, success probability and side effects are ASSUMPTIONS. The synthetic data has no
price variation, so no elasticity is estimated from it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .baseline import ForecastBundle, SimulationDraws
from .types import Action, Assumption, Constraint, StartingCash

TARGETS = ("revenue", "variable_costs", "fixed_costs")
UNITS = {"profit": "USD", "revenue": "USD", "operating_cost": "USD", "margin": "ratio",
         "cash_shortage": "probability", "min_cash": "USD"}


ELASTICITY = Assumption(
    "price_elasticity", -0.8, "ratio", "default_needs_confirmation", -1.2, -0.4,
    "triangular", "Assumed range; not estimated (no price variation in the data). A widely "
    "cited meta-analysis puts food-away-from-home elasticity near -0.8.")
SUPPLIER_SHARE = Assumption(
    "supplier_share_of_variable_costs", 0.85, "share", "default_needs_confirmation",
    note="Share of variable costs covered by the negotiable supplier contract.")
SUPPLIER_SUCCESS = Assumption(
    "supplier_success_probability", 0.6, "probability", "default_needs_confirmation", 0.4,
    0.8, "uniform", "Chance the supplier agrees; drawn per simulation.")
DISCRETIONARY_SHARE = Assumption(
    "discretionary_share_of_fixed_costs", 0.10, "share", "default_needs_confirmation",
    note="Marketing, subscriptions, non-essential services within fixed costs.")
DISCRETIONARY_DEMAND_LOSS = Assumption(
    "discretionary_cut_demand_loss", 0.005, "share", "default_needs_confirmation", 0.0,
    0.015, "uniform", "Possible lost sales from reduced marketing/hours.")


@dataclass(frozen=True)
class RestaurantDecisionConfig:
    price_increases: tuple[float, ...] = (0.03, 0.08)
    menu_change_cost: float = 150.0
    elasticity: Assumption = ELASTICITY
    supplier_reduction: float = 0.04
    supplier_share: Assumption = SUPPLIER_SHARE
    supplier_success: Assumption = SUPPLIER_SUCCESS
    supplier_start_day: int = 7
    supplier_cost: float = 250.0
    discretionary_share: Assumption = DISCRETIONARY_SHARE
    discretionary_cut: float = 0.30
    discretionary_demand_loss: Assumption = DISCRETIONARY_DEMAND_LOSS
    constraints: tuple[Constraint, ...] = field(default_factory=lambda: (
        Constraint("max price increase", "param:price_increase", "max", 0.05, "fraction"),
        Constraint("implementation budget", "param:implementation_cost", "max", 1000.0, "USD"),
    ))


def restaurant_actions(cfg: RestaurantDecisionConfig) -> list[Action]:
    actions = [Action("no_action", "Do nothing (baseline)", "no_action", is_baseline=True)]
    for p in cfg.price_increases:
        actions.append(Action(
            f"raise_prices_{p * 100:g}pct".replace(".", "_"), f"Raise menu prices {p * 100:g}%",
            "price",
            {"price_increase": p, "start_day": 0}, (cfg.elasticity,), cfg.menu_change_cost))
    actions.append(Action(
        f"supplier_{round(cfg.supplier_reduction * 100)}pct",
        f"Negotiate {cfg.supplier_reduction:.0%} lower supplier prices", "supplier",
        {"supplier_reduction": cfg.supplier_reduction, "start_day": cfg.supplier_start_day},
        (cfg.supplier_share, cfg.supplier_success), cfg.supplier_cost))
    actions.append(Action(
        "cut_discretionary", f"Cut discretionary spending {cfg.discretionary_cut:.0%}",
        "discretionary", {"discretionary_cut": cfg.discretionary_cut, "start_day": 0},
        (cfg.discretionary_share, cfg.discretionary_demand_loss), 0.0))
    return actions


def simulate_restaurant(bundle: ForecastBundle, action: Action, draws: SimulationDraws,
                        starting_cash: StartingCash | None = None) -> dict[str, np.ndarray]:
    base = bundle.simulate(draws)
    R, V, F = (base[t].copy() for t in TARGETS)
    H = bundle.horizon
    active = np.arange(H) >= int(action.parameters.get("start_day", 0))

    def draw(name: str) -> np.ndarray:
        return action.assumption(name).sample(draws.uniform(name))[:, None]

    if action.kind == "price":
        p = float(action.parameters["price_increase"])
        m = (1 + p) ** draw("price_elasticity")
        R = np.where(active, R * (1 + p) * m, R)
        V = np.where(active, V * m, V)
    elif action.kind == "supplier":
        s = float(action.parameters["supplier_reduction"])
        share = draw("supplier_share_of_variable_costs")
        q = draw("supplier_success_probability")
        success = (draws.uniform("supplier_negotiation_outcome")[:, None] < q).astype(float)
        V = np.where(active, V * (1 - s * share * success), V)
    elif action.kind == "discretionary":
        c = float(action.parameters["discretionary_cut"])
        share = draw("discretionary_share_of_fixed_costs")
        k = draw("discretionary_cut_demand_loss")
        F = np.where(active, F * (1 - c * share), F)
        R, V = R * (1 - k), V * (1 - k)
    elif action.kind != "no_action":
        raise ValueError(f"unknown restaurant action kind {action.kind!r}")

    impl = float(action.implementation_cost)
    daily = R - V - F
    revenue = R.sum(axis=1)
    out = {"profit": daily.sum(axis=1) - impl, "revenue": revenue,
           "operating_cost": (V + F).sum(axis=1) + impl,
           "margin": np.divide(daily.sum(axis=1) - impl, revenue, out=np.zeros_like(revenue),
                               where=revenue > 0)}
    if starting_cash is not None and starting_cash.confirmed_by_user:
        cash = starting_cash.amount - impl + np.cumsum(daily, axis=1)
        out["cash_shortage"] = (cash.min(axis=1) < 0).astype(float)
        out["min_cash"] = cash.min(axis=1)
    return out


def analytic_expected_delta(bundle: ForecastBundle, action: Action) -> float:
    """Closed-form expected profit change vs no action using point forecasts, valid when
    assumptions are fixed (or enter linearly) and residuals are zero. Used to test the simulator."""
    R, V, F = (bundle.targets[t].point for t in TARGETS)
    active = np.arange(bundle.horizon) >= int(action.parameters.get("start_day", 0))
    if action.kind == "no_action":
        return 0.0
    if action.kind == "price":
        p = action.parameters["price_increase"]
        m = (1 + p) ** action.assumption("price_elasticity").value
        return float(((1 + p) * m - 1) * R[active].sum() - (m - 1) * V[active].sum()
                     - action.implementation_cost)
    if action.kind == "supplier":
        s = action.parameters["supplier_reduction"]
        share = action.assumption("supplier_share_of_variable_costs").value
        q = action.assumption("supplier_success_probability").value
        return float(q * s * share * V[active].sum() - action.implementation_cost)
    if action.kind == "discretionary":
        c = action.parameters["discretionary_cut"]
        share = action.assumption("discretionary_share_of_fixed_costs").value
        k = action.assumption("discretionary_cut_demand_loss").value
        return float(c * share * F[active].sum() - k * (R.sum() - V.sum())
                     - action.implementation_cost)
    raise ValueError(action.kind)
