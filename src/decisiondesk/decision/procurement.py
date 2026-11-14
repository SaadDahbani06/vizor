"""Procurement cash-reserve decisions from a forecast of PURCHASE spend (no demand assumed).

For businesses whose data shows what they buy but not what they sell (e.g. the Iowa liquor
purchase records), the supportable decision is how much cash to set aside for the next horizon of
purchasing. Ordering/inventory policies are NOT supported without demand and inventory data.

Per simulated path: spend = sum of simulated daily purchases x (1 + wholesale price change)
  cash_shortage      = 1 if spend > reserve (the reserve is overrun)
  expected_shortfall = max(0, spend - reserve)      idle_cash = max(0, reserve - spend)
Actions: keep last period's budget (no action) | reserve at quantiles of forecast spend, sized on an
independent draw set. The objective is usually min_cash_shortage_risk under a user-declared limit on
cash available for purchasing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .baseline import ForecastBundle, SimulationDraws, make_draws
from .types import Action, Assumption, Constraint

UNITS = {"cash_shortage": "probability", "reserved_cash": "USD", "expected_shortfall": "USD",
         "idle_cash": "USD", "spend": "USD"}
PRICE_CHANGE = Assumption("wholesale_price_change", 0.0, "fraction", "user_declared",
                          note="0 = forecast spend as is; stress tests set a hypothetical change")


@dataclass(frozen=True)
class ProcurementConfig:
    target: str = "purchase_dollars"
    quantiles: tuple[float, ...] = (0.5, 0.8, 0.95)
    price_change: Assumption = PRICE_CHANGE
    constraints: tuple[Constraint, ...] = field(default_factory=tuple)


def procurement_actions(bundle: ForecastBundle, history: pd.DataFrame,
                        cfg: ProcurementConfig) -> tuple[list[Action], dict]:
    last = float(history[cfg.target].tail(bundle.horizon).sum())
    sizing = bundle.simulate(make_draws(bundle, 4000, seed=7))[cfg.target].sum(axis=1)
    levels = {q: float(np.ceil(np.quantile(sizing, q))) for q in cfg.quantiles}
    asm = (cfg.price_change,)
    actions = [Action("keep_last_budget", f"Keep last {bundle.horizon} days' spend as the budget "
                      f"(${last:,.0f})", "reserve", {"reserve": last}, asm, is_baseline=True)]
    for q, v in levels.items():
        actions.append(Action(f"reserve_p{round(q * 100)}",
                              f"Reserve ${v:,.0f} (P{round(q * 100)} of forecast spend)",
                              "reserve", {"reserve": v}, asm))
    return actions, {"last_period_spend": last, **{f"p{round(q * 100)}": v for q, v in levels.items()}}


def simulate_procurement(bundle: ForecastBundle, action: Action, draws: SimulationDraws,
                         target: str = "purchase_dollars") -> dict[str, np.ndarray]:
    change = action.assumption("wholesale_price_change").sample(
        draws.uniform("wholesale_price_change"))
    spend = bundle.simulate(draws)[target].sum(axis=1) * (1 + change)
    reserve = float(action.parameters["reserve"])
    return {"cash_shortage": (spend > reserve).astype(float),
            "reserved_cash": np.full(draws.n, reserve),
            "expected_shortfall": np.maximum(0.0, spend - reserve),
            "idle_cash": np.maximum(0.0, reserve - spend), "spend": spend}
