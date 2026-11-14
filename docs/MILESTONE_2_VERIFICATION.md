# Milestone 2 verification audit — 2026-11-14

Every result below was produced by running the code on 2026-11-14 (Python 3.13.3, pandas 3.1.1,
scikit-learn 1.9.1, XGBoost 3.4.1, CatBoost 1.2.10). Nothing is copied from earlier sessions.
No credentials were printed. The audit made **no writes** to Nessie.

**Data origins used in this audit**
| Label | What it is |
|---|---|
| REAL | FRED/ALFRED published economic observations with vintage dates |
| SYNTHETIC BANKING | Capital One Nessie sandbox records for a fictional restaurant, generated from our synthetic model |
| SYNTHETIC BUSINESS | `make_sample()` / `data/examples/*_SYNTHETIC.csv` restaurant and retail histories |

No real organization data exists in the project. No accuracy claim here applies to a real business.

## Summary

| # | Check | Status |
|---|---|---|
| 1.1 | FRED connection with `.env` credentials | PASS |
| 1.2 | Real historical observations retrieved | PASS |
| 1.3 | Point-in-time vintage retrieval | PASS |
| 1.4 | No future-data leakage / correct publication alignment | PASS |
| 1.5 | FRED data actually used as model features | PASS |
| 1.6 | With vs without FRED on identical windows | PASS |
| 1.7 | Did FRED improve forecasts? | **No** (reported, not a defect) |
| 2.1 | Nessie connection | PASS |
| 2.2 | Sandbox has customers and accounts | PASS |
| 2.3 | Records retrievable (deposits, purchases, withdrawals, transfers, bills) | PARTIAL (bills: server bug) |
| 2.4 | Conversion to canonical schema | PASS |
| 2.5 | Repeated imports do not duplicate | PASS |
| 2.6 | Nessie data reaches the analysis pipeline | PARTIAL (28 days < 56-day minimum) |
| 2.7 | Simulated vs real data clearly labelled | PASS |
| 3.1 | Full automated test suite | PASS (50 offline + 7 live) |
| 3.2 | Linting | PASS |
| 3.3 | Restaurant demo | PASS |
| 3.4 | Retail demo | PASS |
| 3.5 | Model selection uses validation only | PASS |
| 3.6 | Original leakage-prevention tests | PASS |
| 3.7 | Cash risk uses out-of-sample errors; 28-day horizon only | PASS |
| 4.1 | `.env` ignored, no secrets tracked | PASS |
| 4.2 | Credentials never printed | PASS |
| 4.3 | API failures handled gracefully | PASS (after one fix) |
| 4.4 | Uncommitted changes reviewed | PASS |
| 4.5 | Git initialized in the project directory | PASS (initialized during audit, with approval) |

Totals: 23 PASS, 2 PARTIAL, 0 FAIL. Item 1.7 is a finding, not a pass/fail check.

---

## 1. FRED

### 1.1–1.3 Connection, real observations, vintages — PASS
- **Files:** `src/decisiondesk/connectors/fred.py`, `connectors/base.py`
- **Verified by:** `MSRD_LIVE=1 pytest -q -m live` (fresh download into an empty cache) and
  `python scripts/verify_milestone2.py` (live)
- **Result:**
  - CPIAUCSL metadata: Monthly, "Index 1982-1984=100".
  - Vintage rows for CUSR0000SAF11: 247 rows, 69 months (2021-01..2026-10), retrieved
    2026-11-14T10:17:00Z, median first-release lag 41 days, 59 months revised.
  - UNRATE: 103 rows, median lag 34 days, 26 months revised.
  - CUSR0000SEFV: 69 rows, 0 revisions.
  - Release-lag check: no CPI month was published sooner than 28 days after its month began.
- **Limitation:** ALFRED shows **no revisions for CUSR0000SEFV**, which is implausible for a
  seasonally adjusted CPI series. The runtime prints a warning, and its point-in-time history
  should be treated as incomplete.

### 1.4 Leakage and publication alignment — PASS
- **Files:** `src/decisiondesk/macro.py`, `features.py`
- **Verified by:** offline tests `test_snapshot_respects_publication_and_revision_dates`,
  `test_yoy_uses_same_vintage_and_future_revisions_do_not_leak`,
  `test_origin_exog_alignment_uses_previous_day`; live test
  `test_fred_live_snapshots_never_use_unpublished_rows`; and the audit script on real vintages.
