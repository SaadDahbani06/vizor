"""Scenario evaluation: simulate every action on the SAME draws, compare with no-action, check
constraints, and run one-at-a-time assumption sensitivity."""
from __future__ import annotations

import dataclasses
from collections.abc import Callable

import numpy as np

from .baseline import SimulationDraws
from .types import Action, Constraint, MetricSummary, ScenarioOutcome, fmt_num

Simulator = Callable[[Action, SimulationDraws], dict[str, np.ndarray]]

# Which direction is "better" for each metric (None = descriptive only).
METRIC_DIRECTION = {
    "profit": "max", "revenue": "max", "margin": "max", "fill_rate": "max",
    "operating_cost": "min", "cash_shortage": "min", "stockout_any": "min",
    "stockout_after_arrival": "min", "lost_units": "min", "cash_outlay": "min",
    "min_cash": "max", "ending_inventory": None, "peak_inventory": None,
    "reserved_cash": "min", "expected_shortfall": "min", "idle_cash": "min", "spend": None,
}


def _summary_stat(summary: MetricSummary, stat: str) -> float:
    return {"mean": summary.mean, "p05": summary.p05, "p95": summary.p95}[stat]


def check_constraints(action: Action, metrics: dict[str, MetricSummary],
                      constraints: tuple[Constraint, ...]) -> list[dict]:
    """Return one dict per violated constraint: name, value, limit, kind, unit, message."""
    violations = []
    for c in constraints:
        kind, _, rest = c.target.partition(":")
        if kind == "param":
            if rest == "implementation_cost":
                value = action.implementation_cost
            elif rest in action.parameters:
                value = float(action.parameters[rest])
            else:
                continue  # constraint does not apply to this action
        else:
            name, _, stat = rest.partition(":")
            if name not in metrics:
                continue
            value = _summary_stat(metrics[name], stat or "mean")
        bad = value > c.limit if c.kind == "max" else value < c.limit
        if bad:
            violations.append({
                "name": c.name, "value": value, "limit": c.limit, "kind": c.kind, "unit": c.unit,
                "message": f"{c.name}: {fmt_num(value)} {c.unit} {'>' if c.kind == 'max' else '<'} "
                           f"limit {fmt_num(c.limit)}"})
    return violations


def evaluate_actions(actions: list[Action], simulator: Simulator, draws: SimulationDraws,
                     constraints: tuple[Constraint, ...], units: dict[str, str]) -> list[ScenarioOutcome]:
    baselines = [a for a in actions if a.is_baseline]
    if len(baselines) != 1:
        raise ValueError("exactly one no-action baseline is required")
    ids = [a.action_id for a in actions]
    if len(set(ids)) != len(ids):
        raise ValueError("action ids must be unique")
    samples = {a.action_id: simulator(a, draws) for a in actions}
    base = samples[baselines[0].action_id]
    outcomes = []
    for a in actions:
        s = samples[a.action_id]
        metrics = {m: MetricSummary.of(m, units[m], v) for m, v in s.items()}
        deltas, prob_better = {}, {}
        for m, v in s.items():
            d = v - base[m]
            deltas[m] = MetricSummary.of(m, units[m], d)
            direction = METRIC_DIRECTION.get(m)
            if direction:
                prob_better[m] = float(np.mean(d > 0) if direction == "max" else np.mean(d < 0))
        outcome = ScenarioOutcome(a, metrics, deltas, prob_better, samples=s)
        found = check_constraints(a, metrics, constraints)
        outcome.violations = [v["message"] for v in found]
        outcome.relaxations = {v["name"]: {"needed_limit": v["value"], "declared_limit": v["limit"],
                                           "unit": v["unit"]} for v in found}
        outcome.feasible = not found
        outcomes.append(outcome)
    return outcomes


def uncertain_assumptions(actions: list[Action]) -> list[str]:
    names = []
    for a in actions:
        for asm in a.assumptions:
            if asm.distribution != "fixed" and asm.name not in names:
                names.append(asm.name)
    return names


def fix_assumption(actions: list[Action], name: str, level: str) -> list[Action]:
    return [dataclasses.replace(a, assumptions=tuple(
        asm.at(level) if asm.name == name else asm for asm in a.assumptions)) for a in actions]
