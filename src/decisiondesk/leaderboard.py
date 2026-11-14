"""Time-aware model leaderboard: rolling-origin validation + untouched final holdout.

Protocol (see docs/EVALUATION.md):
  * The last ``holdout_folds`` forecast windows form a final test that is never used for model
    choice. Validation windows all end before the holdout begins.
  * At every cutoff a model is refit on y[:cutoff] only and forecasts the next ``horizon`` days.
  * Model selection uses validation WAPE only. An ML model must beat the best baseline by at
    least ``min_relative_improvement`` or the baseline is kept.
  * Empirical prediction intervals come from validation residuals (bucketed by forecast week);
    their coverage is then *measured* on the holdout. They are empirical, not guaranteed.

CLI:  python -m decisiondesk.leaderboard --industry restaurant --target revenue
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .features import WARMUP_DAYS, build_origin_features, training_rows
from .models import BASELINES, REGISTRY

TARGETS = ("revenue", "units_sold", "variable_costs", "net_cash_flow")


@dataclass(frozen=True)
class EvaluationConfig:
    horizon: int = 28
    step: int = 28
    holdout_folds: int = 6
    min_train: int = 224
    min_val_folds: int = 4
    interval: float = 0.80
    min_relative_improvement: float = 0.02

    def __post_init__(self) -> None:
        if self.horizon <= 0 or self.step <= 0 or self.holdout_folds <= 0:
            raise ValueError("horizon, step and holdout_folds must be positive")
        if not 0 < self.interval < 1:
            raise ValueError("interval must be in (0, 1)")
        if self.min_train <= WARMUP_DAYS:
            raise ValueError(f"min_train must exceed the {WARMUP_DAYS}-day feature warm-up")


def plan_folds(n: int, cfg: EvaluationConfig) -> tuple[list[int], list[int], int]:
    """Return (validation_cutoffs, holdout_cutoffs, holdout_start). Raises if data too short."""
    holdout_start = n - (cfg.holdout_folds - 1) * cfg.step - cfg.horizon
    holdout = list(range(holdout_start, n - cfg.horizon + 1, cfg.step))
    validation = list(range(cfg.min_train, holdout_start - cfg.horizon + 1, cfg.step))
    if holdout_start <= cfg.min_train or len(validation) < cfg.min_val_folds:
        need = cfg.min_train + cfg.min_val_folds * cfg.step + cfg.horizon - cfg.step \
            + (cfg.holdout_folds - 1) * cfg.step + cfg.horizon
        raise ValueError(
            f"Need about {need} daily observations for {cfg.min_val_folds} validation folds and "
            f"{cfg.holdout_folds} holdout folds at horizon {cfg.horizon}; got {n}. Use a shorter "
            "horizon/fewer holdout folds, or stay with the weekday baseline.")
    return validation, holdout, holdout_start


def target_series(history: pd.DataFrame, target: str) -> np.ndarray:
    if target == "net_cash_flow":
        return (history["revenue"] - history["variable_costs"] - history["fixed_costs"]).to_numpy(float)
    if target not in history.columns:
        raise ValueError(f"Target '{target}' not in data; available: "
                         f"{[c for c in TARGETS if c in history.columns or c == 'net_cash_flow']}")
    return history[target].to_numpy(float)


def _extended_dates(history: pd.DataFrame, horizon: int) -> pd.DatetimeIndex:
    start = pd.Timestamp(history["date"].iloc[0])
    return pd.date_range(start, periods=len(history) + horizon, freq="D")


def run_cutoffs(model_name: str, y: np.ndarray, dates: pd.DatetimeIndex, cutoffs: list[int],
                horizon: int, exog: pd.DataFrame | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Refit at each cutoff on y[:cutoff]; return (predictions, actuals), each folds x horizon."""
    spec = REGISTRY[model_name]
    preds, actuals = [], []
    for cut in cutoffs:
        model = spec.build()
        if spec.kind == "ml":
            X_train, y_train = training_rows(y, dates, cut, horizon, exog)
            model.fit(X_train, y_train)
        X_next = build_origin_features(y[:cut], dates, np.array([cut]), horizon, exog)
        preds.append(model.predict(X_next))
        actuals.append(y[cut:cut + horizon])
    return np.asarray(preds), np.asarray(actuals)


def point_metrics(pred: np.ndarray, actual: np.ndarray) -> dict:
    err = (np.asarray(pred) - np.asarray(actual)).ravel()
    denom = float(np.abs(actual).sum())
    return {"mae": float(np.mean(np.abs(err))), "rmse": float(np.sqrt(np.mean(err ** 2))),
            "wape": float(np.abs(err).sum() / denom) if denom > 0 else None,
            "bias": float(np.mean(err)), "n": int(err.size)}


