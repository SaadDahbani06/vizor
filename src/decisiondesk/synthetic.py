"""Two clearly synthetic example organizations; NEVER treat as real training evidence."""
from __future__ import annotations

import numpy as np
import pandas as pd


def make_sample(industry: str = "restaurant", days: int = 730, seed: int = 42) -> pd.DataFrame:
    if industry not in {"restaurant", "retail"}:
        raise ValueError("industry must be restaurant or retail")
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=days, freq="D")
    weekday = dates.day_of_week.to_numpy()
    seasonal = 1 + .10 * np.sin(2 * np.pi * np.arange(days) / 365.25)
    if industry == "restaurant":
        unit_base = 90 + 35 * np.isin(weekday, [4, 5, 6])
        unit_price, unit_variable_cost, daily_fixed = 19.0, 7.7, 980.0
    else:
        unit_base = 40 + 20 * np.isin(weekday, [5, 6])
        unit_price, unit_variable_cost, daily_fixed = 56.0, 30.0, 760.0
    units = np.maximum(0, rng.poisson(unit_base * seasonal)).astype(int)
    revenue = units * unit_price
    variable = units * unit_variable_cost * np.clip(rng.normal(1.0, .04, days), .85, 1.2)
    fixed = daily_fixed + np.where(weekday == 0, 150.0, 0.0)
    inventory = np.maximum(0, np.round(300 - np.cumsum(units - np.mean(units)) * .04)).astype(int)
    return pd.DataFrame({
        "date": dates,
        "revenue": revenue.round(2),
        "variable_costs": variable.round(2),
        "fixed_costs": fixed.round(2),
        "units_sold": units,
        "inventory_on_hand": inventory,
    })
