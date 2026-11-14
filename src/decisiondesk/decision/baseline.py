"""No-action baseline from the SELECTED forecasting models, plus shared random draws.

Each target's baseline is ``leaderboard.forecast_selected`` (the validation-selected model refit
on all history). Uncertainty comes from that model's out-of-sample backtest residual paths
(validation + holdout folds). All targets share fold indices, so the simulation keeps the
historical co-movement of, e.g., revenue and variable costs. All actions share the same draws
(common random numbers), so differences between actions are not polluted by sampling noise.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..leaderboard import EvaluationConfig, Leaderboard, build_leaderboard, forecast_selected

NONNEGATIVE = {"revenue", "variable_costs", "fixed_costs", "units_sold", "purchase_dollars",
               "online_orders_per_store", "instore_orders_per_store"}
BLOCK = 7


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
        if draws.fold_idx.shape[1] != -(-self.horizon // BLOCK):
            raise ValueError("draws were made for a different horizon")
        out = {}
        for name, tf in self.targets.items():
            paths = np.empty((draws.n, self.horizon))
            for b, start in enumerate(range(0, self.horizon, BLOCK)):
                stop = min(start + BLOCK, self.horizon)
                paths[:, start:stop] = tf.point[start:stop] + tf.residuals[draws.fold_idx[:, b],
                                                                            start:stop]
            out[name] = np.maximum(paths, 0.0) if name in NONNEGATIVE else paths
        return out


@dataclass(frozen=True)
class SimulationDraws:
    """Common random numbers: fold indices per 7-day block and a uniform stream per assumption
    name. The stream for a name depends only on (seed, name), never on evaluation order."""

    n: int
    seed: int
    fold_idx: np.ndarray

    def uniform(self, name: str) -> np.ndarray:
        rng = np.random.default_rng([self.seed, zlib.crc32(name.encode())])
        return rng.random(self.n)


def make_draws(bundle: ForecastBundle, n: int = 4000, seed: int = 20261114) -> SimulationDraws:
    if n < 100:
        raise ValueError("use at least 100 simulations")
    rng = np.random.default_rng(seed)
    blocks = -(-bundle.horizon // BLOCK)
    return SimulationDraws(n, seed, rng.integers(0, bundle.n_folds, size=(n, blocks)))


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