def interval_offsets(residuals: np.ndarray, level: float) -> np.ndarray:
    """Per-forecast-week (lo, hi) residual quantiles; residual = actual - prediction."""
    horizon = residuals.shape[1]
    alpha = 1 - level
    buckets = np.arange(horizon) // 7
    out = np.zeros((horizon, 2))
    for b in np.unique(buckets):
        r = residuals[:, buckets == b].ravel()
        out[buckets == b] = (np.quantile(r, alpha / 2, method="lower"),
                             np.quantile(r, 1 - alpha / 2, method="higher"))
    return out


def interval_coverage(pred: np.ndarray, actual: np.ndarray, offsets: np.ndarray) -> float:
    lo, hi = pred + offsets[:, 0], pred + offsets[:, 1]
    return float(np.mean((actual >= lo) & (actual <= hi)))


def select_model(val_wape: dict[str, float | None], *, min_relative_improvement: float) -> tuple[str, str]:
    """Pick by validation WAPE; ML must beat the best baseline by the stated relative margin."""
    scored = {k: v for k, v in val_wape.items() if v is not None and np.isfinite(v)}
    # Baselines are identified by their registered kind (mandatory ones are in BASELINES).
    baselines = {k: v for k, v in scored.items()
                 if k in BASELINES or (k in REGISTRY and REGISTRY[k].kind == "baseline")}
    if not baselines:
        raise ValueError("At least one baseline must be evaluated")
    best_base = min(baselines, key=baselines.get)
    best = min(scored, key=scored.get)
    if best in baselines:
        return best, f"{best} has the lowest validation WAPE; no model beat the baselines"
    gain = 1 - scored[best] / baselines[best_base] if baselines[best_base] > 0 else 0.0
    if gain < min_relative_improvement:
        return best_base, (f"{best} improved validation WAPE by only {gain:.1%} over {best_base} "
                           f"(< {min_relative_improvement:.0%} required); keeping the baseline")
    return best, f"{best} improved validation WAPE by {gain:.1%} over best baseline {best_base}"


@dataclass
class ModelResult:
    name: str
    kind: str
    description: str
    available: bool
    unavailable_reason: str = ""
    validation: dict | None = None
    holdout: dict | None = None
    holdout_interval_coverage: float | None = None
    skill_vs_seasonal_naive_val: float | None = None
    fit_seconds: float | None = None


@dataclass
class Leaderboard:
    meta: dict
    models: list[ModelResult]
    selected: str
    selection_reason: str
    # Arrays for downstream use (not serialized): selected model only.
    val_residuals: np.ndarray = field(repr=False, default=None)
    holdout_pred: np.ndarray = field(repr=False, default=None)
    holdout_actual: np.ndarray = field(repr=False, default=None)

    def table(self) -> pd.DataFrame:
        rows = []
        for m in self.models:
            row = {"model": m.name, "kind": m.kind}
            if not m.available:
                row["note"] = m.unavailable_reason
            else:
                for split, scores in (("val", m.validation), ("test", m.holdout)):
                    for key in ("mae", "rmse", "wape", "bias"):
                        row[f"{split}_{key}"] = scores[key]
                row["val_skill_vs_snaive"] = m.skill_vs_seasonal_naive_val
                row[f"test_cov@{self.meta['interval_nominal']:.0%}"] = m.holdout_interval_coverage
                row["selected"] = "<==" if m.name == self.selected else ""
            rows.append(row)
        return pd.DataFrame(rows)

    def to_dict(self) -> dict:
        return {"meta": self.meta, "selected": self.selected,
                "selection_reason": self.selection_reason,
                "models": [asdict(m) for m in self.models]}

    def save(self, out_dir: str | Path) -> tuple[Path, Path]:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        suffix = "" if self.meta.get("feature_set", "internal") == "internal" \
            else f"_{self.meta['feature_set']}"
        stem = f"leaderboard_{self.meta['dataset']}_{self.meta['target']}{suffix}"
        json_path, csv_path = out / f"{stem}.json", out / f"{stem}.csv"
        json_path.write_text(json.dumps(self.to_dict(), indent=2, default=float))
        self.table().to_csv(csv_path, index=False)
        return json_path, csv_path


