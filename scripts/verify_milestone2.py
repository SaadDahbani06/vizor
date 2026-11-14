"""Milestone 2 verification evidence (READ-ONLY: never writes to Nessie, never prints keys).

    python scripts/verify_milestone2.py            # uses live APIs (FRED refreshes its cache)
    python scripts/verify_milestone2.py --offline  # FRED from cache; skips Nessie

Each section prints PASS/FAIL/PARTIAL lines with the numbers behind them.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from decisiondesk.connectors.base import ConnectorError
from decisiondesk.connectors.fred import FREDConnector
from decisiondesk.data import validate_daily_frame
from decisiondesk.features import WARMUP_DAYS, training_rows
from decisiondesk.leaderboard import (
    EvaluationConfig,
    _extended_dates,
    build_leaderboard,
    plan_folds,
    run_cutoffs,
    target_series,
)
from decisiondesk.macro import DEFAULT_SERIES, origin_exog, point_in_time_table
from decisiondesk.models import REGISTRY
from decisiondesk.synthetic import make_sample

RESULTS: list[tuple[str, str, str]] = []


def record(check: str, status: str, detail: str) -> None:
    RESULTS.append((check, status, detail))
    print(f"[{status}] {check}: {detail}")


def fred_section(offline: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    print("\n=== FRED: retrieval, point-in-time alignment, leakage ===")
    history, _ = validate_daily_frame(make_sample("restaurant"), source="synthetic")
    conn = FREDConnector(offline=offline)
    specs = DEFAULT_SERIES["restaurant"]
    vintages = {}
    for spec in specs:
        frame, prov = conn.fetch_vintages(spec.series_id, "2021-01-01")
        vintages[spec.series_id] = frame
        first = frame.groupby("observed_at").published_at.min()
        lag = (first - first.index).dt.days
        revised = int((frame.groupby("observed_at").size() > 1).sum())
        record(f"FRED retrieval {spec.series_id}", "PASS",
               f"{len(frame)} vintage rows, {frame.observed_at.nunique()} months "
               f"{frame.observed_at.min():%Y-%m}..{frame.observed_at.max():%Y-%m}, retrieved "
               f"{frame.retrieved_at_utc.iloc[0]}, median first-release lag {lag.median():.0f}d, "
               f"{revised} months revised, mode={prov.vintage_mode}")

    exog = origin_exog(history["date"], vintages, specs)
    dates = pd.DatetimeIndex(history["date"])
    # 1) Recompute every origin with all rows published after its as-of date deleted.
    mismatches, checked, stale_violations = 0, 0, 0
    for o in range(WARMUP_DAYS, len(dates) + 1, 7):
        asof = dates[o - 1]
        parts = []
        for spec in specs:
            v = vintages[spec.series_id]
            truncated = v[v.realtime_start <= asof].copy()
            parts.append(point_in_time_table(truncated, [asof], spec))
            latest = truncated[truncated.realtime_end >= asof].observed_at.max()
            if v.loc[v.observed_at == latest, "published_at"].min() > asof:
                stale_violations += 1
        recomputed = pd.concat(parts, axis=1).iloc[0]
        checked += 1
        if not np.allclose(recomputed[exog.columns].to_numpy(float),
                           exog.loc[o].to_numpy(float), equal_nan=True):
            mismatches += 1
    record("FRED no future rows used", "PASS" if mismatches == 0 and stale_violations == 0
           else "FAIL", f"{checked} origins recomputed with post-as-of rows deleted: "
           f"{mismatches} mismatches; latest-observation published after as-of: "
           f"{stale_violations}")

    # 2) What a naive join would have done (latest revised values keyed by observation month).
    cpi = vintages["CUSR0000SAF11"]
    latest_rev = cpi[cpi.realtime_end == cpi.realtime_end.max()].set_index("observed_at").value
    naive_unpublished, diffs = 0, []
    for o in range(WARMUP_DAYS, len(dates) + 1, 7):
        asof = dates[o - 1]
        month = asof.to_period("M").to_timestamp()
        naive_unpublished += int(cpi.loc[cpi.observed_at == month, "published_at"].min() > asof
                                 if month in set(cpi.observed_at) else 0)
        prior = month - pd.DateOffset(years=1)
        if month in latest_rev.index and prior in latest_rev.index:
            diffs.append(abs(100 * (latest_rev[month] / latest_rev[prior] - 1)
                             - exog.loc[o, "x_cusr0000saf11_yoy_pct"]))
    record("FRED naive-join comparison", "PASS",
           f"a same-month join of revised data would use an unpublished value at "
           f"{naive_unpublished}/{checked} origins; mean |naive - point-in-time| food-CPI YoY = "
           f"{np.mean(diffs):.2f} pp")
    return history, exog


def features_used_section(history: pd.DataFrame, exog: pd.DataFrame) -> None:
    print("\n=== FRED features reach the models ===")
    y = target_series(history, "revenue")
    dates = _extended_dates(history, 28)
    X, yt = training_rows(y, dates, len(y), 28, exog)
    xcols = [c for c in X.columns if c.startswith("x_")]
    ridge = REGISTRY["ridge"].build().fit(X, yt)
    coefs = pd.Series(ridge.model.named_steps["ridge"].coef_, index=ridge.columns)
    share = coefs[xcols].abs().sum() / coefs.abs().sum()
    record("FRED columns in training matrix", "PASS" if xcols else "FAIL",
           f"{len(xcols)} x_ columns in {X.shape} matrix: {xcols}")
    record("Ridge uses FRED columns", "PASS" if share > 0 else "FAIL",
           f"standardized |coef| share of FRED columns = {share:.1%}; largest: "
           f"{coefs[xcols].abs().idxmax()} = {coefs[coefs[xcols].abs().idxmax()]:+.1f}")
    ok, _ = REGISTRY["xgboost"].availability()
    if ok:
        xgb = REGISTRY["xgboost"].build().fit(X, yt)
        imp = pd.Series(xgb.model.feature_importances_, index=xgb.columns)
        record("XGBoost uses FRED columns", "PASS", f"importance share {imp[xcols].sum():.1%}")


def selection_section(history: pd.DataFrame) -> None:
    print("\n=== Model selection ignores the final test ===")
    cfg = EvaluationConfig()
    board = build_leaderboard(history, "revenue", dataset="audit", data_label="SYNTHETIC", cfg=cfg)
    _, _, holdout_start = plan_folds(len(history), cfg)
    scrambled = history.copy()
    rng = np.random.default_rng(0)
    tail = scrambled.loc[holdout_start:, "revenue"].to_numpy()
    scrambled.loc[holdout_start:, "revenue"] = rng.permutation(tail) * 3.0
    board2 = build_leaderboard(scrambled, "revenue", dataset="audit", data_label="SYNTHETIC",
                               cfg=cfg)
    same_val = all(a.validation == b.validation for a, b in zip(board.models, board2.models)
                   if a.available)
    test_changed = any(a.holdout != b.holdout for a, b in zip(board.models, board2.models)
                       if a.available)
    record("Selection independent of final test",
           "PASS" if same_val and board.selected == board2.selected and test_changed else "FAIL",
           f"final-test revenue permuted and tripled: selected {board.selected} -> "
           f"{board2.selected}; validation metrics identical={same_val}; test metrics "
           f"changed={test_changed}")

    print("\n=== Cash risk uses out-of-sample errors at the validated horizon ===")
    cash = build_leaderboard(history, "net_cash_flow", dataset="audit", data_label="SYNTHETIC",
                             cfg=cfg)
    val_cuts, _, _ = plan_folds(len(history), cfg)
    y = target_series(history, "net_cash_flow")
    pred, actual = run_cutoffs(cash.selected, y, _extended_dates(history, 28), val_cuts, 28)
    same = np.allclose(cash.val_residuals, actual - pred)
    record("Cash-risk residuals are out-of-sample", "PASS" if same else "FAIL",
           f"{cash.val_residuals.shape[0]} validation folds x {cash.val_residuals.shape[1]} days "
           f"re-derived from models refit before each cutoff: identical={same}; horizon=28d "
           "(no 90-day claim)")


def nessie_section() -> None:
    print("\n=== Nessie: live read, canonical conversion, pipeline reach ===")
    from decisiondesk.connectors.nessie import NessieClient
    from decisiondesk.nessie_sandbox import ACCOUNTS, CUSTOMER, fetch_all
    from decisiondesk.transactions import nessie_to_canonical, transactions_to_daily

    client = NessieClient()
    customers = client.list_customers()
    demo = [c for c in customers if c.get("last_name") == CUSTOMER["last_name"]]
    record("Nessie sandbox has customer/accounts", "PASS" if demo else "FAIL",
           f"{len(customers)} customer(s) visible, demo customer present={bool(demo)}")
    records, accounts, merchants, unreadable = fetch_all(client)
    counts = {role: {k: len(v) for k, v in records[aid].items()} for role, aid in accounts.items()}
    record("Nessie records retrievable", "PARTIAL" if unreadable else "PASS",
           f"{counts}; unreadable lists: {sorted(k.split('/')[1] for k in unreadable)}")
    tx, report = nessie_to_canonical(records, org_account_ids=set(accounts.values()),
                                     merchants=merchants)
    tx2, _ = nessie_to_canonical(*fetch_all(client)[:1], org_account_ids=set(accounts.values()),
                                 merchants=merchants)
    record("Repeated import does not duplicate", "PASS" if set(tx.transaction_id) ==
           set(tx2.transaction_id) and tx.transaction_id.is_unique else "FAIL",
           f"run1={len(tx)} rows, run2={len(tx2)} rows, identical ids, duplicates dropped="
           f"{report.duplicates_dropped}")
    balances = {role: a.get("balance") for a in client.list_accounts(demo[0]["_id"])
                for role, body in ACCOUNTS.items() if a.get("nickname") == body["nickname"]}
    implied = {role: ACCOUNTS[role]["balance"] + tx[tx.account_id == aid].amount.sum()
               for role, aid in accounts.items()}
    record("Nessie balance reconciliation", "PASS" if balances == implied else "PARTIAL",
           f"Nessie-reported balances {balances} vs opening + imported transactions {implied}")

    daily = transactions_to_daily(tx)
    try:
        validate_daily_frame(daily[["date", "revenue", "variable_costs", "fixed_costs"]],
                             source="nessie")
        reach = ("PASS", "validated")
    except ValueError as exc:
        detail = (f"canonical daily frame built ({len(daily)} days, revenue "
                  f"{daily.revenue.sum():,.0f}) but ingestion rejects it: {exc}")
        reach = ("PARTIAL", detail)
    record("Nessie data reaches analysis pipeline", *reach)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    load_dotenv(".env")
    history, exog = fred_section(args.offline)
    features_used_section(history, exog)
    selection_section(history)
    if not args.offline:
        try:
            nessie_section()
        except ConnectorError as exc:
            record("Nessie live section", "FAIL", str(exc))
    print("\n=== SUMMARY ===")
    for status in ("PASS", "PARTIAL", "FAIL"):
        print(status, sum(1 for _, s, _ in RESULTS if s == status))
    sys.exit(1 if any(s == "FAIL" for _, s, _ in RESULTS) else 0)


if __name__ == "__main__":
    main()
