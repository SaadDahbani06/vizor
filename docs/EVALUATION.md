# ML and decision-system evaluation plan

## Implemented protocol (Milestone 1, `src/decisiondesk/leaderboard.py`)
- Direct multi-horizon models; features at origin `o` use only `y[:o]` + calendar facts of the target day.
- Final `holdout_folds` windows (default 6 x 28 days) are an untouched test; validation windows end before it.
- Each fold refits on all data before its cutoff (expanding window); scalers refit inside every fit.
- Selection on validation WAPE; ML must beat the best baseline by >= 2% relative or the baseline is kept.
- 80% intervals from validation residual quantiles per forecast week; coverage measured on the test.
- Net cash: use MAE/RMSE, not WAPE (denominator is a small difference of large numbers).
- Cash risk: 7-day block bootstrap of out-of-sample error paths (not demeaned). Calibration is only claimed with >= 20 holdout windows.
- Run: `python -m decisiondesk.leaderboard --industry restaurant --target revenue`. Synthetic results are in `docs/ARCHITECTURE_REVIEW.md` and test the software, **not generalization to real organizations**.

## Model candidates (later)
1. Seasonal naive and moving average (required baselines).
2. Ridge regression with lagged target, weekday, month, trailing means, optional known-in-advance exogenous variables.
3. Gradient boosting (XGBoost, CatBoost) when enough real records and cross-validation folds exist.
4. SARIMAX when sample size and frequency suit it; avoid over-complexity.

## Backtest protocol
- Set an explicit as-of date; use only information then available.
- Chronological train/validation/test; rolling-origin folds with no leakage across windows.
- Evaluate revenue and demand separately. Use MAE, RMSE, WAPE where denominator meaningful, model-vs-baseline skill.
- If forecasting event probabilities: assess calibration / Brier score when actual outcomes are numerous enough.
- Refit scalers/encoders/feature calculations inside each training fold; forecasts only use observed past or known future covariates.
- External sources must reflect publication delay and revised data; missing vintages must be flagged.
- Select on validation only; preserve an untouched final holdout when enough data exists.
- Run feature ablation: internal-only model vs internal + economic features to see whether external signals actually help.
- Report data dates, sample sizes, missingness, uncertainty, error distributions, and failure cases.

## Scenario validity
Financial 'price up 4%' does not imply sales increase 4%: elasticity is assumed/estimated from suitable data. Supplier discounts require procurement feasibility. Inventory reorder recommendations need lead time, on-hand counts, carrying costs, and stockout history.

## Do not claim
- No forecast '95% accuracy' unless a precisely defined metric supports it.
- No certainty about real-world events from historical correlations.
- No evidence of business savings without controlled/credible measurement.
