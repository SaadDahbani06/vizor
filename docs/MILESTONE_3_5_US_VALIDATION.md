# Milestone 3.5 — Real U.S. small-business data and external intelligence (2026-11-14)

Every number below comes from commands run on 2026-11-14 in this repository. Each accuracy
figure is labelled with the kind of data it was measured on, and **categories are never pooled**:

| Category | Meaning |
|---|---|
| **REAL_AGGREGATE_INDUSTRY** | Real published aggregate of many businesses (Kwick365) |
| **REAL_INDIVIDUAL_BUSINESS_PURCHASES** | One real store's purchase records (Iowa dataset 1051) |
| **SYNTHETIC** | Our generated restaurant/retail histories (Milestones 1–3) |

**No real individual-business *sales* history was found or used.** Accuracy on a real
individual business therefore remains **unestablished**.

---

## 1. API integration inventory (audited in code, not inferred from keys)

| Source | Connected? | Endpoints implemented | Retrieves | Reaches models? | Reaches recommendations? | Nature |
|---|---|---|---|---|---|---|
| **FRED / ALFRED** | Yes: live-tested (this and earlier sessions) | `fred/series`; `fred/series/observations` (latest, and full vintages with `realtime_start=1776-07-04`, `output_type=1`, paginated) | CPI (all items, food at home, food away from home, alcoholic beverages), UNRATE, IAUR, RSXFS; with units, frequency, first-release dates, vintages | **Only in ablation arms.** Every validation comparison so far rejected the features, so production forecasts are internal-only | Yes, as **observed context** (as of the forecast origin) and to size **hypothetical** stress tests | Real published economic data |
| **Kwick365** | Yes: live-tested (new) | `GET /api/v1/reading/<reading>/?geo=<geo>` (10 readings; geo = us, states, metros) | Daily median ticket, orders per restaurant, tips etc., with `n_stores` and `n` per day | **As a forecast target** in real-data validation. Not a model input: it has no vintages, and survivorship recomputation means history changes | Yes, as benchmark **context** and a hypothetical demand stress in the restaurant demo | Real aggregate (KwickPOS independent restaurants) |
| **Iowa Liquor Sales (1051)** | **No live access**: no query API exists. Importer for the official export is built and tested on a schema fixture | None (the legacy Socrata `resource/m3tr-qhgy` returns 404; the Data Hub provides whole-dataset CSV/JSON downloads only) | Store-level spirits **purchases** by invoice line | Not yet. It will be the forecast target once the export is downloaded | Procurement-reserve decisions, once data is present | Real individual-store purchases (not sales) |
| **Capital One Nessie** | Yes: live read + write on `prod-api` (earlier sessions, with approval) | GET/POST customers, accounts, merchants, deposits, withdrawals, purchases, transfers, bills | 56 sandbox records for a fictional restaurant | **No** (28 days < 56-day minimum) | **No** | Simulated |
| **SEC EDGAR** | **Not connected.** A raw client exists but has never been run (no `SEC_USER_AGENT` configured) | `data.sec.gov/api/xbrl/companyfacts/CIK##########.json` (code only) | Unmapped XBRL facts | No | No | Real public-company data (not small-business data) |
| BLS, EIA, USDA ERS | Not implemented | — | — | No | No | Documented official sources only (see `src/decisiondesk/sources.py`) |

The machine-readable version is `decisiondesk.sources.CATALOG`. `select_sources()` returns only
the sources relevant to a business's industry and requested analysis, with a literal status, and
never selects a source just because a key exists.

## 2. Real U.S. data sources: verification, schema, licensing

### Kwick365 — The Independent Restaurant Index (https://kwick365.com)

- **Verified on 2026-11-14** from its `/terms/` and `/methodology/` pages and its API.
- **Producer and sample:** published by Jinstech Corp dba KwickPOS, Houston, TX. It covers
  **1,923 independent restaurants running KwickPOS**, concentrated in TX, GA, FL, CA and NY.
  This is **not a probability sample** of American restaurants, and its levels do not generalise.
