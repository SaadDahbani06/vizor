# Milestone 5: dashboard and product-level Scenario Lab (2026-11-14)

> **Update (Milestone 5.1):** the Monte Carlo error sampling described below changed from
> 7-day blocks to whole historical windows, because the weekly blocks made multi-week totals too
> narrow. Ranges quoted here are the Milestone 5 values; see `docs/MILESTONE_5_1_CALIBRATION.md`
> for the check, the new values, and the fact that no recommendation changed.

Everything below was produced by running the code on 2026-11-14. The business is still
**synthetic** (Lone Star Taqueria, Austin TX). Nothing here is validated on a real business, and
no live API was called in this milestone. Gemini and Photon code paths were exercised offline,
with the offline analyst and the local simulator.

## What changed

| Area | What is new | Where |
|---|---|---|
| Product data | Five product groups, recipes and an ingredient stock ledger, derived from the unchanged daily dataset; reconciles to the cent | `src/decisiondesk/products.py` |
| Expense detail | Daily accrual cost components (labor, rent, utilities, …) recomputed from the generator's formulas | `synthetic_v2.restaurant_cost_components` |
| Product forecasts | One demand forecaster per product, chosen by the existing chronological leaderboard | `decision/product_scenario.py` |
| Scenario Lab engine | Price, ingredient orders, extra purchase, demand shock and marketing vs the current plan; uncertainty; sensitivity; policy check; recommendation | `decision/product_scenario.py` |
| Dashboard numbers | Headline cards, comparisons, expense breakdown, payables, product table, insights, profit-change decomposition | `src/decisiondesk/dashboard.py` |
| Interface | Seven pages, editorial theme, clickable cards, hover charts, product view, Scenario Lab, shareable links | `app/streamlit_app.py`, `src/decisiondesk/ui/` |
| Analyst | Three new tools (13 in total): product performance, product scenario and why profit changed; offline routes for them; Gemini replies run in the background | `agent/tools.py`, `agent/agent.py` |
| Number check | Numbers must match what the sentence says they are, not just appear somewhere | `agent/grounding.py` |
| Messaging | One published scenario for the dashboard and worker; one message poller at a time; reply evidence records scenario, tools and model | `messaging/shared.py`, `service.py`, `worker.py` |

The original daily dataset, models, caches and decision engine are unchanged. The restaurant
engine still recommends cutting discretionary spending 30% (+$957; 90% range $616 to $1,293) for
the default scenario.

## The three kinds of numbers

The interface tags every number with one of these labels: **Observed · synthetic**, **Real data**,
**Model forecast**, **Simulated** and **Assumption**.

| Kind | What it is | Examples |
|---|---|---|
| **Accounting identities** (observed, synthetic) | Exact formulas on the records | Revenue, operating profit = revenue − variable − fixed costs, margins, the expense breakdown, gross profit = revenue − ingredient cost, payables (net-7 purchases not yet paid), inventory at cost, the cash ledger, why profit changed |
| **Trained ML** (model forecast) | Forecasters chosen by back-testing in date order against baselines | Revenue and variable costs: Ridge. Fixed costs: CatBoost. The five products' demand: Ridge. Forecast ranges come from the models' own out-of-sample errors |
| **Assumptions** (counterfactual, simulated) | Stated values with ranges, never estimated from the data | Price elasticity for each product, the substitution share, the marketing response, the ordering rule, the intraday demand curve, recipes. The existing engine's discretionary share and supplier success |

**Profit and cash are kept apart.** Profit counts ingredients when they are used. Cash counts
them when they are paid (net-7), plus card settlement the next day. Inventory build-up therefore
costs cash but not profit, and the Scenario Lab shows both.

## Product data (synthetic, reproducible)

**How it is generated.** `build_product_history` generates the product layer from the existing
daily records with seed 5150, so it is the same on every run.

1. **Orders.** Guests order main dishes (1.30 per guest) and drinks (1.10 per guest). The product
   mix has weekday, season, trend and event effects: Taco Tuesday, Super Bowl nachos, burritos
   gaining share and quesadillas losing it.
2. **Deliveries.** Ingredients arrive Monday and Thursday under an order-up-to rule: 1.35 × the
   estimated same-weekday usage until the next delivery. About 5% of deliveries are
   short-shipped for one ingredient, and the shortfall arrives the next morning.
3. **Shortages.** When an ingredient runs short, every product that needs it is rationed in
   proportion. Sales never exceed demand, and stock never goes negative.
4. **Sell-out records.** The POS records the time an item sold out. Demand on that day is
   estimated as sales ÷ the share of daily demand that usually arrives by then (an assumed
   lunch/dinner curve).

**Checks** (`tests/test_products.py`, also shown on the Evidence & Data → Reconciliation page):
- **Revenue.** Product revenue + other items = daily revenue, within one cent. Other items are
  never negative, and the five products are about 85% of sales.