def build_leaderboard(history: pd.DataFrame, target: str, *, dataset: str, data_label: str,
                      cfg: EvaluationConfig | None = None,
                      models: tuple[str, ...] | None = None,
                      exog: pd.DataFrame | None = None,
                      feature_set: str = "internal") -> Leaderboard:
    """``exog``: origin-indexed known-at-origin features (see decisiondesk.macro). Baselines
    ignore it; ML models use it. Folds, holdout and selection rule are identical either way."""
    cfg = cfg or EvaluationConfig()
    y = target_series(history, target)
    dates = _extended_dates(history, cfg.horizon)
    val_cuts, test_cuts, holdout_start = plan_folds(len(y), cfg)
    names = list(models or REGISTRY)
    for base in BASELINES:
        if base not in names:
            names.insert(0, base)  # baselines are mandatory

    results, arrays = [], {}
    for name in names:
        spec = REGISTRY[name]
        ok, why = spec.availability()
        if not ok:
            results.append(ModelResult(name, spec.kind, spec.description, False, why))
            continue
        start = time.perf_counter()
        vp, va = run_cutoffs(name, y, dates, val_cuts, cfg.horizon, exog)
        tp, ta = run_cutoffs(name, y, dates, test_cuts, cfg.horizon, exog)
        elapsed = time.perf_counter() - start
        offsets = interval_offsets(va - vp, cfg.interval)
        arrays[name] = (va - vp, tp, ta)
        results.append(ModelResult(
            name, spec.kind, spec.description, True,
            validation=point_metrics(vp, va), holdout=point_metrics(tp, ta),
            holdout_interval_coverage=interval_coverage(tp, ta, offsets),
            fit_seconds=round(elapsed, 2)))

    snaive_mae = next(r.validation["mae"] for r in results if r.name == "seasonal_naive")
    for r in results:
        if r.available and snaive_mae > 0:
            r.skill_vs_seasonal_naive_val = 1 - r.validation["mae"] / snaive_mae
    selected, reason = select_model(
        {r.name: r.validation["wape"] for r in results if r.available},
        min_relative_improvement=cfg.min_relative_improvement)

    d = history["date"]
    meta = {
        "dataset": dataset, "data_label": data_label, "target": target, "n_obs": len(y),
        "date_range": [str(d.iloc[0].date()), str(d.iloc[-1].date())],
        "validation_period": [str(d.iloc[val_cuts[0]].date()),
                              str(d.iloc[val_cuts[-1] + cfg.horizon - 1].date())],
        "holdout_period": [str(d.iloc[holdout_start].date()), str(d.iloc[-1].date())],
        "n_validation_folds": len(val_cuts), "n_holdout_folds": len(test_cuts),
        "interval_nominal": cfg.interval, "config": asdict(cfg),
        "interval_method": "empirical quantiles of validation residuals by forecast week",
        "hyperparameters": "fixed, untuned",
        "feature_set": feature_set,
        "exogenous_columns": [] if exog is None else list(exog.columns),
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "caveat": ("Scores on SYNTHETIC data test the software, not real-world accuracy."
                   if "SYNTHETIC" in data_label.upper() else
                   "Scores describe this uploaded history only; re-evaluate as data changes."),
    }
    val_res, tp, ta = arrays[selected]
    return Leaderboard(meta, results, selected, reason, val_res, tp, ta)


def forecast_selected(history: pd.DataFrame, target: str, board: Leaderboard,
                      exog: pd.DataFrame | None = None) -> pd.DataFrame:
    """Refit the selected model on all history and forecast the next horizon with intervals.

    Production intervals use validation AND holdout residuals (the holdout is no longer needed
    for selection once the leaderboard is frozen).
    """
    cfg = EvaluationConfig(**board.meta["config"])
    y = target_series(history, target)
    dates = _extended_dates(history, cfg.horizon)
    pred, _ = run_cutoffs(board.selected, np.append(y, np.full(cfg.horizon, np.nan)), dates,
                          [len(y)], cfg.horizon, exog)
    residuals = np.vstack([board.val_residuals, board.holdout_actual - board.holdout_pred])
    offsets = interval_offsets(residuals, cfg.interval)
    point = pred[0]
    return pd.DataFrame({"date": dates[len(y):], target: point,
                         "lower": point + offsets[:, 0], "upper": point + offsets[:, 1]})


