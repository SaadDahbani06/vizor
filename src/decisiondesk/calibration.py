"""Out-of-sample calibration of the Monte Carlo uncertainty method (Milestone 5.1).

What is evaluated is the method the app actually uses: draw historical out-of-sample forecast
error paths (``ForecastBundle.simulate`` / ``risk.simulate_cash_paths``), add them to a forecast,
and read 80% ranges off the simulated paths.

Chronological protocol (no evaluation window ever calibrates itself):
  * The leaderboard's error windows are stored in time order: validation windows, then the
    untouched final-test windows. They do not overlap (step = horizon = 28 days).
  * "expanding": window k is evaluated with an interval built ONLY from windows 0..k-1
    (at least ``MIN_PAST`` of them). This gives the most evaluations.
  * "final_test": the final-test windows are evaluated with intervals built from the validation
    windows only (fewer evaluations, but the classic split).
  * Everything is computed in error space: actual - forecast lies in [q_lo, q_hi] of the
    simulated errors <=> actual lies in [forecast + q_lo, forecast + q_hi]. (The simulator clips
    non-negative targets at zero; with these forecast levels clipping is negligible and is
    ignored here.)

Two kinds of coverage are reported and must not be confused:
  * daily: share of individual days inside their daily 80% range;
  * total: share of evaluated windows whose H-day TOTAL lies inside the 80% range of simulated
    H-day totals (H = 7, 14, 28). Totals drive the Scenario Lab, revenue-total and cash figures.

Sampling methods compared (block length, pool of past windows):
  * weekly_blocks         - 7-day blocks from independently drawn windows (Milestone 5 method);
  * biweekly_blocks       - 14-day blocks;
  * whole_window          - one past window supplies the whole path (Milestone 5.1 default);
  * whole_window_recent12 - whole windows from the 12 most recent past windows (evaluated, not adopted).
Why whole windows: within a 28-day window the weekly error sums are correlated at every lag
(revenue +0.32 / +0.27 / +0.37 at 1 / 2 / 3 weeks): a forecast is often off by a level for the
whole window. Any shorter block discards that and makes multi-week totals too narrow (the weekly
method covered 67-68% of 14- and 28-day totals in the validation period).
Known remaining bias: the earliest backtest windows (models trained on less history) have larger
errors, so whole windows from ALL past windows are conservative for 28-day totals (about 90% in
the validation period). Restricting to recent windows removes that but, with only ~12 windows,
reads quantiles from too few values (daily coverage fell to ~70%); pool-size results were not
stable, so no pool size was tuned. Details: docs/MILESTONE_5_1_CALIBRATION.md.
Small samples: with k past windows a whole-window total range is read from k values, and 20-30
evaluated windows give coverage estimates with a +/- 15-point uncertainty (Wilson 95% interval
reported). These are synthetic-data results; they say nothing about real-world calibration.

CLI:  python -m decisiondesk.calibration
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

LEVEL = 0.80
HORIZONS = (7, 14, 28)
MIN_PAST = 8
METHODS = {"weekly_blocks": (7, None), "biweekly_blocks": (14, None),
           "whole_window": (None, None), "whole_window_recent12": (None, 12)}
DEFAULT_METHOD = "whole_window"
N_SIMS = 4000
ASSESSABLE_WINDOWS = 20


def chronological_windows(board) -> tuple[np.ndarray, int]:
    """(windows x horizon) out-of-sample errors in time order, and the index where the final
    test starts. Errors are actual - forecast."""
    val = np.asarray(board.val_residuals, float)
    test = np.asarray(board.holdout_actual, float) - np.asarray(board.holdout_pred, float)
    return np.vstack([val, test]), val.shape[0]


def sample_errors(pool: np.ndarray, n: int, rng: np.random.Generator, block: int | None) -> np.ndarray:
    """Error paths (n x H) drawn from ``pool`` (windows x H). ``block=None`` = whole windows."""
    k, horizon = pool.shape
    if block is None or block >= horizon:
        return pool[rng.integers(0, k, size=n)]
    out = np.empty((n, horizon))
    for start in range(0, horizon, block):
        stop = min(start + block, horizon)
        out[:, start:stop] = pool[rng.integers(0, k, size=n), start:stop]
    return out


def wilson(hits: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = hits / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [float(c - h), float(c + h)]


def interval_score(y: float, lo: float, hi: float, alpha: float) -> float:
    """Proper scoring rule for central intervals (lower is better): width + miss penalties."""
    return (hi - lo) + 2 / alpha * max(0.0, lo - y) + 2 / alpha * max(0.0, y - hi)


def _evaluate_one(pool: np.ndarray, target: np.ndarray, block, rng, level: float, n: int) -> dict:
    alpha = 1 - level
    sims = sample_errors(pool, n, rng, block)
    qlo, qhi = np.quantile(sims, [alpha / 2, 1 - alpha / 2], axis=0)
    tot = sims.sum(1)
    tlo, thi = np.quantile(tot, [alpha / 2, 1 - alpha / 2])
    y = float(target.sum())
    return {"daily_hits": int(np.sum((target >= qlo) & (target <= qhi))), "days": int(target.size),
            "total_hit": bool(tlo <= y <= thi), "total_width": float(thi - tlo),
            "total_score": interval_score(y, float(tlo), float(thi), alpha), "total_error": y}


def past_pool(k: int, recent: int | None, first_test: int | None = None) -> list[int]:
    """Indices of the windows allowed to calibrate window k: strictly earlier windows (and, in
    the final-test scheme, validation windows only), optionally only the most recent ones."""
    end = k if first_test is None else min(k, first_test)
    start = 0 if recent is None else max(0, end - recent)
    return list(range(start, end))


def evaluate(errors: np.ndarray, *, first_test: int | None = None, scheme: str = "expanding",
             horizons=HORIZONS, methods=None, level: float = LEVEL, min_past: int = MIN_PAST,
             n: int = N_SIMS, seed: int = 0, windows: range | None = None) -> dict:
    """Coverage of the Monte Carlo ranges, per method and horizon, chronologically.
    ``windows`` restricts which windows are evaluated (e.g. the validation period only)."""
    methods = methods or METHODS
    W = errors.shape[0]
    if scheme == "expanding":
        ks = [k for k in (windows if windows is not None else range(min_past, W)) if k >= min_past]
        ft = None
    elif scheme == "final_test":
        if first_test is None:
            raise ValueError("final_test scheme needs first_test")
        ks, ft = list(range(first_test, W)), first_test
    else:
        raise ValueError("scheme must be 'expanding' or 'final_test'")
    out = {}
    for name, (block, recent) in methods.items():
        rng = np.random.default_rng(seed)
        per_h = {}
        evals = [(k, past_pool(k, recent, ft)) for k in ks]
        for h in horizons:
            rows = [_evaluate_one(errors[past, :h], errors[k, :h], block if block is not None and block < h else None,
                                  rng, level, n) for k, past in evals]
            dh, dd = sum(r["daily_hits"] for r in rows), sum(r["days"] for r in rows)
            th = sum(r["total_hit"] for r in rows)
            per_h[h] = {"daily_coverage": dh / dd if dd else None,
                        "total_coverage": th / len(rows) if rows else None,
                        "total_hits": th, "windows": len(rows),
                        "total_coverage_95ci": wilson(th, len(rows)),
                        "mean_total_width": float(np.mean([r["total_width"] for r in rows])) if rows else None,
                        "mean_interval_score": float(np.mean([r["total_score"] for r in rows])) if rows else None,
                        "mean_total_error": float(np.mean([r["total_error"] for r in rows])) if rows else None}
        out[name] = per_h
    return {"scheme": scheme, "level": level, "windows_total": W, "min_past": min_past,
            "evaluated_windows": len(ks), "methods": out}


@dataclass(frozen=True)
class Validation:
    """What the dashboard shows next to a Monte Carlo range."""
    method: str
    horizon: int
    daily_coverage: float | None
    total_coverage: float | None
    total_hits: int
    windows: int
    ci95: list[float] | None
    assessable: bool

    def text(self, what: str = "total") -> str:
        if self.windows == 0:
            return "not validated (no out-of-sample windows)"
        msg = (f"validated out of sample: {self.total_hits}/{self.windows} past {self.horizon}-day "
               f"totals inside their 80% range ({self.total_coverage:.0%}); daily coverage "
               f"{self.daily_coverage:.0%}")
        if not self.assessable:
            msg += f" — only {self.windows} windows, so calibration is NOT established"
        return msg

    def as_dict(self) -> dict:
        return {"method": self.method, "horizon_days": self.horizon,
                "daily_coverage": self.daily_coverage, "total_coverage": self.total_coverage,
                "total_hits": self.total_hits, "windows": self.windows,
                "total_coverage_95ci": self.ci95, "assessable": self.assessable,
                "scheme": "expanding window: each window judged by an interval built only from "
                          "earlier windows", "summary": self.text()}


def validation_for(errors: np.ndarray, horizon: int, method: str = DEFAULT_METHOD) -> Validation:
    r = evaluate(errors, horizons=(horizon,), methods={method: METHODS[method]})["methods"][method][horizon]
    return Validation(method, horizon, r["daily_coverage"], r["total_coverage"], r["total_hits"],
                      r["windows"], r["total_coverage_95ci"], r["windows"] >= ASSESSABLE_WINDOWS)


@lru_cache(maxsize=256)
def _cached(key: bytes, shape: tuple, horizon: int, method: str) -> Validation:
    return validation_for(np.frombuffer(key).reshape(shape), horizon, method)


def board_validation(board, horizon: int = 28, method: str = DEFAULT_METHOD) -> Validation:
    errs, _ = chronological_windows(board)
    errs = np.ascontiguousarray(errs, dtype=float)
    return _cached(errs.tobytes(), errs.shape, horizon, method)


# ------------------------------------------------------------------------------ cash paths
_PRED_CACHE: dict = {}


def window_forecasts(history, target: str, board) -> np.ndarray:
    """Forecasts of every backtest window in time order (validation windows are re-run with the
    selected model exactly as the leaderboard did; final-test forecasts are stored). Checked:
    actual - forecast reproduces the stored errors."""
    from .leaderboard import (
        EvaluationConfig,
        _extended_dates,
        plan_folds,
        run_cutoffs,
        target_series,
    )
    key = (board.meta.get("cache", {}).get("key") or id(board), target, len(history))
    if key not in _PRED_CACHE:
        cfg = EvaluationConfig(**board.meta["config"])
        y = target_series(history, target)
        val_cuts, _, _ = plan_folds(len(y), cfg)
        vp, va = run_cutoffs(board.selected, y, _extended_dates(history, cfg.horizon), val_cuts, cfg.horizon)
        if not np.allclose(va - vp, board.val_residuals, atol=1e-6):
            raise ValueError("re-run validation forecasts do not reproduce the stored errors")
        _PRED_CACHE[key] = np.vstack([vp, board.holdout_pred])
    return _PRED_CACHE[key]


def cash_validation(history, board, horizon: int = 28, method: str = DEFAULT_METHOD,
                    level: float = LEVEL, n: int = N_SIMS, seed: int = 0) -> dict:
    """Out-of-sample check of the cash simulation for net cash flow, chronologically: does the
    80% range cover (a) the H-day total and (b) the lowest point of cumulative cash flow (what a
    cash shortfall depends on)? Shortfall probabilities themselves are NOT validated here."""
    errors, _ = chronological_windows(board)
    preds = window_forecasts(history, "net_cash_flow", board)
    block, recent = METHODS[method]
    rng = np.random.default_rng(seed)
    alpha = 1 - level
    tot_hits = min_hits = 0
    ks = list(range(MIN_PAST, errors.shape[0]))
    for k in ks:
        pool = errors[past_pool(k, recent), :horizon]
        sims = preds[k, :horizon] + sample_errors(pool, n, rng, block if block is not None and block < horizon else None)
        cum = np.cumsum(sims, axis=1)
        actual = np.cumsum(preds[k, :horizon] + errors[k, :horizon])
        lo, hi = np.quantile(cum[:, -1], [alpha / 2, 1 - alpha / 2])
        tot_hits += lo <= actual[-1] <= hi
        mlo, mhi = np.quantile(np.minimum(0, cum.min(1)), [alpha / 2, 1 - alpha / 2])
        min_hits += mlo <= min(0.0, actual.min()) <= mhi
    k = len(ks)
    return {"method": method, "horizon_days": horizon, "windows": k,
            "total_hits": int(tot_hits), "total_coverage": tot_hits / k if k else None,
            "total_coverage_95ci": wilson(int(tot_hits), k),
            "running_min_hits": int(min_hits), "running_min_coverage": min_hits / k if k else None,
            "running_min_coverage_95ci": wilson(int(min_hits), k),
            "assessable": k >= ASSESSABLE_WINDOWS,
            "summary": (f"cash-flow ranges checked out of sample on {k} past {horizon}-day windows: "
                        f"total inside the 80% range {tot_hits}/{k}, lowest cumulative point inside "
                        f"{min_hits}/{k}"),
            "shortfall_probability_validated": False}


def lab_validation(ctx, horizon: int) -> dict:
    """Validation of the forecast-error part of Scenario Lab revenue and profit totals."""
    key = ("lab_validation", horizon)
    if key not in ctx._cache:
        errs = lab_errors(ctx)
        ctx._cache[key] = {
            "revenue_total": validation_for(errs["lab_revenue_tracked"], horizon).as_dict(),
            "profit_total": validation_for(errs["lab_profit_tracked_minus_fixed"], horizon).as_dict(),
            "note": ("Checks the forecast-error part of the ranges (product demand errors priced at "
                     "current prices; profit also subtracts fixed-cost errors). Assumption ranges "
                     "(price sensitivity, substitution, marketing response) and differences between "
                     "options cannot be validated: no counterfactual outcomes exist.")}
    return ctx._cache[key]


def product_validation(ctx, product_id: str, horizon: int) -> Validation:
    key = ("product_validation", product_id, horizon)
    if key not in ctx._cache:
        ctx._cache[key] = validation_for(np.asarray(ctx.bundle.targets[f"demand_{product_id}"].residuals, float), horizon)
    return ctx._cache[key]


def combined_errors(boards: dict, weights: dict, extra: dict | None = None) -> np.ndarray:
    """Error windows of a linear combination sum_j w_j * error_j (same windows, same days)."""
    out = None
    for name, w in weights.items():
        e, _ = chronological_windows(boards[name])
        out = w * e if out is None else out + w * e
    for name, (w, board) in (extra or {}).items():
        e, _ = chronological_windows(board)
        out = out + w * e
    return out


# ------------------------------------------------------------------------------ reports
def lab_errors(ctx) -> dict[str, np.ndarray]:
    """Random part of Scenario Lab totals, as error windows (chronological, same windows as the
    simulator draws): product demand errors priced at current menu prices (revenue) or at
    price - ingredient cost (gross profit), minus the fixed-cost forecast error (profit).
    Other menu items and stock limits are left out, so this approximates the Lab's uncertainty."""
    from .products import PRODUCT_IDS
    prods = {p: np.asarray(ctx.bundle.targets[f"demand_{p}"].residuals, float) for p in PRODUCT_IDS}
    rev = sum(ctx.prices[i] * prods[p] for i, p in enumerate(PRODUCT_IDS))
    gp = sum((ctx.prices[i] - ctx.unit_cost[i]) * prods[p] for i, p in enumerate(PRODUCT_IDS))
    fixed = np.asarray(ctx.agg.targets["fixed_costs"].residuals, float)
    return {"lab_revenue_tracked": rev, "lab_profit_tracked_minus_fixed": gp - fixed,
            "products": prods}