- **Result:**
  - The audit recomputed 93 forecast origins with every row published after the origin's
    as-of date deleted. **0 mismatches.** At every origin, the latest observation used had been
    published on or before the as-of date.
  - For comparison, a naive same-month join of revised data would have used an unpublished value
    at **89/93** origins, with a mean food-CPI YoY difference of 0.26 percentage points.
- **Rule:** a forecast issued at origin `o` sees only vintages valid on `dates[o-1]`, and YoY is
  computed inside that same vintage.

### 1.5 FRED data reaches the models — PASS
- **Files:** `features.py` (`exog`), `models.py` (`AnchoredRegressor` uses `x_` columns),
  `leaderboard.py`, `ablation.py`
- **Verified by:** audit script (fits on the full restaurant revenue history); offline
  positive-control test `test_positive_control_exog_signal_is_detected`.
- **Result:**
  - The training matrix is 17,710 × 28 with 7 FRED columns.
  - Ridge: FRED columns carry 5.4 % of standardized |coef|. The largest is `x_unrate_level`
    (−33.0).
  - XGBoost: FRED importance share 18.7 %.
  - Positive control: when the target truly depends on information known at the origin, the
    exogenous arm wins on validation by more than 5 % and is adopted.

### 1.6–1.7 With vs without FRED (live data, identical windows)
- **Command:** `python -m decisiondesk.ablation --industry <restaurant|retail> --target <t>`
- **Windows:** validation 2024-08-12..2025-07-13 (12 × 28-day folds); untouched final test
  2025-07-16..2025-12-30 (6 folds).
- **Data:** SYNTHETIC BUSINESS + REAL FRED.
- **Rule:** decision on validation only. The ML model must lower validation WAPE by at least 2 %.

| Target | Model selected (internal / FRED arm) | Ridge val WAPE internal → FRED | Ridge test WAPE internal → FRED | Selected-model test WAPE internal / FRED arm | Adopt FRED |
|---|---|---|---|---|---|
| restaurant revenue | ridge / ridge | 0.0863 → 0.0880 (+1.9 %) | 0.0884 → 0.0914 (+3.4 %) | 0.0884 / 0.0914 | **No** |
| restaurant variable_costs | ridge / ridge | 0.0933 → 0.0956 (+2.4 %) | 0.0934 → 0.0971 (+4.0 %) | 0.0934 / 0.0971 | **No** |
| retail revenue | ridge / weekday_mean_12w | 0.1300 → 0.1320 (+1.5 %) | 0.1317 → 0.1302 (−1.1 %) | 0.1317 / 0.1362 | **No** |
| retail units_sold | ridge / weekday_mean_12w | 0.1300 → 0.1320 (+1.5 %) | 0.1317 → 0.1302 (−1.1 %) | 0.1317 / 0.1362 | **No** |

XGBoost and CatBoost moved by between −1.6 % and +3.8 % in either direction. Full tables are in
`artifacts/ablation/*.json`.

**Answer: FRED did not improve forecasting performance in any of the four comparisons.** This is
the expected result, because the synthetic businesses were generated with no economic
dependence. The positive control shows the pipeline can detect a real signal. Whether FRED helps
a **real** business remains untested.

---

## 2. Capital One Nessie (SYNTHETIC BANKING)

Host: `https://prod-api.nessieisreal.com`. The sandbox was already seeded with your approval in
the previous session, so no writes were needed or made during this audit.

### 2.1–2.2 Connection, customers, accounts — PASS
- **Files:** `connectors/nessie.py`, `nessie_sandbox.py`
- **Verified by:** live `test_nessie_live_sandbox_populated_and_readable`; audit script.
- **Result:** 1 customer visible (the demo customer "Harbor Lane Bistro MSRD-DEMO"); checking and
  savings accounts; 2 merchants.
- **Caveat:** Nessie GET returns `200 []` for any key, so a GET alone never proves a key works.
  Only writes validate keys, which was confirmed in the previous session.

