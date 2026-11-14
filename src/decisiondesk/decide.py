"""Decision CLI: rank candidate actions against doing nothing, for a declared objective.

  python -m decisiondesk.decide --scenario restaurant
  python -m decisiondesk.decide --scenario restaurant --objective min_cash_shortage_risk --starting-cash 12000
  python -m decisiondesk.decide --scenario retail --objective min_stockout_risk

All numbers are computed at run time from the data, the selected forecasting models and the
declared assumptions. Nothing is executed: every recommendation needs human approval.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from .data import DatasetSpec, ingest_daily_csv, validate_daily_frame
from .decision.runner import DecisionRun, run_restaurant, run_retail
from .decision.types import OBJECTIVES, Objective, OrganizationContext, StartingCash, fmt_num
from .models import REGISTRY
from .synthetic import make_sample

DEFAULT_OBJECTIVE = {"restaurant": Objective.MAX_EXPECTED_PROFIT,
                     "retail": Objective.MAX_EXPECTED_PROFIT}


def _fmt(x: float, unit: str) -> str:
    if unit == "probability":
        return f"{x:.1%}"
    if unit == "ratio":
        return f"{x:.3f}"
    if unit == "units":
        return f"{x:,.0f} u"
    return f"${x:,.0f}"


def print_run(run: DecisionRun) -> None:
    ctx = run.context
    print(f"\n######## Scenario: {run.scenario.upper()} | data: {ctx.data_label} | "
          f"{ctx.name}")
    if run.unsupported:
        u = run.unsupported
        print(f"UNSUPPORTED ({u.scenario}): missing {u.missing}. {u.reason}")
        return
    b, rec = run.bundle, run.recommendation
    print("\n1. Selected forecasting models (validation-selected; untouched test reported)")
    for t, tf in b.targets.items():
        print(f"   {t:15s} {tf.model:17s} val WAPE {tf.validation_wape:.3f} | test WAPE "
              f"{tf.test_wape:.3f} | {tf.residuals.shape[0]} out-of-sample error paths")
    print(f"\n2. Baseline (no action) forecast: {b.targets[next(iter(b.targets))].dates[0].date()} "
          f".. {b.targets[next(iter(b.targets))].dates[-1].date()} ({b.horizon} days)")
    base = next(o for o in run.outcomes if o.action.is_baseline)
    for t, tf in b.targets.items():
        print(f"   {t:15s} total {tf.point.sum():>12,.0f}  (daily mean {tf.point.mean():,.1f})")
    for m in ("profit", "revenue", "operating_cost", "lost_units", "stockout_after_arrival"):
        if m in base.metrics:
            s = base.metrics[m]
            print(f"   baseline {m:22s} mean {_fmt(s.mean, s.unit):>10} "
                  f"[90%: {_fmt(s.p05, s.unit)} .. {_fmt(s.p95, s.unit)}]")
    spec = OBJECTIVES[run.objective]
    print(f"\n3. Objective: {run.objective.value} -> {spec.direction} {spec.metric} ({spec.unit}) "
          f"over {b.horizon} days")
    print("\n4. Candidate actions")
    for a in run.actions:
        params = {k: v for k, v in a.parameters.items()}
        print(f"   {a.action_id:24s} {a.label}  params={params} "
              f"implementation_cost=${a.implementation_cost:,.0f}")
    print("\n5. Expected outcomes (mean [5%..95%]; delta = paired change vs no action)")
    metrics = [m for m in ("profit", "operating_cost", "revenue", "margin", "cash_shortage",
                           "stockout_after_arrival", "lost_units", "fill_rate", "cash_outlay",
                           "peak_inventory") if m in base.metrics]
    rows = []
    for o in run.outcomes:
        row = {"action": o.action.action_id}
        for m in metrics:
            s = o.metrics[m]
            row[m] = _fmt(s.mean, s.unit)
        d = o.deltas["profit"]
        row["d_profit [5..95]"] = f"{d.mean:+,.0f} [{d.p05:+,.0f}..{d.p95:+,.0f}]"
        row["P(profit>none)"] = f"{o.prob_better.get('profit', 0):.0%}"
        row["feasible"] = "yes" if o.feasible else "NO"
        rows.append(row)
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(pd.DataFrame(rows).to_string(index=False))
    print(f"\n6. Ranking for {run.objective.value} ({spec.metric}, {spec.unit})")
    for r in rec.ranking:
        tag = f"#{r.rank}" if r.rank else "excluded"
        extra = "" if r.feasible else f"  <- {'; '.join(r.violations)}"
        print(f"   {tag:9s} {r.action_id:24s} {_fmt(r.objective_mean, spec.unit):>10}  "
              f"delta {fmt_num(r.delta_mean)} [{fmt_num(r.delta_p05)}..{fmt_num(r.delta_p95)}]  "
              f"P(better)={r.prob_better_than_no_action:.0%}{extra}")
    print("\n   Constraints applied:")
    for c in rec.evidence.get("constraints", []):
        print(f"     {c['name']}: {c['kind']} {fmt_num(c['limit'])} {c['unit']} on {c['target']} "
              f"({c['source']})")
    print("\n7. Uncertainty and assumptions")
    for a in rec.evidence["assumptions"]:
        rng = f" range [{a['low']}, {a['high']}] {a['distribution']}" if "low" in a else ""
        print(f"   {a['name']} = {a['value']:.4g} {a['unit']} ({a['source']}){rng}")
    for s in rec.sensitivity:
        print(f"   sensitivity: {s['assumption']} at {s['level']} ({s['value']:.4g}) -> top "
              f"{s['top_action']} ({_fmt(s['top_mean'], spec.unit)})")
    if run.stress:
        print(f"   stress test [{run.stress['category']}]: {run.stress['description']}")
        print(f"      -> recommended under stress: {run.stress['recommended']} "
              f"({run.stress['strength']}); baseline profit {run.stress['baseline_profit_mean']:,.0f}")
    print("\n8. Recommendation and explanation")
    print(f"   RECOMMENDED: {rec.recommended_action_id or 'none (no defensible recommendation)'} "
          f"| strength: {rec.strength} | effect type: {rec.effect_type}")
    print(f"   {rec.rationale}")
    for lim in rec.limitations:
        print(f"   - limitation: {lim}")
    print("   Other objectives (same simulations):")
    for name, o in run.other_objectives.items():
        print(f"     {name:24s} -> {o['recommended'] or 'none'} ({o['strength']})")
    print(f"   Human approval required before any change: {rec.requires_human_approval}. "
          "Nothing was executed.")
    print("\n9. Evidence provenance")
    ev = rec.evidence
    bd = ev["business_data"]
    print(f"   business data: {bd['dataset']} [{bd['label']}] {bd['rows']} rows "
          f"{bd['first_date']}..{bd['origin_date']}")
    for t, f in ev["forecasts"].items():
        print(f"   forecast {t}: {f['selected_model']} (validation {f['validation_period'][0]}.."
              f"{f['validation_period'][1]}, test {f['test_period'][0]}..{f['test_period'][1]})")
    for note in ev["feature_notes"]:
        print(f"   features: {note}")
    for e in ev["economic_context"]:
        print(f"   [observed_economic_data] {e['series_id']} {e['observed_at'][:7]} = "
              f"{e['value']:,.1f} {e['units']} (12-month change "
              f"{'n/a' if e['yoy_pct'] is None else format(e['yoy_pct'], '+')} {e['yoy_unit']}"
              f"{'' if not e.get('yoy_period') else ' for ' + e['yoy_period']}), published "
              f"{e['published_at']}, as of {e['as_of']}, retrieved {e['retrieved_at_utc']}")
    for note in run.economic_notes:
        print(f"   economic context note: {note}")
    print("   [historical_relationship] none claimed: FRED features failed validation.")
    print(f"   simulation: {ev['simulation']['n']} paths, seed {ev['simulation']['seed']}; "
          f"{ev['simulation']['method']}")


def _jsonable(run: DecisionRun) -> dict:
    if run.unsupported:
        return {"scenario": run.scenario, "unsupported": asdict(run.unsupported)}
    rec = asdict(run.recommendation)
    return {"scenario": run.scenario, "objective": run.objective.value, "recommendation": rec,
            "other_objectives": run.other_objectives, "stress": run.stress,
            "outcomes": [{"action": o.action.action_id, "feasible": o.feasible,
                          "violations": o.violations,
                          "metrics": {k: asdict(v) for k, v in o.metrics.items()},
                          "deltas": {k: asdict(v) for k, v in o.deltas.items()},
                          "prob_better": o.prob_better} for o in run.outcomes]}


def _config(args):
    """Default demo constraints, overridden by any constraint the user declares."""
    from dataclasses import replace

    from .decision.restaurant import RestaurantDecisionConfig
    from .decision.retail import RetailDecisionConfig
    from .decision.types import Constraint

    if args.scenario == "restaurant":
        cfg = RestaurantDecisionConfig()
        overrides = {"param:price_increase": args.max_price_increase}
    else:
        cfg = RetailDecisionConfig()
        overrides = {"metric:cash_outlay:mean": args.budget,
                     "metric:peak_inventory:p95": args.storage,
                     "metric:stockout_after_arrival:mean": args.max_stockout}
    constraints = tuple(
        Constraint(c.name, c.target, c.kind, overrides[c.target], c.unit, "user_declared")
        if overrides.get(c.target) is not None else
        Constraint(c.name, c.target, c.kind, c.limit, c.unit, "default_needs_confirmation")
        for c in cfg.constraints)
    return replace(cfg, constraints=constraints)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank candidate actions against no action")
    parser.add_argument("--scenario", choices=["restaurant", "retail"], required=True)
    parser.add_argument("--csv", help="Canonical daily CSV (uploaded user data)")
    parser.add_argument("--objective", choices=[o.value for o in Objective], default=None)
    parser.add_argument("--starting-cash", type=float, default=None,
                        help="User-confirmed starting cash balance (needed for cash-shortage risk)")
    parser.add_argument("--max-price-increase", type=float, default=None,
                        help="restaurant: largest allowed menu price increase (fraction)")
    parser.add_argument("--budget", type=float, default=None,
                        help="retail: 28-day purchase budget in USD")
    parser.add_argument("--storage", type=float, default=None,
                        help="retail: storage capacity in units (95th percentile peak)")
    parser.add_argument("--max-stockout", type=float, default=None,
                        help="retail: max probability of any stockout after the first delivery")
    parser.add_argument("--models", nargs="*", choices=list(REGISTRY), default=None)
    parser.add_argument("--sims", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=20261114)
    parser.add_argument("--out", default="artifacts/decisions")
    args = parser.parse_args()

    if args.csv:
        history, _ = ingest_daily_csv(args.csv, spec=DatasetSpec())
        dataset, label = Path(args.csv).stem, "UPLOADED USER DATA"
    else:
        history, _ = validate_daily_frame(make_sample(args.scenario), source="synthetic")
        dataset, label = f"{args.scenario}_SYNTHETIC", "SYNTHETIC"
    cash = None if args.starting_cash is None else StartingCash(
        args.starting_cash, "declared by user on the command line", True)
    ctx = OrganizationContext(f"demo {args.scenario}", args.scenario, label, starting_cash=cash)
    objective = Objective(args.objective) if args.objective else DEFAULT_OBJECTIVE[args.scenario]
    runner = run_restaurant if args.scenario == "restaurant" else run_retail
    run = runner(history, ctx, objective, dataset=dataset, cfg=_config(args),
                 models=tuple(args.models) if args.models else None, n_sims=args.sims,
                 seed=args.seed)
    print_run(run)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"decision_{dataset}_{objective.value}.json"
    path.write_text(json.dumps(_jsonable(run), indent=2, default=str))
    print(f"\nsaved {path}")


def cli() -> None:
    try:
        main()
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")


if __name__ == "__main__":
    cli()
