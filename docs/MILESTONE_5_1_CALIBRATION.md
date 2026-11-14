# Milestone 5.1: Monte Carlo uncertainty calibration (2026-11-14)

Branch `milestone-5.1-calibration`, from `milestone-5-dashboard`. Every number below was produced
by running the code on 2026-11-14 on the **synthetic** demo businesses. These results check the
method; they say nothing about calibration on a real business.

## The problem

**How Milestone 5 built ranges.** The Monte Carlo simulator built each simulated 28-day future
by adding historical out-of-sample forecast errors to the forecast. Each **7-day block** came
from a randomly chosen past window, chosen independently for every week.

**Why that was wrong.** Forecast errors are correlated **across all weeks** of a window. The
correlation of weekly error sums within a window, by lag, is:

| Quantity | 1 week | 2 weeks | 3 weeks |
|---|---|---|---|
| Revenue | +0.32 | +0.27 | +0.37 |
| Net cash flow | +0.34 | +0.27 | +0.39 |
| Taco demand | +0.40 | +0.32 | +0.46 |

The correlation does not fade with distance. A forecast is often off by a level for the whole
month. Mixing weeks from different months cancels those offsets, so ranges for multi-week
**totals** were too narrow. The real spread of 28-day revenue errors is $6,585 (standard
deviation); the weekly-block simulation gave $4,554.

## The fix

**Each simulated path now replays one whole historical out-of-sample error window**
(`decision/baseline.py` `make_draws(block=None)`, and `risk.py` `_block_bootstrap(block=None)`).

- **Horizons.** 7- and 14-day horizons use the first 7 or 14 days of the same windows (tested).
- **Products and costs move together.** All products and the fixed costs share the window of a
  path, so they keep moving together (tested).
- **Common random numbers are unchanged.** The plan and every option use the same paths, and the
  assumption draws keep their per-name random streams. A zero-change scenario still equals the
  baseline exactly (tested).
- **The old method is kept.** It is still available as `block=7`, for comparisons and tests.
- **Forecasts, formulas and models are untouched.** Point forecasts, models, financial formulas,
  product analytics, Gemini, Photon and the dashboard features are unchanged. Only the error
  sampling and the labels changed.

## How the method was validated

**The harness** (`src/decisiondesk/calibration.py`, `python -m decisiondesk.calibration`):

- **Chronological, out of sample.** The 31 error windows (25 validation, then 6 final-test,
  non-overlapping) are judged in time order. Window *k* is judged by a range built **only from
  windows 0…k−1**, with at least 8 past windows. That gives 23 evaluated windows. A test checks
  that later windows can never change an earlier verdict.
- **A second view.** The 6 final-test windows are judged by ranges built from the 25 validation
  windows only.
- **Daily and total coverage are reported separately.** Daily is the share of single days inside
  their daily 80% range. Total is the share of windows whose 7-, 14- or 28-day **total** fell
  inside the 80% range for totals.
- **Sharpness and bias.** Interval width, the interval score (a proper scoring rule) and the mean
  error (bias) are also reported, plus a Wilson 95% interval for each coverage estimate.
- **What is actually evaluated.** The error-sampling method itself: what `ForecastBundle.simulate`
  and `simulate_cash_paths` do.

### Choosing the method, including what didn't work

1. **The candidates:** weekly blocks (old), 14-day blocks, whole windows from all past windows,
   and whole windows from only the most recent 8, 12, 16 or 20 windows.
2. **The first rule, and why it was revised.** I first proposed choosing the recent-window pool
   size by 28-day total coverage on the **validation period only**, which pointed to 12 windows
   (79%). Including daily coverage, which the dashboard also shows, recent-12 under-covered days
   (70%). That is the small-sample effect of reading an 80% range from 12 values, which covers
   about 0.8 × 11/13 ≈ 68% on average.
3. **Pool sizes were not tuned.** Across pool sizes the results were not monotone (combined
   calibration error 2.5 / 7.0 / 4.2 / 4.5 points for 8 / 12 / 16 / 20 windows), which looks like
   noise.
4. **Why not 14-day blocks.** They scored well on the validation period (3.3 points) but **cut a
   correlation that exists**. Their coverage was right only because two errors cancelled: narrower
   totals, and larger early errors in the pool.
5. **The choice:** whole windows from **all** past windows. It has the right dependence structure
   and no setting to tune. Its known bias is caution, explained below.

Validation-period combined numbers, across 10 related quantities (they are not independent
evidence):

