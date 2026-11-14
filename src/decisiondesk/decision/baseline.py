"""No-action baseline from the SELECTED forecasting models, plus shared random draws.

Each target's baseline is ``leaderboard.forecast_selected`` (the validation-selected model refit
on all history). Uncertainty comes from that model's out-of-sample backtest residual paths
(validation + holdout windows, in time order).

Sampling (Milestone 5.1): each simulated path takes the residuals of ONE whole historical window
(``block=None``). Forecast errors are correlated across all weeks of a window (a forecast is often
off by a level for the whole month), so drawing each week from a different window (the
Milestone 5 method, ``block=7``) made multi-week totals too narrow. Validation:
``decisiondesk.calibration`` and docs/MILESTONE_5_1_CALIBRATION.md. Shorter horizons use the
first days of the same window.

All targets share the window index of a path, so the simulation keeps the historical
co-movement of, e.g., revenue and variable costs, or the five products. All actions share the
same draws (common random numbers), so differences between actions are not polluted by
sampling noise.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..leaderboard import EvaluationConfig, Leaderboard, build_leaderboard, forecast_selected

NONNEGATIVE = {"revenue", "variable_costs", "fixed_costs", "units_sold", "purchase_dollars",
               "online_orders_per_store", "instore_orders_per_store"}
WHOLE_WINDOW = None   # default: one historical window per simulated path
WEEKLY_BLOCK = 7      # Milestone 5 method, kept for comparisons and tests


@dataclass
class TargetForecast:
    target: str
    model: str
    dates: pd.DatetimeIndex
    point: np.ndarray            # (H,)
    residuals: np.ndarray        # (folds, H) out-of-sample actual - prediction
    validation_wape: float | None = None
    test_wape: float | None = None
    selection_reason: str = ""
    validation_period: tuple[str, str] | None = None
    test_period: tuple[str, str] | None = None


@dataclass
class ForecastBundle:
    dataset: str
    data_label: str
    origin_date: pd.Timestamp     # last observed day; forecasts start the day after
    horizon: int
    targets: dict[str, TargetForecast]
    feature_set: str = "internal"
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        folds = {t.residuals.shape for t in self.targets.values()}
        if len(folds) != 1:
            raise ValueError(f"targets must share the same fold x horizon residual shape: {folds}")
        if next(iter(folds))[1] != self.horizon:
            raise ValueError("residual horizon does not match bundle horizon")

    @property
    def n_folds(self) -> int:
        return next(iter(self.targets.values())).residuals.shape[0]

    def simulate(self, draws: SimulationDraws) -> dict[str, np.ndarray]:
        """Baseline (no-action) sample paths, sims x H, for every target."""
        block = self.horizon if draws.block is None else draws.block
        if draws.fold_idx.shape[1] != -(-self.horizon // block):
            raise ValueError("draws were made for a different horizon or block length")
        if draws.fold_idx.max() >= self.n_folds:
            raise ValueError("draws reference more backtest windows than this bundle has")
        out = {}
        for name, tf in self.targets.items():
            paths = np.empty((draws.n, self.horizon))
            for b, start in enumerate(range(0, self.horizon, block)):
                stop = min(start + block, self.horizon)
                paths[:, start:stop] = tf.point[start:stop] + tf.residuals[draws.fold_idx[:, b],
                                                                            start:stop]
            out[name] = np.maximum(paths, 0.0) if name in NONNEGATIVE else paths
        return out


@dataclass(frozen=True)
class SimulationDraws:
    """Common random numbers: the historical window index of each path (per block when
    ``block`` is set) and a uniform stream per assumption name. The stream for a name depends
    only on (seed, name), never on evaluation order."""

    n: int
    seed: int
    fold_idx: np.ndarray
    block: int | None = WHOLE_WINDOW

    def uniform(self, name: str) -> np.ndarray:
        rng = np.random.default_rng([self.seed, zlib.crc32(name.encode())])
        return rng.random(self.n)


def make_draws(bundle: ForecastBundle, n: int = 4000, seed: int = 20261114,
               block: int | None = WHOLE_WINDOW) -> SimulationDraws:
    """``block=None``: each path uses one whole historical window (default). ``block=7``: the
    Milestone 5 weekly-block method (for comparisons)."""
    if n < 100:
        raise ValueError("use at least 100 simulations")
    if block is not None and block < 1:
        raise ValueError("block must be a positive number of days or None (whole window)")
    rng = np.random.default_rng(seed)
    blocks = 1 if block is None else -(-bundle.horizon // block)
    return SimulationDraws(n, seed, rng.integers(0, bundle.n_folds, size=(n, blocks)), block)


def build_forecast_bundle(history: pd.DataFrame, targets: tuple[str, ...], *, dataset: str,
                          data_label: str, cfg: EvaluationConfig | None = None,
                          models: tuple[str, ...] | None = None,
                          boards: dict[str, Leaderboard] | None = None) -> ForecastBundle:
    """Run (or reuse) the Milestone 1 leaderboard per target and keep only the selected model.

    Model selection, folds and the untouched test are exactly those of ``build_leaderboard``;
    this function does not choose models itself.
    """
    cfg = cfg or EvaluationConfig()
    boards = dict(boards or {})
    out = {}
    for target in targets:
        board = boards.get(target) or build_leaderboard(
            history, target, dataset=dataset, data_label=data_label, cfg=cfg, models=models)
        fc = forecast_selected(history, target, board)
        sel = next(m for m in board.models if m.name == board.selected)
        out[target] = TargetForecast(
            target=target, model=board.selected, dates=pd.DatetimeIndex(fc["date"]),
            point=fc[target].to_numpy(float),
            residuals=np.vstack([board.val_residuals, board.holdout_actual - board.holdout_pred]),
            validation_wape=sel.validation["wape"], test_wape=sel.holdout["wape"],
            selection_reason=board.selection_reason,
            validation_period=tuple(board.meta["validation_period"]),
            test_period=tuple(board.meta["holdout_period"]))
    return ForecastBundle(dataset=dataset, data_label=data_label,
                          origin_date=pd.Timestamp(history["date"].iloc[-1]),
                          horizon=cfg.horizon, targets=out,
                          notes=[("Internal features only: the Milestone 2 ablation rejected "
                                  "FRED features on validation for every target.")])
