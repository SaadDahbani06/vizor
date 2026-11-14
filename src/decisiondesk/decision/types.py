"""Typed objects for the decision engine.

Layers (each a separate module):
  context    -> OrganizationContext, StartingCash          (who / what data / which horizon)
  objective  -> Objective + ObjectiveSpec                  (what is optimized, in which unit)
  actions    -> Action with Assumptions                     (what the user can actually do)
  baseline   -> ForecastBundle (selected models)            (no-action forecast + error paths)
  scenarios  -> restaurant.py / retail.py                   (explicit formulas, simulation)
  feasibility-> Constraint                                  (hard limits)
  ranking    -> Recommendation                              (deterministic ordering)
  evidence   -> provenance dicts + explanation text         (why, from which data)

Modeled effects are ASSUMPTIONS applied to forecasts, never estimated causal effects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np


def fmt_num(x: float) -> str:
    """Readable number: thousands separators for large values, 4 significant digits otherwise."""
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.4g}"


class Objective(StrEnum):
    MAX_EXPECTED_PROFIT = "max_expected_profit"
    MIN_OPERATING_COST = "min_operating_cost"
    MIN_CASH_SHORTAGE_RISK = "min_cash_shortage_risk"
    MIN_STOCKOUT_RISK = "min_stockout_risk"


@dataclass(frozen=True)
class ObjectiveSpec:
    objective: Objective
    metric: str
    unit: str
    direction: str  # "max" | "min"
    tie_breakers: tuple[tuple[str, str], ...]  # ((metric, direction), ...) then action_id


OBJECTIVES: dict[Objective, ObjectiveSpec] = {
    Objective.MAX_EXPECTED_PROFIT: ObjectiveSpec(
        Objective.MAX_EXPECTED_PROFIT, "profit", "USD", "max", (("operating_cost", "min"),)),
    Objective.MIN_OPERATING_COST: ObjectiveSpec(
        Objective.MIN_OPERATING_COST, "operating_cost", "USD", "min", (("profit", "max"),)),
    Objective.MIN_CASH_SHORTAGE_RISK: ObjectiveSpec(
        Objective.MIN_CASH_SHORTAGE_RISK, "cash_shortage", "probability", "min",
        (("profit", "max"), ("reserved_cash", "min"))),
    Objective.MIN_STOCKOUT_RISK: ObjectiveSpec(
        Objective.MIN_STOCKOUT_RISK, "stockout_after_arrival", "probability", "min",
        (("lost_units", "min"), ("profit", "max"))),
}

ASSUMPTION_SOURCES = ("user_declared", "derived_from_data", "default_needs_confirmation",
                      "observed_external", "hypothetical")


@dataclass(frozen=True)
class Assumption:
    """A number the scenario depends on. Uncertain ones carry a [low, high] range and are
    sampled with common random numbers (the same draw for every action)."""

    name: str
    value: float
    unit: str
    source: str
    low: float | None = None
    high: float | None = None
    distribution: str = "fixed"  # "fixed" | "uniform" | "triangular" (mode = value)
    note: str = ""

    def __post_init__(self) -> None:
        if self.source not in ASSUMPTION_SOURCES:
            raise ValueError(f"assumption source must be one of {ASSUMPTION_SOURCES}")
        if self.distribution not in ("fixed", "uniform", "triangular"):
            raise ValueError("distribution must be fixed, uniform or triangular")
        if self.distribution != "fixed" and (
                self.low is None or self.high is None or not self.low <= self.value <= self.high):
            raise ValueError(f"{self.name}: uncertain assumptions need low <= value <= high")

    def sample(self, u: np.ndarray) -> np.ndarray:
        """Map uniform(0,1) draws to this assumption's distribution."""
        if self.distribution == "fixed":
            return np.full(u.shape, float(self.value))
        lo, hi = float(self.low), float(self.high)
        if hi == lo:
            return np.full(u.shape, lo)
        if self.distribution == "uniform":
            return lo + u * (hi - lo)
        c = (self.value - lo) / (hi - lo)
        return np.where(u < c, lo + np.sqrt(u * (hi - lo) * (self.value - lo)),
                        hi - np.sqrt((1 - u) * (hi - lo) * (hi - self.value)))

    def at(self, which: str) -> Assumption:
        """Deterministic copy fixed at 'low', 'value' or 'high' (for sensitivity analysis)."""
        level = {"low": self.low, "value": self.value, "high": self.high}[which]
        return Assumption(self.name, float(level), self.unit, self.source, note=self.note)