| Method | 7-day totals | 14-day totals | 28-day totals | Daily coverage |
|---|---|---|---|---|
| Weekly blocks (old) | 78% | **67%** | **68%** | 75–77% |
| 14-day blocks | 78% | 78% | 82% | 75–77% |
| **Whole windows, all (new)** | 78% | 78% | **90%** | 75–77% |
| Whole windows, recent 12 | 74% | 74% | 79% | 70–71% |

## Results: old vs new, per quantity

**Expanding scheme (23 evaluated windows each):** the count of past windows whose total fell
inside the 80% range.

| Quantity | 7-day totals old → new | 14-day totals old → new | 28-day totals old → new | 28-day range width old → new | daily coverage old → new |
|---|---|---|---|---|---|
| Revenue | 19/23 → **19/23** | 17/23 → **18/23** | 15/23 → **22/23** | 11,164 → 18,800 | 77% → 77% |
| Guests (units sold) | 20/23 → **20/23** | 17/23 → **19/23** | 17/23 → **21/23** | 476 → 796 | 79% → 79% |
| Net cash flow | 19/23 → **19/23** | 18/23 → **19/23** | 16/23 → **20/23** | 7,526 → 12,871 | 76% → 76% |
| Taco demand | 18/23 → **18/23** | 16/23 → **19/23** | 16/23 → **20/23** | 244 → 402 | 77% → 77% |
| Burrito demand | 18/23 → **18/23** | 14/23 → **19/23** | 18/23 → **20/23** | 208 → 294 | 78% → 78% |
| Quesadilla demand | 19/23 → **19/23** | 21/23 → **21/23** | 20/23 → **23/23** | 130 → 200 | 79% → 79% |
| Nacho demand | 17/23 → **17/23** | 15/23 → **19/23** | 14/23 → **20/23** | 126 → 165 | 77% → 77% |
| Drink demand | 20/23 → **20/23** | 18/23 → **20/23** | 17/23 → **20/23** | 540 → 799 | 78% → 77% |
| Scenario Lab revenue (tracked) | 20/23 → **20/23** | 18/23 → **19/23** | 17/23 → **21/23** | 9,386 → 15,360 | 78% → 77% |
| Scenario Lab profit (tracked − fixed) | 20/23 → **20/23** | 18/23 → **19/23** | 18/23 → **20/23** | 6,738 → 10,900 | 78% → 78% |

All 28-day rows together: old **168/230 (73%)**, new **207/230 (90%)**.

**Final test** (6 windows, ranges from validation windows only), 28-day totals, old → new:
- revenue 5/6 → 6/6;
- net cash flow 5/6 → 5/6;
- quesadillas 4/6 → 6/6 and nachos 4/6 → 6/6;
- every other quantity 6/6 → 6/6.

**Mean 28-day error (actual − forecast):** revenue +$1,638, net cash flow +$952, taco demand
−3 units. Errors are not demeaned, so this small under-forecast bias is carried into the
simulation as before.

**Cash** (`calibration.cash_validation`, 23 windows): the 28-day total fell inside its 80% range
16/23 (old) → 20/23 (new). The **lowest point of cumulative cash flow**, which a shortfall depends
on, was inside its range 18/23 under both. **Cash-shortfall probabilities themselves are not
validated:** there are no shortfalls in the history to check them against, and the app now says
so.

**What it means.**
1. **Totals were the problem, and they are fixed.** 14- and 28-day totals were too narrow, and
   now they are not.
2. **Daily ranges were never the problem.** A single day's distribution is the same under both
   methods, so daily coverage is unchanged at 76–79%. That is slightly below 80%, as expected when
   reading quantiles from about 23–31 windows.
3. **The new 28-day ranges are conservative:** 87–100% per quantity, 90% overall. The earliest
   backtest windows, whose models had less training history, have larger errors: mean |28-day
   revenue error| was $6,311 in windows 0–9 against $3,733–$4,747 later. Restricting to recent
   windows would remove that, but it costs daily calibration with so few windows (see above).
4. **Small samples.** With 23 windows, each coverage figure is uncertain by roughly ±15 points
   (Wilson interval). The 10 quantities share the same windows and are strongly related, so the
   "230" in the pooled figure is not 230 independent checks.
5. **Run-to-run noise.** A whole-window range is read from a few dozen past windows, so one
   borderline window can flip with the random seed (for example 21/23 vs 22/23).

## What changed on screen (before → after, all models, warm cache)

**Recommendations: none changed.**