def main() -> None:
    from .data import DatasetSpec, ingest_daily_csv, validate_daily_frame
    from .synthetic import make_sample

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--industry", choices=["restaurant", "retail"], default="restaurant")
    parser.add_argument("--csv", help="Canonical daily CSV (uploaded user data)")
    parser.add_argument("--currency", default=None, help="ISO currency of the CSV, e.g. USD")
    parser.add_argument("--missing-days", choices=["error", "closed_zero"], default="error")
    parser.add_argument("--target", choices=TARGETS, default="revenue")
    parser.add_argument("--horizon", type=int, default=28)
    parser.add_argument("--holdout-folds", type=int, default=6)
    parser.add_argument("--models", nargs="*", choices=list(REGISTRY), default=None)
    parser.add_argument("--out", default="artifacts/leaderboards")
    args = parser.parse_args()

    if args.csv:
        spec = DatasetSpec(currency=args.currency or "USD",
                           currency_declared_by_user=args.currency is not None)
        history, report = ingest_daily_csv(args.csv, spec=spec, missing_days=args.missing_days)
        dataset, label = Path(args.csv).stem, "UPLOADED USER DATA"
    else:
        history, report = validate_daily_frame(make_sample(args.industry),
                                               spec=DatasetSpec(business_type=args.industry),
                                               source=f"synthetic:{args.industry}")
        dataset, label = f"{args.industry}_SYNTHETIC", "SYNTHETIC"
    print(report.summary())
    cfg = EvaluationConfig(horizon=args.horizon, step=args.horizon,
                           holdout_folds=args.holdout_folds)
    board = build_leaderboard(history, args.target, dataset=dataset, data_label=label, cfg=cfg,
                              models=tuple(args.models) if args.models else None)
    print_leaderboard(board)
    json_path, csv_path = board.save(args.out)
    print(f"\nSaved {json_path} and {csv_path}")


def print_leaderboard(board: Leaderboard) -> None:
    m = board.meta
    print(f"\nLEADERBOARD [{m['data_label']}] target={m['target']} horizon={m['config']['horizon']}d")
    print(f"validation {m['validation_period'][0]}..{m['validation_period'][1]} "
          f"({m['n_validation_folds']} folds) | untouched test {m['holdout_period'][0]}.."
          f"{m['holdout_period'][1]} ({m['n_holdout_folds']} folds)")
    with pd.option_context("display.width", 200, "display.max_columns", 30,
                           "display.float_format", "{:,.3f}".format):
        print(board.table().to_string(index=False))
    print(f"SELECTED: {board.selected} — {board.selection_reason}")
    print(f"NOTE: {m['caveat']}")


def cli() -> None:
    """Entry point: user-facing data/config problems print a message, not a traceback."""
    try:
        main()
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")


if __name__ == "__main__":
    cli()


# ---------------------------------------------------------------------------------- disk cache
CACHE_DIR = Path("data/cache/leaderboards")


def _code_fingerprint() -> str:
    import hashlib
    import importlib.metadata as md
    h = hashlib.sha256()
    here = Path(__file__).parent
    for name in ("features.py", "models.py", "leaderboard.py"):
        h.update((here / name).read_bytes())
    for pkg in ("numpy", "pandas", "scikit-learn", "xgboost", "catboost"):
        try:
            h.update(f"{pkg}={md.version(pkg)}".encode())
        except md.PackageNotFoundError:
            h.update(f"{pkg}=absent".encode())
    return h.hexdigest()


def leaderboard_cache_key(history: pd.DataFrame, target: str, data_label: str,
                          cfg: EvaluationConfig, models: tuple[str, ...] | None) -> str:
    import hashlib
    y = target_series(history, target)
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(y, dtype=float).tobytes())
    h.update(str(history["date"].iloc[0]).encode() + str(len(history)).encode())
    h.update(json.dumps({"target": target, "label": data_label, "cfg": asdict(cfg),
                         "models": list(models or REGISTRY)}, sort_keys=True).encode())
    h.update(_code_fingerprint().encode())
    return h.hexdigest()[:32]


def build_leaderboard_cached(history: pd.DataFrame, target: str, *, dataset: str, data_label: str,
                             cfg: EvaluationConfig | None = None,
                             models: tuple[str, ...] | None = None,
                             cache_dir: Path | str | None = None) -> Leaderboard:
    """Same result as ``build_leaderboard``; reuses a validated on-disk copy when the data, config,
    model list, forecasting code and library versions are byte-for-byte identical."""
    import pickle
    cfg = cfg or EvaluationConfig()
    folder = Path(cache_dir or CACHE_DIR)
    key = leaderboard_cache_key(history, target, data_label, cfg, models)
    path = folder / f"{dataset}_{target}_{key}.pkl"
    if path.exists():
        with path.open("rb") as fh:  # local, gitignored cache written only by this function
            board = pickle.load(fh)
        board.meta["cache"] = {"hit": True, "key": key}
        return board
    board = build_leaderboard(history, target, dataset=dataset, data_label=data_label, cfg=cfg,
                              models=models)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as fh:
        pickle.dump(board, fh)
    tmp.replace(path)
    board.meta["cache"] = {"hit": False, "key": key}
    return board
