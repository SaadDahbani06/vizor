"""Deterministic recommendation ranking for a declared objective.

Ordering key: (objective metric mean in its declared direction, declared tie-breakers,
action_id). Only feasible actions are ranked; infeasible ones are listed with reasons.

Defensibility rules (stated in every Recommendation):
  * Reference = no action; if no action itself violates a constraint, the runner-up feasible
    action (and a single feasible action is labelled "only feasible option").
  * USD objectives: P(top beats the reference) over the paired simulations. When no action is
    feasible as the reference, the top action must also beat the runner-up: the rating is the
    weaker of the two comparisons. Because paired draws make even a $1 gap "certain", a USD gap
    below 1% of the reference outcome is treated as not distinguishable (materiality).
      >= 0.80 -> "robust" (unless sensitivity flips the top action -> "assumption-sensitive")
      0.60-0.80 -> "weak";  < 0.60 -> "not distinguishable" (no action is recommended)
  * Probability objectives: the paired reduction must exceed 2 Monte-Carlo standard errors
    and 1 percentage point, otherwise "not distinguishable".
Metrics with different units are never combined unless ``rank_weighted`` is called with
explicit weights; it min-max normalizes each metric across the feasible actions and says so.
"""
from __future__ import annotations

import numpy as np

from .engine import (
    METRIC_DIRECTION,
    Simulator,
    evaluate_actions,
    fix_assumption,
    uncertain_assumptions,
)
from .types import (
    OBJECTIVES,
    Action,
    Constraint,
    Objective,
    RankedAction,
    Recommendation,
    ScenarioOutcome,
    fmt_num,
)

ROBUST, WEAK = 0.80, 0.60
MATERIAL_PROBABILITY_CHANGE = 0.01
MATERIAL_SHARE = 0.01  # USD gaps below 1% of the reference outcome are practically equivalent


def _sort_key(outcome: ScenarioOutcome, objective: Objective):
    spec = OBJECTIVES[objective]
    key = []
    for metric, direction in ((spec.metric, spec.direction), *spec.tie_breakers):
        value = outcome.metrics[metric].mean if metric in outcome.metrics else 0.0
        key.append(-value if direction == "max" else value)
    return (*key, outcome.action.action_id)


def order(outcomes: list[ScenarioOutcome], objective: Objective) -> list[ScenarioOutcome]:
    feasible = sorted((o for o in outcomes if o.feasible), key=lambda o: _sort_key(o, objective))
    infeasible = sorted((o for o in outcomes if not o.feasible), key=lambda o: o.action.action_id)
    return feasible + infeasible


def _strength(top: ScenarioOutcome, reference: ScenarioOutcome,
              objective: Objective) -> tuple[str, float]:
    """Paired comparison of the top action against a reference (no action, or the runner-up
    feasible action when no action itself violates a constraint)."""
    spec = OBJECTIVES[objective]
    d = top.samples[spec.metric] - reference.samples[spec.metric]
    p = float(np.mean(d > 0) if spec.direction == "max" else np.mean(d < 0))
    if spec.unit == "probability":
        se = float(np.std(d, ddof=1) / np.sqrt(len(d))) if len(d) > 1 else 0.0
        improvement = -float(d.mean()) if spec.direction == "min" else float(d.mean())
        ok = improvement > max(2 * se, MATERIAL_PROBABILITY_CHANGE)
        return ("robust" if ok else "not distinguishable"), p
    threshold = max(MATERIAL_SHARE * abs(reference.metrics[spec.metric].mean), 1.0)
    if abs(float(d.mean())) < threshold:
        return "not distinguishable", p  # paired draws make tiny gaps "certain" but immaterial
    return ("robust" if p >= ROBUST else "weak" if p >= WEAK else "not distinguishable"), p


