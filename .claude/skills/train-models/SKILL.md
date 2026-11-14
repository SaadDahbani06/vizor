---
name: train-models
description: Train and evaluate trustworthy ML revenue/demand forecasters using chronological splits and real measured results.
---
Read `docs/EVALUATION.md`. Add Ridge, XGBoost and/or CatBoost as available, with seasonal-naive as an enforced baseline. Implement strictly time-aware lag and rolling features with only past observations, and record timestamps and publication vintages for any external covariates. Evaluate rolling-origin validation and holdout, MAE/RMSE/WAPE. Save actual results and choose model by validation; advanced ML may lose. Synthetic-only evaluation must be clearly labeled. Run pytest, demo, and a separate leaderboard command. Do not fabricate accuracy or use random train_test_split on time series.