- **Food cost.** Product ingredient cost + other food = the daily food cost, within one cent.
- **Stock.** Opening + delivered − used = closing, and closing is never negative.
- **Usage.** Ingredient usage = recipe × units sold.
- **Sales.** Units sold ≤ demand.
- **Sell-outs.** 10 sell-out days in three years (42 product-days).
- **Demand estimate.** On sell-out days it is off by 1.4 units on average, against a 12.3-unit
  gap between recorded sales and hidden demand.
- **Mix adjustments.** None were needed to reconcile the totals.

**Planning bug found and fixed while building it.** The first ordering rule planned from
recorded sales. Sales are capped on sell-out days, so every shortage shrank the next order and
sell-outs fed on themselves (131 product-days a year). The owner now plans from the estimated
demand, which only uses past days.

## Product demand forecasts

The target is each product's estimated daily demand. The models are compared with the existing
rolling-origin validation and an untouched final test (2026-05-30 to 2026-11-13). There are 6
model candidates, and a model is kept only if it beats the best baseline by at least 2%.

| Product | Selected | Test MAE (units/day) | Seasonal-naive test MAE | Test WAPE | 80% interval coverage (test) |
|---|---|---|---|---|---|
| Tacos | ridge | 11.3 | 14.9 | 0.194 | 0.77 |
| Burritos | ridge | 9.8 | 14.5 | 0.179 | 0.82 |
| Quesadillas | ridge | 5.8 | 8.4 | 0.223 | 0.82 |
| Nachos | ridge | 6.1 | 9.6 | 0.222 | 0.82 |
| Drinks | ridge | 25.0 | 35.4 | 0.168 | 0.77 |

These scores are measured on **synthetic** history. They test the software, not real-world
accuracy.

**Consistency check.** The baseline revenue from the product models, $76,429 over 28 days, is
within 1.1% of the existing whole-business revenue model ($75,613), and a test enforces less
than 5%.

## Scenario Lab

**The model.** For each of 1,000 simulated paths, with the same paths reused for the plan and
every option:

1. **Baseline demand.** Each product's forecast plus a re-sampled back-test error path.
2. **Price change.** Demand × (1 + p)^e, with e drawn from the product's assumed range (tacos
   −1.8 to −0.6, mode −1.1). 30% of the demand a main dish loses (assumed range 10–50%) moves to
   the other main dishes. Drinks have no substitutes.
3. **Other inputs.** The demand shock and the marketing response (0–5% of demand per 100% change
   in spend) multiply demand.
4. **Supply.** The same ordering rule as the history, sized to the baseline forecast. The supply
   control scales this product's share of each order, and an extra purchase arrives on day 1.
   Shared ingredients are rationed. 30% of a main dish's unmet demand is re-offered to main
   dishes that still have stock; the rest is lost.
5. **Money.** Revenue, ingredient cost, other items and other food (per main dish, from the last
   56 open days), packaging (per guest), card fees (2.3% of revenue), and fixed costs from the
   existing model plus any marketing change.
6. **Comparison.** Means and 5th–95th percentile ranges against the plan, the share of paths
   where profit improves, sell-out risk (paths with at least 1% of demand unmet), cash and ending
   inventory.

**Owner limits.** A price increase above the owner's limit (5%) is computed, shown as
**outside the approved limits**, and excluded from recommendations. The limit is never silently
overridden.

**Recommendation.** It tests price −5%, +3% and +5% (the policy maximum), +25% orders when
sell-out risk is above 10%, and your own scenario, then ranks them by mean profit change. It
re-runs the winner with each assumption at its low and high bound and says whether the result
reverses. The text is a template filled from the simulation; Gemini never computes it.

**Example (default scenario, 28 days).** Taco price +5% changes profit by +$405 (90% range +$12 to
+$1,531; better in 96% of paths) and sells about 89 fewer tacos. With elasticity at −1.8 the gain
is only +$49.

