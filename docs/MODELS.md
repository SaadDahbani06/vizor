# Financial models, assumptions and limitations

This page describes every calculation Vizor shows, what it assumes, and where it can be wrong.
Everything here is deterministic Python. Vizor AI (Gemini) never calculates: it receives tool
results and writes an explanation, and a reply is rejected if it states a number that does not
match a tool value next to the concept it is said to be (`agent/grounding.py`).

## 1. Recorded figures (accounting, no model)

| Figure | Definition | Source |
|---|---|---|
| Revenue | sum of daily `revenue` | records |
| Variable costs | costs that move with sales (ingredients, packaging, card fees) | records |
| Fixed costs | rent, payroll, utilities, software, insurance | records |
| Operating profit | revenue − variable costs − fixed costs (before taxes, loan repayments, owner draws) | records |
| Margin | operating profit ÷ revenue | records |
| Cash on hand | opening balance + all money in − all money out | needs a declared opening balance; otherwise "not entered" |
| Month to date | the current month up to the last recorded day, compared with the same days of last month | records |

Uploaded and connected data are converted into these daily totals by the ingestion layer
(`docs/INGESTION.md`). For ledger sources the basis is **accrual** (invoice and bill dates) for
QuickBooks, **sale date** for Square and files. Missing values are never filled in: records without
a date or amount are skipped and counted; days without any transactions count as zero activity
and are reported.

## 2. Forecasts (statistical models, tested in date order)

Candidates (`models.py`, `leaderboard.py`):

- `seasonal_naive`: the same weekday last week.
- `weekday_mean_12w`: average of the same weekday over the last 12 weeks.
- `historical_mean_28d`: average of the last 28 days.
- `ridge`: Ridge regression on lag, rolling-window and calendar features computed only from data
  available at the forecast origin (leakage-tested by perturbing future values).
- `xgboost`, `catboost`: gradient-boosted trees with fixed, untuned settings (optional `ml` extra).

**Evaluation** (`EvaluationConfig`): 28-day horizon, rolling origins every 28 days, at least 224
training days, at least 4 validation windows and 6 untouched holdout windows. That is why a tested
forecast needs about **504 days** of history; with less, Vizor shows no forecast rather than an
untested one. A machine-learning model replaces the best simple baseline only if it improves the
validation error by at least 2%. Reported metrics: MAE, RMSE, WAPE, bias, skill against
seasonal-naive, and interval coverage, all measured out of sample.

**Ranges** (`decision/baseline.py`, `calibration.py`): Monte Carlo paths replay whole historical
4-week windows of out-of-sample errors (errors are correlated across weeks; resampling single
weeks made 4-week totals too narrow, about 68% coverage instead of 80%). Every displayed range is
checked chronologically against past windows and labelled when there are too few windows (fewer
than 20) to judge it.

**Measured results** are synthetic for the demo businesses: they show the software works, not
how accurate Vizor would be for a real business. The only real data evaluated is an aggregate
index of independent restaurants (Kwick365; `docs/MILESTONE_3_5_US_VALIDATION.md`).

## 3. Scenarios (simulation under stated assumptions)

All scenarios run on the same random draws (common random numbers), so "action vs no action"
differences are not noise from separate simulations. Every result is reported against the
no-action baseline with a range and the chance it is better.

**Whole-business restaurant actions** (`decision/restaurant.py`):

| Action | Formula | Assumption (default, range) |
|---|---|---|
| Price increase p | volume × (1 + p)^e; revenue × (1 + p) × volume | elasticity e = −0.8 (−1.2 to −0.4), triangular |
| Supplier negotiation | variable costs × (1 − s × share × success) | share 0.85; success probability 0.6 (0.4–0.8) |
| Discretionary cut | fixed costs × (1 − c × share), demand × (1 − k) | share 0.10; demand loss k 0.5% (0–1.5%) |

**Product Scenario Lab** (`decision/product_scenario.py`, demo restaurant only): per-product
demand forecasts; own-price elasticities between about −0.6 and −2.2 by product; 30% (10–50%) of
demand lost by one main dish moves to the others; ingredient stock and twice-weekly deliveries limit
what can be sold; cash timing differs from profit (card settlement, net-7 supplier terms).

**Retail inventory policies** (`decision/retail.py`): weekly review, order-up-to service levels,
lead time, holding cost and lost sales; budget, storage and stock-out limits are the owner's.

**Cash risk**: the chance that cash falls below zero within 4 weeks, from the same simulated paths.
It is only computed with an owner-declared starting balance. With few holdout windows it is
reported as "not established" rather than as a calibrated probability.

## 4. What these models are not

- **Not causal.** The data has no price or supplier variation, so elasticities and success rates
  are assumptions with ranges, never estimates. A forecast says what is likely if nothing changes;
  a scenario says what the stated assumptions imply.
- **Not validated on a real business.** Real accuracy can only be measured once a business's own
  history (ideally two or more years) is connected; the outlook page then shows the measured
  coverage of its ranges.
- **Not tax, lending or investment advice.** Operating profit is before taxes and debt service.
- **External economic data** (FRED) did not improve forecasts on the synthetic businesses and is not
  used in the dashboard.
- **Sensitivity matters.** The tacos +5% example is positive at the default elasticity but turns
  negative at the low end of its range; the Decision Lab shows this edge case on purpose.
