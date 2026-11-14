"""Fully offline vertical slice; no external API or agent is claimed to be active."""
from __future__ import annotations

import argparse
from pathlib import Path

from .data import DatasetSpec, ingest_daily_csv, validate_daily_frame
from .decision.runner import run_restaurant, run_retail
from .decision.types import OBJECTIVES, Objective, OrganizationContext, StartingCash, fmt_num
from .leaderboard import EvaluationConfig, build_leaderboard, forecast_selected, print_leaderboard
from .models import REGISTRY
from .risk import backtested_cash_risk
from .synthetic import make_sample


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--industry", choices=["restaurant", "retail"], default="restaurant")
    parser.add_argument("--csv", help="Optional CSV with canonical daily columns")
    parser.add_argument("--currency", default=None, help="ISO currency of the CSV, e.g. USD")
    parser.add_argument("--missing-days", choices=["error", "closed_zero"], default="error")
    parser.add_argument("--starting-cash", type=float, default=None)
    parser.add_argument("--reserve", type=float, default=0.0)
    parser.add_argument("--horizon", type=int, default=28,
                        help="Forecast/validation horizon in days; risk is only reported this far")
    parser.add_argument("--models", nargs="*", choices=list(REGISTRY), default=None,
                        help="Subset of models (baselines always run)")
    parser.add_argument("--out", default="artifacts/leaderboards")
    args = parser.parse_args()

    if args.csv:
        if args.starting_cash is None:
            parser.error("--starting-cash is required for real uploaded CSVs")
        spec = DatasetSpec(currency=args.currency or "USD",
                           currency_declared_by_user=args.currency is not None)
        history, report = ingest_daily_csv(args.csv, spec=spec, missing_days=args.missing_days)
        dataset, label = Path(args.csv).stem, "UPLOADED USER DATA"
        origin = "UPLOADED USER DATA (not independently verified)"
    else:
        history, report = validate_daily_frame(
            make_sample(args.industry), spec=DatasetSpec(business_type=args.industry),
            source=f"synthetic:{args.industry}")
        dataset, label = f"{args.industry}_SYNTHETIC", "SYNTHETIC"
        origin = "SYNTHETIC DEMO DATA — not real company data"
    starting_cash = args.starting_cash if args.starting_cash is not None else 15_000.0
    cfg = EvaluationConfig(horizon=args.horizon, step=args.horizon)
    models = tuple(args.models) if args.models else None

    print(f"DATA: {origin}\nStarting cash (provided/demo): ${starting_cash:,.0f}")
    print("\n== 1. Ingestion audit ==")
    print(report.summary())

    print("\n== 2. Model leaderboards (rolling-origin validation + untouched test) ==")
    targets = ["revenue"] + (["units_sold"] if "units_sold" in history else [])
    boards = {}
    for target in targets:
        board = build_leaderboard(history, target, dataset=dataset, data_label=label, cfg=cfg,
                                  models=models)
        print_leaderboard(board)
        board.save(args.out)
        boards[target] = board

    print(f"\n== 3. {args.horizon}-day cash risk from backtested net-cash forecast errors ==")
    cash_board = build_leaderboard(history, "net_cash_flow", dataset=dataset, data_label=label,
                                   cfg=cfg, models=models)
    cash_board.save(args.out)
    fc = forecast_selected(history, "net_cash_flow", cash_board)
    print(f"net-cash model selected on validation: {cash_board.selected} "
          f"(test WAPE {next(m.holdout['wape'] for m in cash_board.models if m.name == cash_board.selected):.3f})")
    risk = backtested_cash_risk(
        fc["net_cash_flow"].to_numpy(), cash_board.val_residuals,
        cash_board.holdout_actual - cash_board.holdout_pred, cash_board.holdout_pred,
        cash_board.holdout_actual, starting_cash=starting_cash, reserve=args.reserve)
    for key, value in risk.items():
        print(f"  {key}: {value}")

    print(f"\n== 4. Decision engine on the SELECTED forecasts ({args.horizon} days; modeled "
          "assumptions, NOT causal effects) ==")
    cash = (StartingCash(args.starting_cash, "declared by user on the command line", True)
            if args.starting_cash is not None else None)
    ctx = OrganizationContext(dataset, args.industry, label, starting_cash=cash)
    runner = run_restaurant if args.industry == "restaurant" else run_retail
    run = runner(history, ctx, Objective.MAX_EXPECTED_PROFIT, dataset=dataset, eval_cfg=cfg,
                 models=models, boards=boards)
    if run.unsupported:
        print(f"UNSUPPORTED: {run.unsupported.reason} Missing: {run.unsupported.missing}")
    else:
        rec = run.recommendation
        unit = OBJECTIVES[rec.objective].unit
        for r in rec.ranking:
            status = f"#{r.rank}" if r.rank else "excluded"
            why = f"  <- {'; '.join(r.violations)}" if r.violations else ""
            print(f"  {status:9s} {r.action_id:24s} expected {rec.metric} "
                  f"{fmt_num(r.objective_mean):>8} {unit}, vs no action "
                  f"{fmt_num(r.delta_mean):>8}{why}")
        print(f"  RECOMMENDED: {rec.recommended_action_id or 'none'} ({rec.strength}). "
              f"{rec.rationale}")
        print("  Full report: python -m decisiondesk.decide --scenario " + args.industry)
    print(f"\nLeaderboards saved under {args.out}/ (gitignored).")


def cli() -> None:
    """Entry point: user-facing data/config problems print a message, not a traceback."""
    try:
        main()
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")


if __name__ == "__main__":
    cli()
