"""Compare explicit assumptions; modeled effects are NOT proven causal improvements."""
from __future__ import annotations

import pandas as pd


def compare_actions(forecast: pd.DataFrame, *, price_increase: float = .04,
                    demand_elasticity: float = 1.2, supplier_discount: float = .05) -> pd.DataFrame:
    for value in (price_increase, demand_elasticity, supplier_discount):
        if value < 0:
            raise ValueError("Scenario assumptions must be nonnegative")
    base = forecast.revenue.sum() - forecast.variable_costs.sum() - forecast.fixed_costs.sum()
    # For pricing, model volume response as 1 - elasticity * % price increase.
    volume_factor = max(0.0, 1 - demand_elasticity * price_increase)
    pricing_rev = (forecast.revenue * (1 + price_increase) * volume_factor).sum()
    pricing_var = (forecast.variable_costs * volume_factor).sum()
    pricing_net = pricing_rev - pricing_var - forecast.fixed_costs.sum()
    supplier_net = (forecast.revenue - forecast.variable_costs * (1 - supplier_discount)
                    - forecast.fixed_costs).sum()
    return pd.DataFrame([
        {"action": "No change", "projected_net_cash": base, "delta_vs_baseline": 0.0,
         "assumptions": "No adjustments"},
        {"action": "Raise prices", "projected_net_cash": pricing_net,
         "delta_vs_baseline": pricing_net - base,
         "assumptions": f"Price +{100*price_increase:.1f}%; elasticity {demand_elasticity:.2f} (assumed)"},
        {"action": "Negotiate supplier discount", "projected_net_cash": supplier_net,
         "delta_vs_baseline": supplier_net - base,
         "assumptions": f"Variable costs -{100*supplier_discount:.1f}% (assumed)"},
    ]).sort_values("projected_net_cash", ascending=False, ignore_index=True)


def operational_inventory_signal(history: pd.DataFrame, *, lead_days: int = 7,
                                 service_days: int = 3) -> dict:
    if "units_sold" not in history or "inventory_on_hand" not in history:
        return {"supported": False, "reason": "Requires units_sold and inventory_on_hand"}
    if lead_days < 0 or service_days < 0:
        raise ValueError("lead_days and service_days must be nonnegative")
    demand_daily = float(history.units_sold.tail(28).mean())
    available = float(history.inventory_on_hand.iloc[-1])
    threshold = (lead_days + service_days) * demand_daily
    return {"supported": True, "average_daily_demand": demand_daily,
            "reported_inventory_on_hand": available,
            "simple_reorder_threshold": threshold,
            "suggest_reorder_review": available < threshold,
            "notes": "Heuristic, not cost-optimized; validate lead time, stockouts and inventory accuracy."}