### 2.3 Records retrievable — PARTIAL
- **Result:**
  - Checking: 28 deposits, 8 withdrawals, 12 purchases, 4 transfers.
  - Savings: 4 deposits.
  - **Bills: unreadable.** `GET /accounts/{id}/bills` returns HTTP 400 ("validation errors for
    Bill: recurring_date, upcoming_payment_date field required").
- **Cause:** a Nessie server defect. It accepted the bills but cannot serialize them. Bill ids
  were not echoed on create, so the bills cannot be read or deleted individually.
- **Impact:** rent, linen and utilities ($4,380) are missing from imports.
- **Guard:** `init` never creates records it cannot first list (`test_unreadable_list_blocks_blind_creation_and_is_reported`).

### 2.4 Canonical conversion — PASS
- **Files:** `src/decisiondesk/transactions.py`
- **Verified by:** offline `test_import_signs_transfers_and_round_trip`,
  `test_duplicates_and_bill_status_handling`, `test_records_without_underscore_id_are_not_merged`;
  live `test_nessie_live_repeated_import_is_stable_and_canonical`.
- **Result:**
  - 56 records → 56 canonical rows with the 17 `TRANSACTION_COLUMNS`.
  - Signs: deposits +; withdrawals, purchases and transfers −.
  - 8 internal-transfer legs net to $0 and are excluded from revenue and cost.
  - Every row has `data_origin = SYNTHETIC_SANDBOX_NESSIE`.
  - The previous session's round trip against the seed plan found 0 date and 0 amount
    mismatches.

### 2.5 Repeated imports — PASS
- **Result:** two consecutive live imports gave 56 rows each, with identical transaction ids and
  0 duplicates dropped. The live test asserts the two frames are equal.

### 2.6 Reaches the analysis pipeline — PARTIAL
- **Verified by:** audit script (`transactions_to_daily` → `validate_daily_frame`).
- **Result:** a canonical daily cash-basis frame is built (28 days, revenue $56,563). Ingestion
  then correctly **rejects** it: "At least 56 daily observations are required for the baseline."
  The leaderboard would need 504 days.
- **To make this PASS:** seed at least 56 days (baseline / what-if path) or 504 days
  (leaderboard). That requires new sandbox writes, which need your approval.

### Additional finding: Nessie balances are static
- Reported balances stay at 15,000 (checking) and 5,000 (savings).
- Opening balance plus imported transactions gives 30,004 and 7,000.
- Nessie does not apply transactions to `balance`, so that field must not be used as a cash
  figure. Our cash position comes from transactions.

### 2.7 Labelling — PASS
- Every canonical row, output filename (`*_SYNTHETIC.csv`) and CLI banner states
  SYNTHETIC_SANDBOX_NESSIE.
- The README has a data-origin table.

---

## 3. Machine-learning integrity

| Check | Command | Result |
|---|---|---|
| Offline suite | `pytest -q` | **50 passed, 7 skipped** (skips = opt-in live tests) |
| Live suite | `MSRD_LIVE=1 pytest -q -m live` | **7 passed** (read-only) |
| Lint | `ruff check src tests scripts` | All checks passed |
| Restaurant demo | `python -m decisiondesk.demo --industry restaurant` | exit 0; revenue: ridge selected (+14.3 % validation WAPE vs weekday baseline); net cash ridge (test WAPE 0.459, a poor metric for net cash, see EVALUATION.md); horizon 28 days |
| Retail demo | `python -m decisiondesk.demo --industry retail` | exit 0; ridge selected (+2.9 %, marginal); net cash test WAPE 0.407; horizon 28 days |

- **3.5 Selection uses validation only — PASS.**
  - In code, `select_model` receives only validation WAPE (`leaderboard.py`).
  - Offline: `test_selection_falls_back_to_baseline`, `test_fold_plan_isolates_holdout`.
  - Empirical check (audit script): the final-test revenue was permuted and tripled. The selected
    model stayed `ridge`, all validation metrics were identical, and test metrics changed.
- **3.6 Original leakage tests — PASS.** `test_features_ignore_data_at_or_after_origin`,
  `test_training_rows_only_use_targets_before_cutoff`,
  `test_fitted_models_do_not_see_future[ridge|xgboost|catboost]` and
  `test_forecast_uses_only_prior_data` all pass.
- **3.7 Cash risk — PASS.**
  - The audit re-derived the 12 × 28 validation residual matrix from models refit before each
    cutoff, and it is identical to what `backtested_cash_risk` uses.
  - The holdout calibration check reports 6 windows (< 20), so calibration is "NOT
    established". The demos print this.
  - The horizon is 28 days. No 90-day claim exists in the code or README (checked with grep).

---

## 4. Security and repository

- **4.1 PASS.**
  - `git check-ignore` confirms `.env`, `data/cache/`, `artifacts/` and `.venv/` are ignored.
  - `.env` was never committed in the enclosing home repository (0 log entries).
  - A secret scan of all 49 files to be committed found no `.env` key values, no `nsk_…` tokens
    and no 32-hex key-like strings.
- **4.2 PASS.**
  - Keys are passed only as query parameters inside `request_json`, whose errors are rebuilt
    with keys redacted.
  - Offline tests: `test_errors_never_leak_key` (FRED and Nessie).
  - Live test: `test_fred_live_invalid_key_is_graceful_and_redacted`.
  - Raw FRED caches are checked to contain no key.
- **4.3 PASS after a fix.**
  - Blank FRED key on `ablation` previously produced a **traceback**. It is now a one-line error
    suggesting `--offline` (regression test `test_ablation_cli_missing_key_exits_cleanly`).
  - Blank Nessie key → `error: NESSIE_API_KEY not configured`.
  - Unreachable host → `error: GET https://nessie.invalid/customers failed: ConnectionError`.
  - The demo runs with no keys in the environment.
- **4.4 PASS.** Before the audit, the project was entirely untracked (`?? ./`) in the
  home-directory repository. That repository's 41 staged changes belong to other projects and
  were not touched.
- **4.5 PASS.** With your approval, `git init -b main` was run in
  `~/Desktop/main-street-decision-intelligence`, followed by one local initial commit. Nothing
  was pushed.

---

## Test inventory: mocked vs live

| File | Type | Tests | Covers |
|---|---|---|---|
| `tests/test_core.py` | offline | 8 | starter behaviour, CSV validation |
| `tests/test_models.py` | offline | 12 | leakage, folds, selection, intervals, leaderboard |
| `tests/test_ingestion_risk.py` | offline | 9 | ingestion policy, cash-risk mechanics/calibration |
| `tests/test_fred_macro.py` | **mocked HTTP** | 9 | FRED pagination/cache/redaction, point-in-time, positive control, CLI errors |
| `tests/test_nessie.py` | **mocked HTTP** | 12 | seeding idempotency, observed prod schema, transfers, duplicates, unreadable lists |
| `tests/test_live_integrations.py` | **LIVE, read-only** | 7 | real FRED vintages and errors; real Nessie reads, re-import stability, bad host |
| `scripts/verify_milestone2.py` | **LIVE, read-only** | 15 checks | leakage on real vintages, feature use, selection independence, residual provenance, Nessie pipeline reach |

## Fixes made during this audit
1. `ablation.py`: a missing or invalid FRED credential now exits with a message instead of a
   traceback (plus a test).
2. `test_connections.py`: the Nessie GET now prints "REACHABLE … does NOT prove the key is valid"
   instead of "SUCCESS".
3. New opt-in live test suite (`pytest -m live`) and the read-only audit script.
4. `.gitignore`: added `.Rhistory`. Git initialized in the project directory.

## Remaining limitations
- No real organization data. All forecast accuracy is synthetic, and FRED's value for real
  businesses is untested.
- Nessie: bills unreadable (server defect), balances static, transfers cannot name a payee, and
  the 28-day history is too short for the analysis pipeline.
- CUSR0000SEFV vintage history looks incomplete (no revisions).
- Cash-risk calibration is not established (6 holdout windows).

## Recommendation

**READY FOR MILESTONE 3: YES**

No required check failed. The two PARTIAL items are:
- (a) a Nessie server defect outside our code;
- (b) the sandbox holding 28 days, short of the 56-day minimum.

Neither blocks Milestone 3. The decision engine runs on the validated forecasting pipeline,
which passed every integrity check here. Nessie data must not be presented as an analysis input
in the Milestone 3 demo until (b) is resolved. Resolving it means seeding at least 56 days of
sandbox history, which needs your approval for new Nessie writes.