- **API:** `{metric, geo, unit, value2_unit, n_unit, rows:[{day, value, value2, n_stores, n}],
  note, source}`. It is open, needs no key, and returns at most **366 days** (`range` is ignored).
  The site itself has 531 days, back to 2025-06-01; the older days are not reachable through
  the API, and we do not scrape them.
- **Publication thresholds:** a day publishes only with ≥ 30 restaurants and ≥ 500 orders. The
  live test confirms every returned day meets this. Withheld days are absent, never zero.
- **Revisions:**
  - **survivorship:** history is recomputed nightly over *today's* surviving restaurants;
  - **the last 3 days** are recomputed nightly, and we drop them;
  - **no vintages exist.**

  We therefore use Kwick365 as a forecasting *target* (as currently published) and as context,
  never as a historical model input.
- **Terms:** free to quote and build on (commercial use included) with a link. Please carry the
  date and sample size, and describe the sample as "independent restaurants running KwickPOS".
  Be gentle with the API: we cache and use `cache_first`. Citation: "Kwick365 — The Independent
  Restaurant Index, https://kwick365.com".
- **Data used:**
  - `online-orders-per-store` and `instore-orders-per-store`, geo `us`;
  - published 2025-11-13..2026-11-13; evaluated on **2025-11-13..2026-11-10 (363 days)** after
    dropping the 3 unstable days;
  - no withheld days in the window;
  - median sample per day: 761 restaurants and 9,638 orders (online); 1,534 restaurants and
    131,853 orders (in-store);
  - retrieved 2026-11-14T15:14Z.

### Iowa Liquor Sales, January 2012 – Current (https://data.iowa.gov/catalog/dataset/1051)

- **Publisher:** Iowa Department of Revenue, Alcohol & Tax Operations Division.
- **Licence:** **CC BY 4.0**. Updated monthly; 35,287,731 rows; coverage 2012-01-03..2026-10-30;
  last updated 2026-11-06.
- **What a row is:** an invoice line in which an Iowa Class E licensee (grocery, liquor or
  convenience store) **buys** spirits from the state. `sales_dollars` is the store's
  **procurement spend**, not customer sales. There is no inventory or demand column.
