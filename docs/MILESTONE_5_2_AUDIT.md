# Milestone 5.2: hackathon-readiness audit (2026-11-14)

Branch `milestone-5.2-demo-readiness`, from `milestone-5.1-calibration`. No features were added
and nothing was redesigned. Two critical bugs were found and fixed, and four labels were
clarified.

## What was checked

| Check | Result |
|---|---|
| Full offline test suite | **208 passed**, 10 live tests skipped (not run; no live API calls in this audit) |
| Lint | clean |
| Calibration (`python -m decisiondesk.calibration`) | matches the Milestone 5.1 report (for example, 28-day revenue totals 22/23 inside their 80% ranges) |
| Command-line regression | the restaurant demo, the decide command and the offline product demo all ran (before the fixes, in the Milestone 5.1 run) |
| Headless screenshots, 1440 px | Overview, Revenue & Operations (Products), Scenario Lab (tacos +5%): rendered, no errors |
| Timed rehearsal of the demo path, all models, warm cache | first load 2.4 s; each click 0.06–0.46 s; no exceptions |
| Same scenario everywhere | Dashboard Overview and the iMessage worker give the same answer ("cut discretionary spending 30%", +$958, $606–$1,293). After publishing "food costs +12%" from the dashboard, both switch to "raise menu prices 3%", +$1,010 |
| Analyst vs Scenario Lab | identical figures for tacos +5% (+$286, −$5 to +$557, 95%) |

## Critical bugs fixed

### 1. Scenario Lab: exploding substitution demand (present since Milestone 5)

**What went wrong.** When a main dish's price changed, the demand it lost was split among the other
main dishes by their *unclipped* simulated demand. A forecast plus a borrowed historical error can
be near zero or negative, for example a past closed day. Dividing by that produced absurd demand:
an average of 149 million burritos over 28 days for "tacos +5%".

**Why no test caught it.** Sales were capped by ingredient stock, so profit stayed plausible.

**What it distorted:**
- the shared-ingredient rationing;
- the switching effect;
- taco stock-out risk, which *rose* when tacos got pricier;
- the upper tail of the profit range.

**Fix** (`decision/product_scenario.py`):
- Demand is clipped at zero before any effect is applied. The baseline itself is unchanged,
  because it was already clipped later.
- Switching shares are 0 on a day when the other dishes have no demand.

**Regression test:** `test_substitution_conserves_demand_and_never_explodes` checks that switching
demand stays within the assumed 10–50% of the lost demand. On the old code it fails with 115
billion units.

**What changed (28 days, default scenario):**

| Scenario | Before | After |
|---|---|---|
| Tacos +5% | +$410 (+$6 to +$1,479), better in 95%; taco stock-out risk 20% → 27% | **+$286 (−$5 to +$557)**, better in 95%; taco stock-out risk **20% → 18%** |
| Tacos +5%, sensitivity at the most price-sensitive bound (−1.8) | +$55 | **−$72: the recommendation reverses**, and the text now says so |
| Burritos +5% | +$477 (+$0 to +$1,620) | **+$373 (+$115 to +$602), better in 100%**, positive at every bound |
| Recommended action for each product | price +5% | price +5% (unchanged for every product) |

The business-wide decision engine, forecasts, calibration, cash risk and dashboard KPIs were not
affected: this code path exists only in the Scenario Lab.

### 2. "Ask the AI analyst about this" lost the click

**What went wrong.** The Scenario Lab's button opened the analyst with a suggested question, but
the page removed the suggestion as soon as it was displayed. Clicking "Ask: …" then did nothing.

**Fix:** the suggestion is kept until it is asked.

**Regression test:** `test_scenario_lab_question_reaches_the_analyst`.

## Clarifications (labels only)

- **Overview period labels.** "Revenue by product" and "Expense breakdown" now state their period,
  which follows the period selector. "Business intelligence" states that it always uses the last
  28 days. Before, month to date (burritos lead) sat next to a 28-day insight (tacos lead) with no
  label.
- **Business-wide decision engine.** A caption explains that its "do nothing" profit comes from
  whole-business models, so it differs by a few percent from the product-level plan.
- **Deploy button.** `.streamlit/config.toml` now sets `toolbarMode = "minimal"`, so Streamlit's
  Deploy button can't be clicked on stage.

## Demo risks and what to do

1. **An old messaging worker is still running.** PID 75641 was started at 12:07 PM in terminal
   `ttys003`, with code from before Milestone 5. It doesn't use the poller lock or the published
   scenario, has the old number check and the weekly-block ranges, and keeps rewriting
   `data/cache/messaging_state.json`. If anyone texts the line, *it* answers. Stop it (Ctrl-C in
   that terminal) and, if needed, restart `python -m decisiondesk.messaging.worker run`.
2. **A dashboard started at 3:52 PM is still running** (PID 23495, terminal `ttys006`). Restart it
   after checking out this branch, so it loads the fixed code and the new config.
3. **Gemini is on by default.** `GEMINI_API_KEY` is set, and live replies took 2–3 minutes. Pre-ask
   the analyst question, or start the app with `MSRD_LLM_PROVIDER=offline`.
4. **No holiday features in the forecasts.** The models don't know about holidays, and the demo
   restaurant is closed on Thanksgiving (Nov 26, inside the 28-day horizon), so that day is
   forecast as a normal Thursday. Profit is overstated by about one day's sales. Say "the models
   have no holiday calendar yet" if asked.
5. **Fixed stock-out percentages for main dishes.** The four main dishes show the same stock-out
   risk (14%) because they share tortillas, meat and cheese. It's correct, but explain it.
6. **The Messaging page calls Photon live.** Venue Wi-Fi can fail; the simulator is the fallback.
7. **A cold cache takes minutes.** Run `python -m decisiondesk.precompute` beforehand. A warm run
   takes seconds.
