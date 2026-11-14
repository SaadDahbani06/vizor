"""Forecasting model registry: enforced baselines plus optional ML regressors.

All ML models are *direct multi-horizon* regressors over ``features.FEATURE_COLUMNS`` and
predict a correction to the 4-week same-weekday mean (``anchor``). Tree models cannot
extrapolate levels, so anchoring keeps them comparable with linear models.

Hyperparameters are FIXED (not tuned). Tuning would have to happen on validation folds only;
it is intentionally left out until real organization data justifies the extra complexity.
"""
from __future__ import annotations

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .features import FEATURE_COLUMNS

ANCHOR = "wd_mean_4"
BASELINES = ("seasonal_naive", "weekday_mean_12w")


class BaselineModel:
    """No fitting; prediction is a precomputed causal feature column."""

    def __init__(self, column: str):
        self.column = column

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> BaselineModel:
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return X[self.column].to_numpy(dtype=float)


class AnchoredRegressor:
    """Fits ``y - anchor`` on FEATURE_COLUMNS; scalers etc. are refit on every call to fit()."""

    def __init__(self, factory: Callable[[], object]):
        self.factory = factory
        self.model = None

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> AnchoredRegressor:
        # Internal features plus any origin-level exogenous columns (prefixed "x_").
        self.columns = [*FEATURE_COLUMNS, *sorted(c for c in X.columns if str(c).startswith("x_"))]
        target = y - X[ANCHOR].to_numpy()
        self.constant = None
        if np.ptp(target) == 0:
            # Anchor already explains the target exactly (e.g. a fixed weekly cost schedule);
            # some learners (CatBoost) refuse constant targets, and the right fit is the constant.
            self.constant, self.model = float(target[0]), None
            return self
        self.model = self.factory()
        self.model.fit(X[self.columns], target)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.constant is not None:
            return X[ANCHOR].to_numpy() + self.constant
        return X[ANCHOR].to_numpy() + np.asarray(self.model.predict(X[self.columns]))


def _ridge():
    return make_pipeline(StandardScaler(), Ridge(alpha=1.0))


def _xgboost():
    from xgboost import XGBRegressor
    return XGBRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.8,
                        colsample_bytree=0.8, random_state=0, n_jobs=4, verbosity=0)


def _catboost():
    from catboost import CatBoostRegressor
    return CatBoostRegressor(iterations=300, depth=4, learning_rate=0.05, random_seed=0,
                             verbose=0, allow_writing_files=False, thread_count=4)


@dataclass(frozen=True)
class ModelSpec:
    name: str
    kind: str  # "baseline" | "ml"
    build: Callable[[], object]
    requires: str | None = None
    description: str = ""

    def availability(self) -> tuple[bool, str]:
        if self.requires and importlib.util.find_spec(self.requires) is None:
            return False, f"optional dependency '{self.requires}' not installed (pip install -e '.[ml]')"
        return True, ""


REGISTRY: dict[str, ModelSpec] = {
    "seasonal_naive": ModelSpec("seasonal_naive", "baseline",
                                lambda: BaselineModel("seasonal_naive"),
                                description="Last observed same-weekday value"),
    "weekday_mean_12w": ModelSpec("weekday_mean_12w", "baseline",
                                  lambda: BaselineModel("wd_mean_12"),
                                  description="Mean of last 12 same-weekday values (starter model)"),
    "historical_mean_28d": ModelSpec("historical_mean_28d", "baseline",
                                     lambda: BaselineModel("roll_mean_28"),
                                     description="Mean of the last 28 observed days (no seasonality)"),
    "ridge": ModelSpec("ridge", "ml", lambda: AnchoredRegressor(_ridge),
                       description="Standardized Ridge(alpha=1) on lag/rolling/calendar features"),
    "xgboost": ModelSpec("xgboost", "ml", lambda: AnchoredRegressor(_xgboost), requires="xgboost",
                         description="XGBRegressor depth 4, 300 trees, lr 0.05"),
    "catboost": ModelSpec("catboost", "ml", lambda: AnchoredRegressor(_catboost),
                          requires="catboost",
                          description="CatBoostRegressor depth 4, 300 iters, lr 0.05"),
}