| Item | Before | After |
|---|---|---|
| Restaurant engine, default scenario | cut discretionary +$957 (90% range $616–$1,293) | cut discretionary +$958 ($606–$1,293) |
| Restaurant engine, food +15%, $2,000 cash | raise prices 3% +$1,033 ($654–$1,435) | raise prices 3% +$1,029 ($631–$1,453) |
| Restaurant engine: expected profit, doing nothing | $5,522 | $5,408 (Monte Carlo noise; see below) |
| Retail engine | reorder to service level +$21,609 | reorder to service level +$21,601 |
| Scenario Lab, taco +5% (28 days) | +$405 (+$12 to +$1,531), better in 96% | +$410 (+$6 to +$1,479), better in 95% |
| Scenario Lab, burrito +5% (28 days) | +$463 (+$71 to +$1,478) | +$477 (+$0 to +$1,620) |
| Scenario Lab recommendation | "Tacos price +5%" | "Tacos price +5%" |
| Lab baseline 28-day revenue, 90% range | $69,918–$83,263 | **$66,842–$84,027** (+29% wider) |
| Lab baseline 28-day profit, 90% range | $1,421–$10,198 | **−$396–$10,641** (a loss is now inside the range) |
| Forecast: 28-day revenue total, 80% range | $71,407–$82,621 | $70,080–$83,538 |
| Forecast: 28-day net cash flow, 80% range | $1,391–$8,813 | $443–$10,372 |
| Cash risk at $2,000 starting cash | shortfall in 8.9% of paths; ending cash $3,429–$10,715 | **10.2%**; ending cash **−$446–$12,372** |
| 28-day taco stock-out risk (baseline) | 20.6% | 19.5% |
| 7-day stock-out risk shown on the Overview | 14% | 14% |

**Why the profit-change ranges barely moved.** The plan and the option share the same simulated
months, so forecast errors largely cancel in the difference. The width of a difference is driven
mostly by the assumption ranges.

**Means move slightly** (for example $5,522 → $5,408). Both methods have the same expected value,
but whole-window paths repeat a few dozen distinct error windows, so a 2,000-path average carries
more Monte Carlo noise (about ±$150 here). A larger simulation count would shrink it.

## Labels and warnings in the dashboard

- **Revenue & Operations → Forecasts**:
  - The range is labelled "80% range for the 28-day total". Next to it is the range check for
    that quantity and horizon ("past 28-day totals inside: 22/23"), with its 95% uncertainty in
    the tooltip.
  - Final-test WAPE is shown as *point* accuracy.
  - The old daily coverage figure is no longer shown next to the total range; in the tool output
    it is labelled as daily coverage of a different method.
  - A notice appears when a range is not validated, has fewer than 20 checks, looks too narrow
    (< 65%) or looks conservative (> 92%).
- **Product view:** the demand range checks for 7- and 28-day totals, plus daily coverage.
- **Scenario Lab:** the revenue and profit range checks for the selected horizon, plus an explicit
  **not validated** note for the assumption ranges and for differences between options (no
  counterfactual outcomes exist).
- **Stock-out risk** (Overview and the product table): "a simulation frequency, not a validated
  probability."
- **Cash risk** (analyst tool and offline answers): "NOT a validated probability", with the
  cash-path range checks.
- **Evidence & Data → Uncertainty calibration:** a new page with the old-vs-new table, computed
  live.

## Tests

| | Result |
|---|---|
| Full offline suite | **206 passed**, 10 live tests skipped (not run; no live calls in this milestone) |
| New in `tests/test_calibration.py` | **13** |
| Lint (`ruff check src tests scripts app`) | clean |

**What the new tests guard:**
- On synthetic "monthly offset" errors, weekly blocks understate total spread and under-cover;
  whole windows reproduce the spread and cover about 80%.
- The evaluation never uses the judged window or later ones.
- Each path is exactly one window, shared by all products and fixed costs.
- 7- and 14-day horizons slice the same windows.
- Common random numbers survive.
- Cash paths default to whole windows.
- On the real restaurant revenue board, the new 28-day coverage is at least 75% and above the old
  method's.
- Re-run validation forecasts reproduce the stored errors.
- Tools report the check that matches the displayed range.
- Small samples are flagged.

The existing Milestone 1 test of cumulative cash coverage on independent errors still passes.

## Limitations

- **Synthetic data only.** On real data with regime shifts (Milestone 3.5 Kwick365: 38% coverage),
  ranges can fail in ways this cannot show. Monitoring live coverage remains future work.
- **Conservative 28-day ranges** (about 90%) for the reason above. Weighting recent windows would
  help once there are many more windows.
- **No range is larger than history.** The ranges cannot contain a surprise bigger than any in
  the backtest windows.
- **Assumption ranges cannot be validated.** Price sensitivity, substitution and the marketing
  response, and the differences between options, have no counterfactual outcomes to check
  against.
- **Probabilities are not validated as probabilities.** That includes stock-out risk and cash
  shortfall.
