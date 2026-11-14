"""Real U.S. data forecasting validation (Milestone 3.5).

  python -m decisiondesk.us_validation kwick365            # live API (cached), REAL AGGREGATE
  python -m decisiondesk.us_validation kwick365 --offline  # cached payloads only
  python -m decisiondesk.us_validation iowa --scan         # list candidate stores in the export
  python -m decisiondesk.us_validation iowa --store <no>   # REAL INDIVIDUAL STORE PURCHASES

Each result carries an accuracy CATEGORY so results are never pooled:
  REAL_AGGREGATE_INDUSTRY | REAL_INDIVIDUAL_BUSINESS_PURCHASES | SYNTHETIC
The Milestone 1 leaderboard (chronological folds, untouched test, validation-only selection) and
the Milestone 2 FRED ablation (point-in-time vintages, identical windows) are reused unchanged.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from .ablation import compare
from .connectors.base import ConnectorError
from .connectors.fred import FREDConnector
from .connectors.iowa_liquor import export_files, scan_stores, store_daily
from .connectors.kwick365 import Kwick365Connector, contiguous_daily
from .leaderboard import EvaluationConfig, build_leaderboard, print_leaderboard
from .macro import DEFAULT_SERIES, origin_exog

OUT = Path("artifacts/us_validation")
# Kwick365's API returns 366 days, so the standard 28-day/6-fold design (504 days) cannot run.
SHORT_CFG = EvaluationConfig(horizon=14, step=14, holdout_folds=4, min_train=168, min_val_folds=4)


def _fred(specs, offline: bool) -> dict[str, pd.DataFrame]:
    conn = FREDConnector(offline=offline)
    return {s.series_id: conn.fetch_vintages(s.series_id, "2021-01-01")[0] for s in specs}


def run_series_experiment(history: pd.DataFrame, target: str, *, dataset: str, label: str,
                          category: str, cfg: EvaluationConfig, fred_specs, fred_offline: bool,
                          provenance: dict) -> dict:
    internal = build_leaderboard(history, target, dataset=dataset, data_label=label, cfg=cfg)
    print_leaderboard(internal)
    result = {"category": category, "dataset": dataset, "target": target,
              "provenance": provenance, "config": asdict(cfg), "internal": internal.to_dict()}
    try:
        vintages = _fred(fred_specs, fred_offline)
        exog = origin_exog(history["date"], vintages, fred_specs)
        macro = build_leaderboard(history, target, dataset=dataset, data_label=label, cfg=cfg,
                                  exog=exog, feature_set="fred_pit")
        table, decision = compare(internal, macro, cfg.min_relative_improvement)
        with pd.option_context("display.width", 220, "display.max_columns", 20,
                               "display.float_format", "{:,.4f}".format):
            print("\nWith vs without point-in-time FRED features (identical windows):")
            print(table.to_string(index=False))
        print("DECISION:", decision["reason"])
        result.update(fred_series=[s.series_id for s in fred_specs], fred_leaderboard=macro.to_dict(),
                      ablation=table.to_dict(orient="records"), ablation_decision=decision)
    except (ConnectorError, ValueError) as exc:
        print(f"FRED ablation skipped: {exc}")
        result["ablation_skipped"] = str(exc)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{dataset}_{target}.json"
    path.write_text(json.dumps({"generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                                **result}, indent=2, default=str))
    print(f"saved {path}")
    return result


def kwick365(readings: tuple[str, ...], geo: str, offline: bool) -> list[dict]:
    conn = Kwick365Connector(offline=offline)
    results = []
    for reading in readings:
        frame, prov = conn.fetch(reading, geo)
        seg, rep = contiguous_daily(frame)
        target = reading.replace("-", "_")
        history = seg.rename(columns={"value": target})[["date", target]]
        print(f"\n######## Kwick365 {reading} ({geo}) — REAL AGGREGATE of independent restaurants "
              f"running KwickPOS; {prov.units}")
        print(f"published {rep['published_days']} days; dropped {rep['unstable_trailing_days_dropped']} "
              f"unstable trailing days; withheld {rep['withheld_days_in_window'] or 'none'}; "
              f"evaluated segment {rep['segment'][0]}..{rep['segment'][1]} ({rep['segment_days']} days); "
              f"median sample {int(seg['n_stores'].median())} restaurants/day")
        prov_d = {**asdict(prov), "segment": rep,
                  "citation": "Kwick365 — The Independent Restaurant Index, https://kwick365.com",
                  "sample_per_day_median_restaurants": int(seg["n_stores"].median()),
                  "sample_per_day_median_orders": int(seg["n"].median())}
        results.append(run_series_experiment(
            history, target, dataset=f"kwick365_{geo}", category="REAL_AGGREGATE_INDUSTRY",
            label="REAL AGGREGATE (Kwick365; KwickPOS independent restaurants)", cfg=SHORT_CFG,
            fred_specs=DEFAULT_SERIES["restaurant"], fred_offline=True, provenance=prov_d))
    return results


def iowa(store: str | None, scan: bool, data_dir: str, force: bool) -> dict | None:
    files = export_files(data_dir)
    if not files:
        raise SystemExit(
            f"No Iowa Liquor Sales export found in {data_dir}/.\n"
            "Download it from https://data.iowa.gov/catalog/dataset/1051 (Download -> Rows -> "
            "Download as CSV; the export may come as several files) and place the CSV files in "
            f"{data_dir}/ (gitignored). No query API exists for this dataset.")
    if scan:
        summary = scan_stores(files)
        cand = summary[(summary.span_days >= 3 * 365) & (summary.invoice_day_share >= 0.1)]
        print(cand.head(25).to_string())
        return None
    if not store:
        raise SystemExit("pass --store <store_no> (use --scan to list candidates)")
    daily, report, prov = store_daily(files, store)
    print(json.dumps(report.to_dict(), indent=2))
    if not report.suitable and not force:
        raise SystemExit("Store is not suitable for evaluation (see reasons); choose another store "
                         "or pass --force to evaluate anyway with the reasons recorded.")
    return run_series_experiment(
        daily[["date", "purchase_dollars"]], "purchase_dollars", dataset=f"iowa_store_{store}",
        category="REAL_INDIVIDUAL_BUSINESS_PURCHASES",
        label="REAL INDIVIDUAL STORE PURCHASES (Iowa dataset 1051)", cfg=EvaluationConfig(),
        fred_specs=DEFAULT_SERIES["retail_iowa_liquor"], fred_offline=True,
        provenance={**asdict(prov), "store_report": report.to_dict()})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("kwick365")
    k.add_argument("--readings", nargs="*", default=["online-orders-per-store",
                                                     "instore-orders-per-store"])
    k.add_argument("--geo", default="us")
    k.add_argument("--offline", action="store_true")
    i = sub.add_parser("iowa")
    i.add_argument("--store")
    i.add_argument("--scan", action="store_true")
    i.add_argument("--data-dir", default="data/private/iowa_liquor")
    i.add_argument("--force", action="store_true")
    args = parser.parse_args()
    load_dotenv(".env")
    if args.cmd == "kwick365":
        kwick365(tuple(args.readings), args.geo, args.offline)
    else:
        iowa(args.store, args.scan, args.data_dir, args.force)


if __name__ == "__main__":
    try:
        main()
    except (ConnectorError, ValueError) as exc:
        raise SystemExit(f"error: {exc}")