- **Schema** (from the dataset's published transform SQL): `invoice_id, ordered_on, store_no,
  store_name, store_address, store_city, store_zip_code, county_fips_code, county_name,
  category_code, category_name, vendor_number, vendor_name, item_no, im_desc, pack,
  bottle_volume_ml, state_bottle_cost, state_bottle_retail, sales_bottles, sales_dollars,
  sales_liters, sales_gallons`.
- **Access:**
  - the legacy query API returns 404;
  - the Data Hub's Download dialog offers whole-dataset CSV/JSON (possibly several files),
    through a server-generated signed URL. We do not call that internal mechanism directly.
- **Status: BLOCKED on a manual download.**
  `src/decisiondesk/connectors/iowa_liquor.py` reads the export, then:
  - selects one store and aggregates its daily purchases;
  - fills no-invoice days with true zeros and flags them;
  - drops duplicate invoice lines;
  - checks **store identity** (name, address and city over time), history length (≥ 3 years),
    the longest run without purchases (≤ 35 days), and whether the store is still active.

  All of this is tested on a test-only fixture written in the official schema. No real store has
  been evaluated yet.

### FRED / ALFRED (real published economic data)

| Series | Use | Units | Median first-release lag | Months revised (2021+) |
|---|---|---|---|---|
| CUSR0000SAF11 food at home CPI | restaurant input costs | Index 1982-84=100 | 41 d | 59 |
| CUSR0000SEFV food away from home CPI | menu-price inflation | Index | 41 d | 0 (ALFRED shows no revisions; flagged) |
| UNRATE | demand context | % | 34 d | 26 |
| CUSR0000SAF116 alcoholic beverages CPI | Iowa wholesale-price stress | Index | 41 d | 48 |
| IAUR Iowa unemployment | Iowa context | % | 50 d | 57 |

**New real-world data issue found:** **October 2025 was never published** for any of these
series (the federal government shutdown). The original code required the exact year-earlier
month, so every origin after October 2026 lost its YoY value. That **silently disabled the
restaurant cost stress test**. Fixed:
- YoY and 3-month changes now use the newest observation whose comparison month exists *in
  the same vintage*. Nothing is imputed (`test_unpublished_month_is_skipped_not_imputed`).
- Reports state which month was compared.

## 3. Upload architecture (user-confirmed profile → mapping → capability detection)

`src/decisiondesk/profile.py`:
- **`BusinessProfile`:** industry, two-letter U.S. state, objective, constraints and currency.
  Analysis refuses to run unless `confirmed_by_user` is set; the CLI requires `--confirm`.
- **`ColumnMapping`:** maps uploaded columns from CSV or Excel to roles: date, revenue,
  variable/fixed costs, units sold, inventory, purchase dollars/units, net cash flow, plus the
  business id and currency as metadata. Roles are validated, one column per role, and a date is
  required.
- **`capabilities()`:** lists the supported and unsupported analyses with the missing roles
  named. It never creates a metric for an unmapped role. For example, a purchases-only upload
  supports a procurement forecast and procurement budget, while inventory, demand, revenue and
  cash risk are reported as unsupported, with the note "Purchases are not customer demand".
  Forecasting needs ≥ 504 days.
- **Financial uploads** still go through the Milestone 1 validation unchanged.

## 4. Training and testing procedure (identical to Milestones 1–2, sized to the data)

| Item | Kwick365 (real aggregate) | Iowa store (real purchases) | Synthetic |
|---|---|---|---|
| Horizon / step | 14 / 14 days | 28 / 28 | 28 / 28 |
| Untouched final test | last 4 windows (2026-09-16..2026-11-10) | last 6 windows | last 6 windows |
| Validation | 9 windows (2026-04-30..2026-09-02), all ending before the test | ≥ 4 windows | 12 windows |
| Minimum training | 168 days | 224 | 224 |

- **Models:** seasonal naive; weekday mean (12 weeks); **historical average (trailing 28-day
  mean, new)**; Ridge; XGBoost; CatBoost. Hyperparameters are fixed and untuned.
- **Fitting:** each fold refits on data before its cutoff. Features use only `y[:origin]`, plus
  calendar facts about the target day.
- **Selection:** by validation WAPE only. An ML model must beat the best baseline by ≥ 2 %.
- **Intervals:** 80 % intervals from validation residuals; coverage is measured on the test.
- **External variables:** point-in-time FRED features (as of the last observed day, YoY within
  the same vintage), on the **same folds**. Adopted only if they lower validation WAPE by ≥ 2 %.
- Kwick365 is shorter than the standard 504-day design needs, hence the 14-day horizon.

## 5. Results

### 5.1 REAL_AGGREGATE_INDUSTRY: Kwick365 online orders per active restaurant (US)
| Model | Val MAE | Val RMSE | Val WAPE | Test MAE | Test RMSE | Test WAPE | Test 80 % coverage |
|---|---|---|---|---|---|---|---|
| seasonal_naive | 0.569 | 0.908 | 0.0441 | 0.420 | 0.557 | 0.0298 | 0.80 |
| **weekday_mean_12w (selected)** | 0.439 | 0.666 | **0.0340** | 0.864 | 0.965 | **0.0614** | **0.38** |
| historical_mean_28d | 1.163 | 1.340 | 0.0902 | 1.280 | 1.531 | 0.0909 | 0.80 |
| ridge | 0.492 | 0.709 | 0.0381 | 0.558 | 0.687 | 0.0396 | 0.71 |
| xgboost | 0.614 | 0.862 | 0.0476 | 0.331 | 0.484 | 0.0235 | 0.93 |
| catboost | 0.563 | 0.785 | 0.0436 | 0.367 | 0.519 | 0.0260 | 0.88 |

**Finding:**
- The validation winner **degraded sharply on the untouched test**: WAPE nearly doubled, and
  interval coverage was 38 % against a nominal 80 %.
- Models that lost on validation (XGBoost, seasonal naive) did better on the test. The series
  shifted level in Sep–Nov 2026 (test bias −0.84 orders per restaurant for the selected model).
- We report this rather than re-select on the test. The benchmark forecast used in the
  restaurant demo is flagged **LOW reliability**.

### 5.2 REAL_AGGREGATE_INDUSTRY: Kwick365 in-store orders per restaurant (US, median)
| Model | Val MAE | Val RMSE | Val WAPE | Test MAE | Test RMSE | Test WAPE | Test 80 % coverage |
|---|---|---|---|---|---|---|---|
| seasonal_naive | 2.583 | 4.552 | 0.0377 | 1.464 | 2.107 | 0.0217 | 0.89 |
| **weekday_mean_12w (selected)** | 2.328 | 3.708 | **0.0339** | 1.601 | 2.096 | **0.0238** | **0.84** |
| historical_mean_28d | 6.400 | 7.450 | 0.0933 | 7.298 | 8.458 | 0.1083 | 0.70 |
| ridge | 2.610 | 3.921 | 0.0381 | 1.281 | 1.853 | 0.0190 | 0.91 |
| xgboost | 2.529 | 3.975 | 0.0369 | 1.716 | 2.443 | 0.0255 | 0.86 |
| catboost | 2.401 | 3.955 | 0.0350 | 1.480 | 2.085 | 0.0220 | 0.95 |

The selected baseline generalised here (test WAPE 0.024, coverage 84 %).

### 5.3 With vs without external data (FRED point-in-time; identical windows)
FRED series: CUSR0000SAF11, CUSR0000SEFV, UNRATE.

| Target | Model | Val WAPE internal → +FRED | Test WAPE internal → +FRED |
|---|---|---|---|
| online orders | ridge | 0.0381 → 0.0374 (−1.7 %) | 0.0396 → 0.0329 (−17.0 %) |
| online orders | xgboost | 0.0476 → 0.0474 (−0.5 %) | 0.0235 → 0.0252 (+7.3 %) |
| online orders | catboost | 0.0436 → 0.0459 (+5.1 %) | 0.0260 → 0.0243 (−6.8 %) |
| in-store orders | ridge | 0.0381 → 0.0373 (−2.0 %) | 0.0190 → 0.0220 (+15.6 %) |
| in-store orders | xgboost | 0.0369 → 0.0381 (+3.3 %) | 0.0255 → 0.0230 (−9.8 %) |
| in-store orders | catboost | 0.0350 → 0.0371 (+6.0 %) | 0.0220 → 0.0220 (+0.4 %) |

- **Decision (validation only):** both arms select the weekday-mean baseline, which ignores
  external features, so **FRED features are not adopted**.
- The effects are small and **flip sign between validation and test**. That is consistent with
  noise from 9 validation and 4 test windows, not with a usable signal.
- **Conclusion:** FRED did **not** demonstrably improve forecasts of real aggregate restaurant
  order volume.

### 5.4 REAL_INDIVIDUAL_BUSINESS_PURCHASES: Iowa store — **not run** (data not downloaded)
### 5.5 SYNTHETIC (Milestone 1, unchanged)
Restaurant revenue: Ridge, test WAPE 0.088. Retail: Ridge, test WAPE 0.132. Details are in
`docs/ARCHITECTURE_REVIEW.md`. Adding the historical-average baseline changed no selection
(restaurant revenue: historical mean validation WAPE 0.169).

## 6. External events → decisions (restaurant, end to end)
`python -m decisiondesk.us_demo restaurant --state TX --confirm` (exit 0):

1. **Internal indicators (SYNTHETIC):**
   - The restaurant history is re-dated to end **2026-11-10**, the last stable Kwick365 day, so
     every external value is as of the same date.
   - Last 28 days: revenue $56,183; variable costs 40.1 % of revenue; average ticket $19.00.
2. **External observations (REAL, as of 2026-11-10):**
   - Food-at-home CPI: **+1.77 %** YoY (2026-09; the Oct-2026 release was published 2026-11-10).
   - Food-away-from-home CPI: **+3.46 %**.
   - Unemployment: **−0.20 pp** (4.1 %).
   - Kwick365 online ticket: $30.51 on 2026-11-10 (738 restaurants, 9,858 orders).
   - Kwick365 window means, last 28 days vs first 28 days: online ticket −1.4 %, in-store
     ticket +0.9 %, online orders per restaurant +18.1 %.
3. **Forecasts:**
   - The business forecast uses its selected models (Ridge revenue and variable costs,
     seasonal-naive fixed costs).
   - The benchmark forecast (weekday mean) is **−5.7 %** orders for the next 14 days, marked
     **LOW reliability** (test coverage 38 %).
4. **Exposure:**
   - Input-cost pressure: food-at-home CPI +1.77 %.
   - Menu-price inflation (food-away-from-home CPI +3.46 %) is used to **size a candidate
     action**: raise prices 3.5 %.
5–7. **Milestone 3 engine** (maximize expected 28-day profit; constraints: max price +5 %, $1,000
   implementation budget):

| Rank | Action | Δ profit vs no action (90 % range) | P(better) |
|---|---|---|---|
| 1 | Raise menu prices 3.5 % (sized to observed FAFH CPI) | +$887 (+$560..+$1,226) | 100 % |
| 2 | Raise menu prices 3 % | +$742 | 100 % |
| 3 | Cut discretionary spending 30 % | +$583 | 100 % |
| 4 | Negotiate 4 % lower supplier prices | +$105 (−$250..+$378) | 59 % |
| — | Raise menu prices 8 % | excluded (> 5 % limit) | |

- **Recommendation:** raise prices 3.5 %, rated **assumption-sensitive**. If price elasticity
  is −1.2 instead of the assumed −0.8 (range −1.2..−0.4), the discretionary cut ranks first.
- **Hypothetical food-cost stress** (variable costs +1.77 %): the top action is unchanged and
  rated robust; baseline profit falls to $5,637.
- **Hypothetical benchmark-demand stress** (sales −5.7 %): the top action is unchanged and rated
  robust. **No relationship between this restaurant and the benchmark is estimated.**
- **Assumptions:**
  - elasticity −0.8 (−1.2..−0.4), supplier share 0.85, supplier success 0.4–0.8, discretionary
    share 0.10, demand loss 0–1.5 % — all *default_needs_confirmation*;
  - the effects are **modeled assumptions, not causal estimates**.

**Retail (Iowa store):** `python -m decisiondesk.us_demo retail --store <no> --max-reserve <USD>
--confirm`.
- **Currently prints BLOCKED**, with download steps and a schema-based capability report:
  procurement forecast and budget are supported; inventory, demand, revenue and cash risk are
  unsupported.
- **With data**, it would rank: keep last period's budget (no action) vs reserves at the P50 /
  P80 / P95 of forecast 28-day spend.
  - Objective: minimise the chance of overrunning the reserve, under the user-declared cash
    limit.
  - Stress: a hypothetical wholesale price change equal to observed alcohol-CPI YoY.
  - Pipeline tested end to end on a schema fixture
    (`test_procurement_pipeline_end_to_end_on_fixture`), plus a hand-calculated check.
- **Inventory/ordering policies are reported as unsupported:** the data contains purchases, not
  demand or inventory.

## 7. Tests and commands
```
pytest -q                                  -> 85 passed, 7 skipped   (offline/mocked)
MSRD_LIVE=1 pytest -q -m live              -> 8 passed               (live read-only, incl. Kwick365)
ruff check src tests scripts               -> All checks passed!
python scripts/verify_milestone2.py --offline -> PASS 10, PARTIAL 0, FAIL 0
```
New offline tests (`tests/test_us_data.py`, 11; plus 1 in `test_fred_macro.py`):
- Kwick365 parsing, provenance, cache and cache-first, never filling withheld days, dropping the
  3 unstable days;
- Iowa importer on an official-schema fixture: zero-fill flags, duplicate invoices, identity
  change, short history, rejecting a non-official schema;
- the procurement pipeline end to end, plus a hand-calculated shortage/shortfall check;
- profile confirmation and state validation;
- Excel mapping, with capability detection that never invents metrics;
- source-selection honesty;
- FRED point-in-time observation;
- shutdown-month handling.

Reproduce every result:
```
python -m decisiondesk.us_validation kwick365            # live API (cached); writes artifacts/us_validation/*.json
python -m decisiondesk.us_validation kwick365 --offline  # from cache
python -m decisiondesk.us_demo restaurant --state TX --confirm
python -m decisiondesk.us_demo retail --confirm          # BLOCKED until the Iowa export is present
python -m decisiondesk.us_validation iowa --scan         # after downloading the export
python -m decisiondesk.us_validation iowa --store <no>
python -m decisiondesk.us_demo retail --store <no> --max-reserve <USD> --confirm
```

## 8. Defects found and fixed in this milestone
1. **October 2025 never published** (shutdown) → missing YoY → the food-cost stress test was
   silently skipped. Fixed with the same-vintage fallback, and skipping is now announced.
2. **Kwick365 connector kept a revisable day:** it dropped 2 trailing days instead of 3.
3. **Price action labels rounded** 3.5 % to "4 %". Now exact (`raise_prices_3_5pct`).
4. **Unemployment change shown as % of a rate;** now shown in percentage points.
5. **Ablation message** was confusing when both arms pick the same baseline; now explicit.

## 9. Limitations and unsupported claims
- **No accuracy on a real individual business's sales** has been established. No suitable public
  individual-business sales history was found. Kwick365 is aggregate. Iowa records are
  purchases, not sales, and have not yet been downloaded.
- **Kwick365:**
  - 366 days via the API only;
  - survivorship-recomputed history;
  - not a probability sample, so levels must not be generalised to "American restaurants";
  - the backtests evaluate the history as currently published.
- **Real-data evaluation is small:** 9 validation and 4 test windows of 14 days. Differences of
  a few percent are within noise.
- **The restaurant decision uses SYNTHETIC business records.** Its external context is real,
  but its action effects are assumptions. Nothing here establishes causal effectiveness.
- **No correlation-based claims:** the stress tests are hypothetical; no pass-through or
  benchmark-to-business relationship has been estimated.
- **Not implemented** (not needed for the first demos): SEC EDGAR mapping, BLS, EIA, USDA ERS.
- **Nessie** remains simulated and is excluded from analysis. No external writes were made in
  this milestone.

## Recommendation
**READY FOR MILESTONE 4: NO, not yet.**
- The real-world aggregate pipeline (Kwick365 + FRED → validation → decision engine) is verified.
- But the retail real-data path cannot be verified until the Iowa export is downloaded.

**Required to finish Milestone 3.5:**
1. Place the dataset-1051 CSV export in `data/private/iowa_liquor/`.
2. Run `us_validation iowa --scan`, then `--store <no>` on a store that passes the suitability
   checks, and record the REAL_INDIVIDUAL_BUSINESS_PURCHASES results here.

**Optional:** any genuine individual-business sales history (with permission) would allow
individual-business accuracy to be measured.
