"""Orchestrates context -> baseline -> actions -> simulation -> constraints -> ranking -> evidence."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from ..leaderboard import EvaluationConfig, Leaderboard
from .baseline import ForecastBundle, build_forecast_bundle, make_draws
from .context import economic_context, stressed
from .engine import evaluate_actions
from .ranking import rank, sensitivity
from .restaurant import UNITS as RESTAURANT_UNITS
from .restaurant import RestaurantDecisionConfig, restaurant_actions, simulate_restaurant
from .retail import UNITS as RETAIL_UNITS
from .retail import RetailDecisionConfig, retail_actions, retail_inputs, simulate_retail
from .types import (
    OBJECTIVES,
    Action,
    Objective,
    OrganizationContext,
    Recommendation,
    ScenarioOutcome,
    Unsupported,
)

COMMON_LIMITATIONS = [
    "Action effects are modeled assumptions applied to forecasts, not estimated causal effects.",
    "Horizon is the validated 28 days; nothing here is a validated 90-day forecast.",
    "Cash-risk calibration is not established (6 holdout windows < 20 needed).",
]


@dataclass
class DecisionRun:
    scenario: str
    context: OrganizationContext
    objective: Objective
    bundle: ForecastBundle | None = None
    actions: list[Action] = field(default_factory=list)
    outcomes: list[ScenarioOutcome] = field(default_factory=list)
    recommendation: Recommendation | None = None
    other_objectives: dict[str, dict] = field(default_factory=dict)
    stress: dict | None = None
    economic: list[dict] = field(default_factory=list)
    economic_notes: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    unsupported: Unsupported | None = None


def _evidence(history: pd.DataFrame, bundle: ForecastBundle, ctx: OrganizationContext,
              actions: list[Action], econ: list[dict], draws_n: int, seed: int,
              constraints=()) -> dict:
    seen, assumptions = set(), []
    for a in actions:
        for asm in a.assumptions:
            if asm.name not in seen:
                seen.add(asm.name)
                assumptions.append({k: v for k, v in asdict(asm).items() if v not in (None, "")})
    return {
        "business_data": {"dataset": bundle.dataset, "label": ctx.data_label,
                          "rows": len(history), "first_date": str(history["date"].iloc[0].date()),
                          "origin_date": str(bundle.origin_date.date())},
        "forecasts": {t: {"selected_model": tf.model, "validation_wape": tf.validation_wape,
                          "test_wape": tf.test_wape, "validation_period": tf.validation_period,
                          "test_period": tf.test_period, "error_paths": tf.residuals.shape[0],
                          "selection_reason": tf.selection_reason}
                      for t, tf in bundle.targets.items()},
        "feature_notes": bundle.notes,
        "assumptions": assumptions,
        "economic_context": econ,
        "simulation": {"n": draws_n, "seed": seed, "method": (
            "7-day block bootstrap of the selected models' out-of-sample residual paths; "
            "fold indices shared across targets; common random numbers across actions")},
        "constraints": [asdict(c) for c in constraints],
        "horizon_days": bundle.horizon,
        "starting_cash": None if ctx.starting_cash is None else asdict(ctx.starting_cash),
    }


def _other_objectives(outcomes, horizon, evidence, objective) -> dict[str, dict]:
    out = {}
    for obj in Objective:
        if obj == objective:
            continue
        rec = rank(outcomes, obj, horizon_days=horizon, evidence={})
        out[obj.value] = {"metric": OBJECTIVES[obj].metric, "recommended": rec.recommended_action_id,
                          "strength": rec.strength, "rationale": rec.rationale}
    return out


def run_restaurant(history: pd.DataFrame, ctx: OrganizationContext, objective: Objective, *,
                   dataset: str, cfg: RestaurantDecisionConfig | None = None,
                   eval_cfg: EvaluationConfig | None = None, models=None, n_sims: int = 4000,
                   seed: int = 20261114, boards: dict[str, Leaderboard] | None = None,
                   bundle: ForecastBundle | None = None) -> DecisionRun:
    cfg = cfg or RestaurantDecisionConfig()
    bundle = bundle or build_forecast_bundle(
        history, ("revenue", "variable_costs", "fixed_costs"), dataset=dataset,
        data_label=ctx.data_label, cfg=eval_cfg, models=models, boards=boards)
    draws = make_draws(bundle, n_sims, seed)
    actions = restaurant_actions(cfg)

    def sim(action, d, b=bundle):
        return simulate_restaurant(b, action, d, ctx.starting_cash)

    outcomes = evaluate_actions(actions, sim, draws, cfg.constraints, RESTAURANT_UNITS)
    sens = sensitivity(actions, sim, make_draws(bundle, min(n_sims, 2000), seed),
                       cfg.constraints, RESTAURANT_UNITS, objective)
    econ, econ_notes = economic_context("restaurant", bundle.origin_date)
    limitations = list(COMMON_LIMITATIONS)
    if ctx.starting_cash is None:
        limitations.append("Cash-shortage risk not computed: no user-confirmed starting cash "
                           "balance was provided (Nessie balances are not used: they are static).")
    evidence = _evidence(history, bundle, ctx, actions, econ, n_sims, seed, cfg.constraints)
    rec = rank(outcomes, objective, horizon_days=bundle.horizon, evidence=evidence,
               limitations=limitations, sensitivity=sens)
    run = DecisionRun("restaurant", ctx, objective, bundle, actions, outcomes, rec,
                      _other_objectives(outcomes, bundle.horizon, evidence, objective),
                      economic=econ, economic_notes=econ_notes)

    food = next((e for e in econ if e["series_id"] == "CUSR0000SAF11"
                 and e["yoy_pct"] is not None), None)
    if food is None:
        run.economic_notes.append("Food-cost stress test skipped: no published food-at-home CPI "
                                  "change available as of the origin.")
    else:
        shock = max(food["yoy_pct"], 0.0) / 100
        sb = stressed(bundle, {"variable_costs": 1 + shock})
        souts = evaluate_actions(actions, lambda a, d: sim(a, d, sb), draws, cfg.constraints,
                                 RESTAURANT_UNITS)
        srec = rank(souts, objective, horizon_days=bundle.horizon, evidence={})
        base = next(o for o in souts if o.action.is_baseline)
        run.stress = {
            "category": "hypothetical_change",
            "description": f"Variable costs {shock:+.2%} above forecast, sized to the latest "
                           f"observed food-at-home CPI YoY ({food['yoy_pct']:+.2f}% for "
                           f"{food['yoy_period']}; latest release {food['observed_at'][:7]}, "
                           f"published {food['published_at']}). "
                           "No pass-through relationship has been estimated.",
            "baseline_profit_mean": base.metrics["profit"].mean,
            "recommended": srec.recommended_action_id, "strength": srec.strength,
        }
    return run


def run_retail(history: pd.DataFrame, ctx: OrganizationContext, objective: Objective, *,
               dataset: str, cfg: RetailDecisionConfig | None = None,
               eval_cfg: EvaluationConfig | None = None, models=None, n_sims: int = 4000,
               seed: int = 20261114, boards: dict[str, Leaderboard] | None = None,
               bundle: ForecastBundle | None = None) -> DecisionRun:
    cfg = cfg or RetailDecisionConfig()
    inputs = retail_inputs(history)
    if isinstance(inputs, Unsupported):
        return DecisionRun("retail", ctx, objective, unsupported=inputs)
    bundle = bundle or build_forecast_bundle(history, ("units_sold",), dataset=dataset,
                                             data_label=ctx.data_label, cfg=eval_cfg,
                                             models=models, boards=boards)
    if cfg.lead_time_days >= bundle.horizon:
        return DecisionRun("retail", ctx, objective, bundle, unsupported=Unsupported(
            "retail_inventory", ["lead_time_days"], "Lead time is not shorter than the "
            "validated horizon, so an order placed now cannot be evaluated."))
    draws = make_draws(bundle, n_sims, seed)
    actions, levels = retail_actions(bundle, inputs, cfg)

    def sim(action, d, b=bundle):
        return simulate_retail(b, action, d, inputs)

    outcomes = evaluate_actions(actions, sim, draws, cfg.constraints, RETAIL_UNITS)
    sens = sensitivity(actions, sim, make_draws(bundle, min(n_sims, 2000), seed),
                       cfg.constraints, RETAIL_UNITS, objective)
    econ, econ_notes = economic_context("retail", bundle.origin_date)
    limitations = [*COMMON_LIMITATIONS,
                   ("Cash-shortage risk is not modeled for inventory decisions (needs "
                    "payables timing and a full cash model)."),
                   "Weekly review, deterministic lead time; lost sales are not backordered.",
                   "Synthetic inventory_on_hand has no receipts modeled; levels are illustrative."]
    evidence = _evidence(history, bundle, ctx, actions, econ, n_sims, seed, cfg.constraints)
    evidence["inventory"] = {"current_inventory": inputs.current_inventory,
                             "as_of": str(inputs.inventory_date.date()),
                             "lead_time_days": cfg.lead_time_days,
                             "review_days": cfg.review_days, "service_level": cfg.service_level,
                             "policy_levels": levels,
                             "sizing": "independent draw set (seed 7), not the evaluation draws"}
    rec = rank(outcomes, objective, horizon_days=bundle.horizon, evidence=evidence,
               limitations=limitations, sensitivity=sens)
    run = DecisionRun("retail", ctx, objective, bundle, actions, outcomes, rec,
                      _other_objectives(outcomes, bundle.horizon, evidence, objective),
                      economic=econ, economic_notes=econ_notes, extra=levels)

    sb = stressed(bundle, {"units_sold": 0.9})
    souts = evaluate_actions(actions, lambda a, d: sim(a, d, sb), draws, cfg.constraints,
                             RETAIL_UNITS)
    srec = rank(souts, objective, horizon_days=bundle.horizon, evidence={})
    sales = next((e for e in econ if e["series_id"] == "RSXFS"), None)
    run.stress = {
        "category": "hypothetical_change",
        "description": "Demand 10% below forecast (hypothetical). "
                       + (f"Context only: advance retail sales YoY {sales['yoy_pct']:+.2f}% for "
                          f"{sales['observed_at'][:7]} (published {sales['published_at']}); no "
                          "relationship to this store's demand has been estimated."
                          if sales and sales["yoy_pct"] is not None else ""),
        "baseline_profit_mean": next(o for o in souts if o.action.is_baseline).metrics["profit"].mean,
        "recommended": srec.recommended_action_id, "strength": srec.strength,
    }
    return run