def rank(outcomes: list[ScenarioOutcome], objective: Objective, *, horizon_days: int,
         evidence: dict, limitations: list[str] | None = None,
         sensitivity: list[dict] | None = None) -> Recommendation:
    spec = OBJECTIVES[objective]
    limitations = list(limitations or [])
    baseline = next(o for o in outcomes if o.action.is_baseline)
    if spec.metric not in baseline.metrics:
        return Recommendation(objective, spec.metric, spec.unit, horizon_days, [], None,
                              "unavailable", f"Metric '{spec.metric}' cannot be computed for this "
                              "scenario with the available inputs.", limitations,
                              sensitivity or [], evidence)
    ordered = order(outcomes, objective)
    ranking, r = [], 0
    for o in ordered:
        r += o.feasible
        m, d = o.metrics[spec.metric], o.deltas[spec.metric]
        ranking.append(RankedAction(
            r if o.feasible else None, o.action.action_id, o.action.label, o.feasible,
            m.mean, m.p05, m.p95, d.mean, d.p05, d.p95,
            o.prob_better.get(spec.metric, 0.0), o.violations))
    feasible = [o for o in ordered if o.feasible]
    if not feasible:
        return Recommendation(objective, spec.metric, spec.unit, horizon_days, ranking, None,
                              "unavailable", "No candidate action satisfies the declared "
                              "constraints. " + _nearest_relaxation(outcomes),
                              limitations, sensitivity or [], evidence)
    top = feasible[0]
    chosen = top
    if top.action.is_baseline:
        strength = "robust"
        rationale = (f"No evaluated action beats doing nothing on {spec.metric} under the stated "
                     "assumptions.")
    else:
        reference = baseline if baseline.feasible else (feasible[1] if len(feasible) > 1 else None)
        if reference is None:
            strength = "only feasible option"
            rationale = (f"'{top.action.label}' is the only action meeting every constraint (no "
                         f"action violates: {'; '.join(baseline.violations)}). Its expected "
                         f"{spec.metric} is {fmt_num(top.metrics[spec.metric].mean)} {spec.unit}; "
                         "it is chosen for feasibility, not because it beats the alternatives "
                         f"on {spec.metric}.")
        elif np.array_equal(top.samples[spec.metric], reference.samples[spec.metric]):
            strength = "no difference"
            rationale = (f"Every feasible action has the same {spec.metric} as the reference in "
                         f"every simulation ({fmt_num(top.metrics[spec.metric].mean)} "
                         f"{spec.unit}); this objective does not distinguish the actions. "
                         "Consider another objective.")
        else:
            strength, p = _strength(top, reference, objective)
            versus = ("no action" if reference is baseline else
                      f"runner-up '{reference.action.label}' (no action is infeasible)")
            d = top.samples[spec.metric] - reference.samples[spec.metric]
            if spec.unit == "probability":
                se = float(np.std(d, ddof=1) / np.sqrt(len(d)))
                rationale = (f"'{top.action.label}' has the lowest {spec.metric} among feasible "
                             f"actions: {top.metrics[spec.metric].mean:.1%} vs "
                             f"{reference.metrics[spec.metric].mean:.1%} for {versus} (paired "
                             f"difference {float(d.mean()):+.1%}, Monte Carlo s.e. {se:.1%}).")
            else:
                rationale = (f"'{top.action.label}' has the best expected {spec.metric} among "
                             f"feasible actions; paired change vs {versus}: {fmt_num(float(d.mean()))} "
                             f"{spec.unit} (90% range {fmt_num(float(np.quantile(d, .05)))} to "
                             f"{fmt_num(float(np.quantile(d, .95)))}); P(better) = {p:.0%}. "
                             f"Change vs no action: {fmt_num(top.deltas[spec.metric].mean)} "
                             f"{spec.unit}.")
            runner_up = next((o for o in feasible[1:] if o is not reference), None)
            if reference is baseline and runner_up is not None and not runner_up.action.is_baseline:
                s2, p2 = _strength(top, runner_up, objective)
                d2 = top.samples[spec.metric] - runner_up.samples[spec.metric]
                rationale += (f" Versus the runner-up '{runner_up.action.label}': "
                              f"{fmt_num(float(d2.mean()))} {spec.unit} (90% range "
                              f"{fmt_num(float(np.quantile(d2, .05)))} to "
                              f"{fmt_num(float(np.quantile(d2, .95)))}), P(better) = {p2:.0%}.")
                if s2 == "not distinguishable" and strength != "not distinguishable":
                    strength = "weak"
                    rationale += (" The top two are not clearly separated; both beat no action, "
                                  "so the choice between them rests on the assumptions.")
                elif s2 == "weak" and strength == "robust":
                    strength = "weak"
    flips = [s for s in (sensitivity or []) if s["top_action"] != top.action.action_id]
    if flips and strength in ("robust", "weak"):
        strength = "assumption-sensitive"
        limitations.append("Top action changes when "
                           + "; ".join(f"{s['assumption']} is at its {s['level']} bound "
                                       f"(-> {s['top_action']})" for s in flips))
    recommended = (None if strength in ("not distinguishable", "no difference")
                   else chosen.action.action_id)
    if strength == "not distinguishable":
        rationale += (" The advantage is not distinguishable from forecast and assumption "
                      "uncertainty, so no change is recommended until better evidence exists.")
    return Recommendation(objective, spec.metric, spec.unit, horizon_days, ranking, recommended,
                          strength, rationale, limitations, sensitivity or [], evidence)