@dataclass(frozen=True)
class StartingCash:
    amount: float
    source: str
    confirmed_by_user: bool

    def __post_init__(self) -> None:
        if not np.isfinite(self.amount):
            raise ValueError("starting cash must be finite")


@dataclass(frozen=True)
class OrganizationContext:
    name: str
    business_type: str  # "restaurant" | "retail"
    data_label: str     # "SYNTHETIC" | "UPLOADED USER DATA"
    currency: str = "USD"
    starting_cash: StartingCash | None = None


@dataclass(frozen=True)
class Action:
    action_id: str
    label: str
    kind: str
    parameters: dict = field(default_factory=dict)
    assumptions: tuple[Assumption, ...] = ()
    implementation_cost: float = 0.0
    is_baseline: bool = False

    def assumption(self, name: str) -> Assumption:
        for a in self.assumptions:
            if a.name == name:
                return a
        raise KeyError(f"{self.action_id} has no assumption {name!r}")


@dataclass(frozen=True)
class Constraint:
    """Hard limit. ``target`` is either an action parameter ("param:<name>") or an outcome
    metric ("metric:<name>[:p95|:mean]"); ``kind`` is "max" or "min"."""

    name: str
    target: str
    kind: str
    limit: float
    unit: str
    source: str = "user_declared"

    def __post_init__(self) -> None:
        if self.kind not in ("max", "min"):
            raise ValueError("constraint kind must be max or min")
        if not (self.target.startswith("param:") or self.target.startswith("metric:")):
            raise ValueError("constraint target must start with 'param:' or 'metric:'")


@dataclass
class MetricSummary:
    name: str
    unit: str
    mean: float
    p05: float
    p95: float

    @classmethod
    def of(cls, name: str, unit: str, samples: np.ndarray) -> MetricSummary:
        s = np.asarray(samples, dtype=float)
        return cls(name, unit, float(s.mean()), float(np.quantile(s, .05)),
                   float(np.quantile(s, .95)))


@dataclass
class ScenarioOutcome:
    action: Action
    metrics: dict[str, MetricSummary]
    deltas: dict[str, MetricSummary]          # paired (common random numbers) vs no-action
    prob_better: dict[str, float]             # P(action beats no-action) per metric
    feasible: bool = True
    violations: list[str] = field(default_factory=list)
    relaxations: dict[str, dict] = field(default_factory=dict)  # constraint -> needed limit
    notes: list[str] = field(default_factory=list)
    samples: dict[str, np.ndarray] = field(default_factory=dict, repr=False)


@dataclass
class Unsupported:
    scenario: str
    missing: list[str]
    reason: str


@dataclass
class RankedAction:
    rank: int | None
    action_id: str
    label: str
    feasible: bool
    objective_mean: float
    objective_p05: float
    objective_p95: float
    delta_mean: float
    delta_p05: float
    delta_p95: float
    prob_better_than_no_action: float
    violations: list[str]


@dataclass
class Recommendation:
    objective: Objective
    metric: str
    unit: str
    horizon_days: int
    ranking: list[RankedAction]
    recommended_action_id: str | None
    strength: str  # "robust" | "assumption-sensitive" | "not distinguishable" | "unavailable"
    rationale: str
    limitations: list[str]
    sensitivity: list[dict]
    evidence: dict
    effect_type: str = ("modeled scenario under stated assumptions; NOT an estimated causal "
                        "effect")
    requires_human_approval: bool = True
