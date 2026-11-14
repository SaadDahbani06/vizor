"""Feature ablation: internal-only models vs internal + point-in-time FRED features.

Both arms use the SAME folds, untouched final test, models and selection rule as the Milestone 1
leaderboard. The decision to adopt FRED features is made on validation only; the final test is
then reported for both arms as an untouched check.

  python -m decisiondesk.ablation --industry restaurant --target revenue
  python -m decisiondesk.ablation --industry retail --target units_sold --offline   # cached FRED

DATA ORIGINS: FRED/ALFRED values are REAL published economic observations. The bundled business
histories are SYNTHETIC and were generated with no dependence on the economy, so on them FRED
features cannot genuinely help: this run measures the pipeline and the cost of extra features,
not the value of macro data for a real business.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from .connectors.base import ConnectorError
from .connectors.fred import FRED_NOTICE, FREDConnector
from .leaderboard import TARGETS, EvaluationConfig, Leaderboard, build_leaderboard
from .macro import DEFAULT_SERIES, MacroSpec, origin_exog
from .models import REGISTRY


def compare(internal: Leaderboard, macro: Leaderboard, min_rel: float) -> tuple[pd.DataFrame, dict]:
    rows = []
    for a, b in zip(internal.models, macro.models):
        if not a.available:
            continue
        row = {"model": a.name, "kind": a.kind,
               "val_wape_internal": a.validation["wape"], "val_wape_fred": b.validation["wape"],
               "test_wape_internal": a.holdout["wape"], "test_wape_fred": b.holdout["wape"],
               "test_cov_internal": a.holdout_interval_coverage,
               "test_cov_fred": b.holdout_interval_coverage}
        for split in ("val", "test"):
            base, new = row[f"{split}_wape_internal"], row[f"{split}_wape_fred"]
            row[f"{split}_change_pct"] = 100 * (new / base - 1) if base else None
        rows.append(row)
    table = pd.DataFrame(rows)

    sel_int = next(m for m in internal.models if m.name == internal.selected)
    sel_mac = next(m for m in macro.models if m.name == macro.selected)
    gain = 1 - sel_mac.validation["wape"] / sel_int.validation["wape"]
    adopt = macro.selected not in ("seasonal_naive", "weekday_mean_12w") and gain >= min_rel
    decision = {
        "internal_selected": internal.selected, "fred_arm_selected": macro.selected,
        "validation_gain_of_fred_arm": gain, "required_gain": min_rel,
        "adopt_fred_features": bool(adopt),
        "reason": (f"Both arms select {internal.selected}, a baseline that ignores external "
                   "features, so FRED features cannot change the forecast; keep internal-only"
                   if internal.selected == macro.selected and REGISTRY[internal.selected].kind
                   == "baseline" else
                   f"FRED arm lowers validation WAPE by {gain:.1%} (>= {min_rel:.0%})" if adopt
                   else f"FRED arm validation WAPE is {abs(gain):.1%} "
                        f"{'lower' if gain > 0 else 'higher'} than internal-only; needs >= "
                        f"{min_rel:.0%} lower, so keep internal-only features"),
        "test_wape_internal_selected": sel_int.holdout["wape"],
        "test_wape_fred_selected": sel_mac.holdout["wape"],
    }
    return table, decision


def run(history: pd.DataFrame, target: str, specs: tuple[MacroSpec, ...], vintages: dict,
        provenance: dict, *, dataset: str, data_label: str, cfg: EvaluationConfig,
        models: tuple[str, ...] | None) -> dict:
    exog = origin_exog(history["date"], vintages, specs)
    internal = build_leaderboard(history, target, dataset=dataset, data_label=data_label,
                                 cfg=cfg, models=models)
    macro = build_leaderboard(history, target, dataset=dataset, data_label=data_label, cfg=cfg,
                              models=models, exog=exog, feature_set="fred_pit")
    table, decision = compare(internal, macro, cfg.min_relative_improvement)
    return {"internal": internal, "macro": macro, "table": table, "decision": decision,
            "exog_columns": list(exog.columns), "provenance": provenance,
            "exog_preview": exog.iloc[[len(exog) // 2, len(exog) - 1]]}


def main() -> None:
    from dotenv import load_dotenv

    from .data import DatasetSpec, ingest_daily_csv, validate_daily_frame
    from .synthetic import make_sample

    parser = argparse.ArgumentParser(description="Internal vs internal+FRED (point-in-time)")
    parser.add_argument("--industry", choices=list(DEFAULT_SERIES), default="restaurant")
    parser.add_argument("--csv", help="Canonical daily CSV (uploaded user data)")
    parser.add_argument("--target", choices=TARGETS, default="revenue")
    parser.add_argument("--models", nargs="*", choices=list(REGISTRY), default=None)
    parser.add_argument("--offline", action="store_true", help="Use cached FRED payloads only")
    parser.add_argument("--observation-start", default="2021-01-01")
    parser.add_argument("--out", default="artifacts/ablation")
    args = parser.parse_args()
    load_dotenv(".env")

    if args.csv:
        history, _ = ingest_daily_csv(args.csv, spec=DatasetSpec())
        dataset, label = Path(args.csv).stem, "UPLOADED USER DATA"
    else:
        history, _ = validate_daily_frame(make_sample(args.industry), source="synthetic")
        dataset, label = f"{args.industry}_SYNTHETIC", "SYNTHETIC"
    specs = DEFAULT_SERIES[args.industry]
    connector = FREDConnector(offline=args.offline)
    vintages, provenance = {}, {}
    for spec in specs:
        frame, prov = connector.fetch_vintages(spec.series_id, args.observation_start)
        vintages[spec.series_id] = frame
        n_revised = int((frame.groupby("observed_at").size() > 1).sum())
        provenance[spec.series_id] = {**asdict(prov), "why": spec.why,
                                      "vintage_rows": len(frame),
                                      "observations_with_revisions": n_revised}
        if n_revised == 0:
            print(f"WARNING: ALFRED shows no revisions for {spec.series_id}; vintage history "
                  "may be incomplete, treat its point-in-time values with caution.")

    result = run(history, args.target, specs, vintages, provenance, dataset=dataset,
                 data_label=label, cfg=EvaluationConfig(), models=tuple(args.models or ()) or None)
    m = result["internal"].meta
    print(f"\nABLATION target={args.target} business data={label} | FRED = REAL point-in-time "
          f"vintages ({', '.join(s.series_id for s in specs)})")
    print(f"validation {m['validation_period'][0]}..{m['validation_period'][1]} "
          f"({m['n_validation_folds']} folds) | untouched test {m['holdout_period'][0]}.."
          f"{m['holdout_period'][1]} ({m['n_holdout_folds']} folds)")
    print("exogenous columns:", result["exog_columns"])
    with pd.option_context("display.width", 220, "display.max_columns", 20,
                           "display.float_format", "{:,.4f}".format):
        print(result["table"].to_string(index=False))
    print("DECISION (validation only):", json.dumps(result["decision"], default=float, indent=1))
    if label == "SYNTHETIC":
        print("CAVEAT: synthetic business data has no economic dependence by construction; "
              "no improvement should be expected and none can be claimed for real businesses.")
    print(FRED_NOTICE)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"ablation_{dataset}_{args.target}.json"
    path.write_text(json.dumps({
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_origins": {"business": label, "fred": "REAL published observations (ALFRED vintages)"},
        "target": args.target, "decision": result["decision"],
        "comparison": result["table"].to_dict(orient="records"),
        "exogenous_columns": result["exog_columns"], "provenance": provenance,
        "internal_leaderboard": result["internal"].to_dict(),
        "fred_leaderboard": result["macro"].to_dict()}, indent=2, default=str))
    print(f"saved {path}")


def cli() -> None:
    try:
        main()
    except ConnectorError as exc:
        raise SystemExit(f"error: {exc}. Use --offline to reuse cached FRED payloads.")
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")


if __name__ == "__main__":
    cli()
