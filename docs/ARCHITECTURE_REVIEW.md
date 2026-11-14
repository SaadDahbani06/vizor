# Architecture review and development plan — 2026-11-14

Audience: the three-person team. This records what was wrong with the starter, what Milestone 1
changed, and the order we build in next. Numbers below come from actual runs on **synthetic** data.

## Problems found in the starter

| # | Area | Problem | Status |
|---|---|---|---|
| 1 | Models | One model (12-week weekday mean). No seasonal-naive reference, no holdout, no interval coverage. | **Fixed (M1)**: 5-model leaderboard, rolling-origin + untouched test |
| 2 | Risk | Cash "probability" from an i.i.d. bootstrap of demeaned residuals. Ignores autocorrelation (understates cumulative spread), hides bias, mixes horizons 1–180 d, reports 90 d though only 28 d was backtested. | **Fixed (M1)**: block bootstrap of out-of-sample error paths, horizon = validated horizon, explicit calibration check |
| 3 | Leakage | Synthetic revenue = units × price exactly. Any model using same-day units for revenue leaks. | **Guarded**: features never use same-day covariates; perturbation tests |
| 4 | Ingestion | No currency, timezone, metric definitions, file hash, or missing-day policy (any gap = hard fail). | **Fixed (M1)**: `DatasetSpec`, `IngestionReport`, `missing_days="closed_zero"` with `is_imputed` flag |
| 5 | Connectors | FRED fetches latest revised values only (no `realtime_start`/vintage). `DataProvenance` has no `published_at`. | Open → M2 |
| 6 | Synthetic data | Inventory is never replenished, so the reorder flag always fires. Revenue and units are the same signal (two targets give identical leaderboards). | Open → M3 (needs (s,Q) receipts) |
| 7 | Decisions | Ranking on a point forecast only; elasticity and supplier discount are free parameters with no feasibility or cost; runs on the baseline, not the selected model. | Open → M3 |
| 8 | Repo | Git root is the user's home directory, not this project. | **Action needed**: `git init` inside the project before the team branches |
| 9 | Metrics | WAPE is misleading for net cash (small difference of large numbers; test WAPE ≈ 0.41–0.46). | Use MAE/RMSE for net cash; documented |

## What Milestone 1 now guarantees (and tests)

- **Causal features.** `features.build_origin_features` reads only `y[:origin]` plus calendar
  facts about the target day. Tests poison every value at/after the origin and assert features,
  training rows and fitted Ridge/XGBoost/CatBoost predictions are unchanged.
- **Isolated final test.** `plan_folds` ensures every validation window ends before the holdout starts.
  Selection uses validation WAPE only; an ML model must beat the best baseline by ≥ 2 % or the
  baseline is kept.
- **Intervals are measured, not asserted.** 80 % intervals = validation-residual quantiles per
  forecast week; coverage is reported on the untouched test.
- **Cash risk is labelled honestly.** Output is a *simulated frequency*. With fewer than 20 holdout
  windows the check prints "calibration … NOT established". Every current dataset has 6 windows.

## Measured results (synthetic, 28-day horizon, 12 validation / 6 test folds)

| Dataset / target | Selected | Val WAPE (selected vs best baseline) | Test WAPE | Test 80 % coverage |
|---|---|---|---|---|
| restaurant / revenue | ridge | 0.086 vs 0.101 | 0.088 | 0.85 |
| retail / revenue | ridge (+2.9 %, marginal) | 0.130 vs 0.134 | 0.132 | 0.79 |
| retail / units, 3 imputed closures | weekday_mean_12w | ridge 0.137 vs 0.136 | baseline 0.136 | 0.80 |

XGBoost and CatBoost lost to Ridge on validation for both synthetic businesses. That is expected:
the generator is a smooth seasonal Poisson process. **None of this says anything about real
businesses.**

## Development plan (do not skip ahead)