def full_report(models=None, n: int = N_SIMS) -> dict:
    """Old vs new methods for every interval the app displays (synthetic demo businesses)."""
    from .agent.tools import BusinessSession
    s = BusinessSession.from_demo("restaurant", models=models)
    out = {}
    targets = {}
    for t in ("revenue", "units_sold", "net_cash_flow"):
        targets[t] = chronological_windows(s.board(t))
    ctx = s.product_context()
    lab = lab_errors(ctx)
    first = targets["revenue"][1]
    for p, e in lab["products"].items():
        targets[f"demand_{p}"] = (e, first)
    targets["lab_revenue_tracked"] = (lab["lab_revenue_tracked"], first)
    targets["lab_profit_tracked_minus_fixed"] = (lab["lab_profit_tracked_minus_fixed"], first)
    for name, (errs, ft) in targets.items():
        out[name] = {"expanding": evaluate(errs, n=n), "final_test": evaluate(errs, first_test=ft,
                                                                               scheme="final_test", n=n)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Monte Carlo interval calibration report")
    ap.add_argument("--json", help="write the full report to this file")
    args = ap.parse_args()
    rep = full_report()
    print("80% Monte Carlo ranges, synthetic demo restaurant. Coverage = share of out-of-sample "
          "windows/days inside the range. Expanding scheme: each window judged only by earlier windows.")
    for target, r in rep.items():
        e = r["expanding"]
        print(f"\n{target}  ({e['evaluated_windows']} evaluated windows)")
        for m, per_h in e["methods"].items():
            cells = []
            for h, v in per_h.items():
                cells.append(f"H{h}: total {v['total_hits']}/{v['windows']} ({v['total_coverage']:.0%}) "
                             f"daily {v['daily_coverage']:.0%} width {v['mean_total_width']:,.0f}")
            print(f"  {m:16s} " + " | ".join(cells))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rep, fh, indent=1)


if __name__ == "__main__":
    main()