**Speed** (warm caches, all models, measured with Streamlit's AppTest):
- **First load:** 3.0 s.
- **Opening a page:** 0.1–0.4 s.
- **Price slider:** about 0.4 s per change, for the full Scenario Lab rerun. One simulation takes
  20–100 ms, and models are never refitted when a slider moves.
- **Cold cache:** `python -m decisiondesk.precompute` fits everything once. The product models
  take about 75 s; a warm run takes about 2 s.

## AI analyst

**New tools:** `get_product_performance`, `simulate_product_scenario` (blank values keep the
Scenario Lab's current settings) and `explain_profit_change`. Gemini sees 13 tools.

**Offline routes** answer:
- "Why did profit drop?"
- "Which of my five products is most profitable?"
- "What happens if burrito prices increase by 5%?"
- "Should I purchase more taco ingredients?" This tests +25% planned orders. A one-time purchase
  barely matters, because the ordering rule tops up anyway, and the tests check that.
- Demand-shock and marketing questions.

If a business has no product records, the analyst explains what data is missing.

**Gemini does not block the dashboard.** A Gemini question runs in a background thread. The page
shows progress, and every other page stays usable, which a test checks with a deliberately slow
analyst. Gemini was not called live in this milestone.

## The number check: meaning as well as value

**The problem.** On 2026-11-14 a live reply said "price elasticity is low (−0.6)". The tool
output has no elasticity of −0.6; the recommendation reverses at −0.4. It passed because 0.6
appears in the output as the supplier success probability.

**The new rule.** The number must match a tool value *and* the concept named next to it.
- The concepts are elasticity, probability, range, supplier, price, demand, profit, revenue,
  cash, cost, food, stock-out, inventory, margin, accuracy and duration.
- Each tool value carries labels from its key path and the short text beside it.
- A dollar amount can never match a count, a probability or an elasticity.
- Numbers the user typed are always accepted.

**Results.**
- The live reply is now flagged: `-0.6 (no tool value for 'elasticity')`.
- The corrected sentence ("−0.4") passes.
- All 16 offline question types produce no false flags.
- Fixing the offline replies meant writing negative money as "-$602" instead of "$-602".

**Limit.** The check works on words near the number. It catches the wrong number for a named
concept, but it cannot judge reasoning, such as calling −0.4 "low".

**Also fixed.** `get_assumptions` no longer hands the generator's hidden true elasticity (−0.7)
to the language model.

## Messaging: one scenario, one poller

**One scenario.**
- **Publishing.** "Use this scenario for iMessage answers" in the Scenario Lab, or "Publish" on
  the Messaging page, writes `data/cache/published_scenario.json` with a version number.
- **Who reads it.** The dashboard's auto-check and `python -m decisiondesk.messaging.worker run`
  both read that file.
- **Updates.** An owner's next text picks up a newly published version.
- **Default.** With nothing published, both use the **default** scenario. The dashboard no longer
  copies whatever it happens to show.
- **Checking it.** `python -m decisiondesk.messaging.worker scenario` prints what replies will use.
- **Evidence.** Each reply's record now stores the scenario version and summary, the tools called
  and the Gemini model that answered.

**One poller.**
- **The lock.** The worker and the dashboard's auto-check take a lock file. The worker refuses to
  start if another poller is active (tested in a real subprocess), and the dashboard turns into a
  read-only viewer while the worker runs.
- **Long replies.** The worker's lock lasts as long as its process is alive (capped at one hour),
  so a 2.5-minute Gemini reply cannot release it. The dashboard's lock needs a heartbeat every
  30 s.
- **Background polling.** The dashboard's auto-check now runs in a background thread.
- **Chat status fix.** Chat bubbles now show the delivery status Photon confirms. They used to
  stay at "dispatched".

**Action needed:** a worker started before this change (PID 75641, 12:07 PM) is still running
the old code, without the lock or the published scenario. Stop it with Ctrl-C and start it again.

## Tests

| Kind | Result | Command |
|---|---|---|
| Offline and mocked (all milestones) | **193 passed** (55 new: 14 product/dashboard, 22 Scenario Lab, 11 verifier/messaging, 8 UI) | `pytest -q` |
| Live (opt-in) | **Not run in this milestone** (10 skipped by design) | `MSRD_LIVE=1 pytest -q -m live` |
| Lint | `ruff check src tests scripts app`: clean | |

Existing UI tests were updated for the new navigation: they open the right page first.

**Regression checks:**
- `python -m decisiondesk.demo --industry restaurant` works.
- `python -m decisiondesk.product_demo --offline` works.
- `python -m decisiondesk.decide --scenario restaurant` works.
- `scripts/verify_milestone2.py` gives 0 FAIL and the 3 known PARTIALs.

## Limitations

- **Synthetic data.** All business and product data is synthetic. Validation scores say nothing
  about real accuracy.
- **Assumed effects.** Product elasticities, substitution and the marketing response are
  assumptions. The data has only menu-wide price steps, so product price effects cannot be
  estimated.
- **Inventory model.** It has no spoilage or shelf life. Ingredient purchases for the five
  products are a planning ledger, not separate cash-ledger entries; the dataset's total food
  purchases are in the cash ledger.
- **Unavailable figures.** Loan principal and total debt are not in the data. There is no
  transaction-level data, so "average revenue per guest" replaces "average ticket".
- **Two meanings of "discretionary".** The business-wide engine's "discretionary" (an assumed
  10% of fixed costs) is broader than the Scenario Lab's marketing & software line ($1,050 a
  month).
- **Theme.** Dark mode is not themed; the app is set to the light editorial theme.
- **Screenshots.** Pages were checked with headless Chrome at 1440 px. Phone widths were not
  checked.
- **Live channels.** Gemini and Photon were not run live in this milestone.