def _nearest_relaxation(outcomes: list[ScenarioOutcome]) -> str:
    """Which non-baseline action needs the smallest relative relaxation to become feasible."""
    best = None
    for o in outcomes:
        if o.action.is_baseline or not o.relaxations:
            continue
        worst = max(abs(r["needed_limit"] - r["declared_limit"]) / max(abs(r["declared_limit"]), 1e-9)
                    for r in o.relaxations.values())
        if best is None or (worst, o.action.action_id) < (best[0], best[1].action.action_id):
            best = (worst, o)
    if best is None:
        return ""
    o = best[1]
    parts = [f"{name} to {fmt_num(r['needed_limit'])} {r['unit']} "
             f"(declared {fmt_num(r['declared_limit'])})"
             for name, r in sorted(o.relaxations.items())]
    return (f"Smallest relaxation: '{o.action.label}' becomes feasible if " + "; ".join(parts)
            + ". Whether to relax a constraint is the user's decision.")


def sensitivity(actions: list[Action], simulator: Simulator, draws, constraints: tuple[Constraint, ...],
                units: dict[str, str], objective: Objective) -> list[dict]:
    """Fix each uncertain assumption at its low and high bound (others keep their
    distributions, same draws) and record which feasible action ranks first."""
    spec = OBJECTIVES[objective]
    rows = []
    for name in uncertain_assumptions(actions):
        for level in ("low", "high"):
            outs = evaluate_actions(fix_assumption(actions, name, level), simulator, draws,
                                    constraints, units)
            if spec.metric not in outs[0].metrics:
                continue
            ordered = [o for o in order(outs, objective) if o.feasible]
            if not ordered:
                continue
            rows.append({"assumption": name, "level": level,
                         "value": next(a.assumption(name).value for a in fix_assumption(
                             actions, name, level) if any(x.name == name for x in a.assumptions)),
                         "top_action": ordered[0].action.action_id,
                         "top_mean": ordered[0].metrics[spec.metric].mean})
    return rows


def rank_weighted(outcomes: list[ScenarioOutcome], weights: dict[str, float]) -> list[dict]:
    """Explicitly declared multi-metric score. Each metric is min-max normalized across the
    feasible actions to [0, 1] with 1 = best in that metric's direction, then weighted."""
    if not weights:
        raise ValueError("a weighted ranking requires explicitly declared weights")
    feasible = [o for o in outcomes if o.feasible]
    for metric in weights:
        if METRIC_DIRECTION.get(metric) is None:
            raise ValueError(f"metric {metric!r} has no declared direction")
        if any(metric not in o.metrics for o in feasible):
            raise ValueError(f"metric {metric!r} is not available for every action")
    total = sum(weights.values())
    scores = []
    for o in feasible:
        score = 0.0
        for metric, w in weights.items():
            vals = np.array([x.metrics[metric].mean for x in feasible])
            span = vals.max() - vals.min()
            norm = 0.5 if span == 0 else (o.metrics[metric].mean - vals.min()) / span
            score += (w / total) * (norm if METRIC_DIRECTION[metric] == "max" else 1 - norm)
        scores.append({"action_id": o.action.action_id, "score": score})
    scores.sort(key=lambda s: (-s["score"], s["action_id"]))
    for s in scores:
        s["normalization"] = "min-max across feasible actions; weights " + ", ".join(
            f"{k}={v / total:.2f}" for k, v in weights.items())
    return scores