1. **M1 remainder.** Run the leaderboard on at least one *real* organization-level daily series
   (candidate: UCI Online Retail II, CC BY 4.0, aggregated to daily; needs closure handling).
   Add `is_imputed` as a model feature, plus holiday flags. Report per-horizon error.
2. **M2 — FRED point-in-time.** Add `published_at`/`realtime_start` to `DataProvenance`; fetch
   ALFRED vintages; join as-of the forecast origin; mocked-HTTP tests. Then run an **ablation**
   (internal-only vs + macro) on the same folds. Keep a feature only if validation improves.
   SEC EDGAR stays a separate benchmarking connector.
3. **M3 — decisions on selected forecasts.** Price, supplier and reorder actions vs no-action,
   with uncertainty propagated from the leaderboard error paths, user constraints, and an
   elasticity range rather than a single assumed value. Fix synthetic inventory receipts first.
4. **M4 — agent, alerts, Streamlit**, only after 1–3 pass tests.

---

# Milestone 2 update — external data (2026-11-14)

## FRED / ALFRED (real economic observations): working
- `connectors/fred.py` fetches every vintage (`realtime_start=1776-07-04`, `output_type=1`), records
  `published_at` (first release), units, frequency and retrieval time, and caches raw payloads
  without the key. `macro.py` builds features as of the last observed business day; YoY is computed
  inside the same vintage. Tests poison future revisions and check nothing earlier changes.
- Observed live: median first release 41 days after the observation month starts for CPI, 34 for
  UNRATE, 45 for RSXFS. Revisions matter: RSXFS for Jan 2024 was first published as 605,198 and
  is now 588,944 (−2.7 %). ALFRED shows **no revisions for CUSR0000SEFV**, which is implausible
  for a seasonally adjusted CPI series; treat its vintage history as incomplete.

### Ablation (synthetic business data + real FRED, same 12 validation / 6 test folds)
| Target | Ridge val WAPE internal → +FRED | Ridge test WAPE internal → +FRED | Adopt FRED? |
|---|---|---|---|
| restaurant revenue | 0.0863 → 0.0880 (+1.9 %) | 0.0884 → 0.0914 (+3.4 %) | no |
| restaurant variable_costs | 0.0933 → 0.0956 (+2.4 %) | 0.0934 → 0.0971 (+4.0 %) | no |
| retail revenue | 0.1300 → 0.1320 (+1.5 %) | 0.1317 → 0.1302 (−1.1 %) | no |
| retail units_sold | same as retail revenue (units = revenue / price in synthetic data) | | no |

Interpretation: the synthetic businesses have no economic dependence, so this is the expected
null result and mostly measures the cost of 7 extra features. A positive-control test
(`tests/test_fred_macro.py`) confirms the pipeline does detect and adopt a real known-at-origin
signal. **Whether FRED helps a real business is untested** until real history is loaded.

## Capital One Nessie (synthetic banking records): working, with server limitations
- The first key (`nsk_…`) was rejected on writes by both hosts. A 32-hex key works on
  `https://prod-api.nessieisreal.com` (now in `.env`). GET never validates keys.
- Seeded live (idempotent; the second confirmed run skipped all 45 existing objects and created only
  the 20 that had failed before): 1 customer, 2 accounts, 2 merchants, 32 deposits,
  8 withdrawals, 12 purchases, 4 transfers, 4 bills.
- Import: 56 records, 0 duplicates, 0 date or amount mismatches against the seed plan, so past dates
  are stored as sent. 8 internal-transfer legs net to $0 and are excluded from aggregates.
- Server limitations found: strict create schemas (`purchase_date`, not `transaction_date`);
  transfers accept no payee field and come back with `id` instead of `_id` (this caused a
  silent de-duplication bug, now fixed and tested); listing bills returns 400 once a one-time
  bill exists, so 4 bills are missing from the import and fixed costs are understated by $4,380.
- 28 days of sandbox data is far below the 504 days the leaderboard needs; it demonstrates the
  transaction pipeline only and is not used for model accuracy.
