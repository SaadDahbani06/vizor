# Milestone 4: end-to-end product verification (2026-11-14)

Every result below was produced by running the code on 2026-11-14.
- **Python** 3.13.3; **anthropic** 2.8.0; **streamlit** 1.67.0.

## What the data is (read this first)
| Data | Nature | Used for |
|---|---|---|
| `synthetic_v2` restaurant (Lone Star Taqueria, Austin TX) and retailer (Maple Street Hardware, Columbus OH) | **SYNTHETIC**; fictional businesses, 1,096 days (2023-11-14..2026-11-13) | Forecasting, decisions, agent, UI, monitoring |
| FRED/ALFRED (food-at-home CPI, food-away-from-home CPI, CPI, retail sales, unemployment) | **REAL** published U.S. observations, point-in-time | Context, alerts, hypothetical stress tests. Not model inputs: they failed validation earlier |
| Capital One Nessie sandbox | **SIMULATED** banking records | Banking tab and import summary only. Never forecasts |
| Food-cost changes, demand-sensitivity changes, budgets the user proposes | **HYPOTHETICAL** scenarios | Scenario simulation |
| Action effects (price elasticity, supplier success, discretionary demand loss) | **ASSUMED** | Decision engine. Not estimated causal effects |

**Accuracy figures:**
- **Synthetic data only.** No figure here is validated on a real small business.
- **Real aggregate data:** results from Milestone 3.5 (Kwick365) are documented separately.
- **Simulated shares are not real-world guarantees,** including "100% of simulations better".

## Summary
| Requirement | Status | Evidence |
|---|---|---|
| Phase 1: realistic, consistent demo data | **PASS** | `synthetic_v2.py`; data tests; leaderboard results below |
| Phase 2: AI agent calling Python tools, never inventing numbers | **PASS (offline + scripted Claude path); live Claude NOT run** | `agent/agent.py`, `agent/grounding.py`; tests; no Anthropic credential configured |
| Phase 3: one pipeline from dataset to explanation | **PASS** | `agent/tools.py`; `python -m decisiondesk.product_demo` |
| Phase 4: functional interface | **PASS** | `app/streamlit_app.py`; headless AppTest; server health 200 |
| Phase 5: proactive monitoring | **PASS (in-app)**; Telegram coded and tested with a fake, **not sent live** (no credentials) | `monitor.py`; tests |
| Phase 6: two demo scenarios with follow-ups | **PASS** | outputs below |
| Phase 7: accuracy and transparency | **PASS** | labels, grounding, caveats in every tool output |
| Phase 8: integration tests, regression, lint | **PASS** | 104 offline + 8 live tests; Ruff clean |
| Nessie as selectable simulated banking source | **PASS** | `banking.py`; Banking tab; tests with a fake server plus a live read-only import |

## Phase 1: demonstration data (`src/decisiondesk/synthetic_v2.py`)

The v1 generator is kept unchanged, so Milestone 1–3 results still reproduce.

**What v2 adds:**
- **Demand:** negative-binomial daily demand with weekday and annual seasonality, a trend, an
  AR(1) shock, and U.S. holidays and closures (computed, including Easter, Thanksgiving and
  Mother's Day).
- **Local and pricing effects:** local events (SXSW weeks); menu price steps (restaurant);
  promotions and annual list-price changes (retail).
- **Costs and inventory:** a supplier-cost random walk with a commodity shock; deliveries with
  variable lead times; retail sales capped by stock on hand (lost sales unobserved; stockout days
  flagged).
- **Bills:** recurring bills on real schedules (rent on the 1st, biweekly payroll, seasonal
  utilities, quarterly insurance, loan, software/marketing, weekly supplies/repairs).
- **Cash:** next-day card settlement; supplier terms net-7 (restaurant) and net-30 (retail); a
  cash ledger from a **declared** opening balance; owner draws as cash-only outflows.
- **Metadata:** name, industry, city, state, assumptions, declared constraints.
- **Hidden truths** (e.g. demand elasticity −0.7 / −1.1) are documented in metadata, **not**
  given to the models.

**Calibration (last 365 days):**

| | Restaurant | Retailer |
|---|---|---|
| Revenue | $1.11 M | $1.13 M |
| Food / COGS + card fees | 35.2 % | 61.2 % |
| Labour | 32.5 % | 21.4 % |
| Rent | 7.1 % | 7.6 % |
| Other operating | 8.0 % | 2.7 % |
| Operating margin (accrual) | 12.0 % before a 6.5 % owner draw | 5.0 % |

**Forecasting on v2: harder than v1, as intended.** 28-day horizon, 12 validation and 6 test
folds:

| Target | Selected | Validation WAPE | Test WAPE | Test 80 % coverage | Best baseline (test) |
|---|---|---|---|---|---|
| restaurant revenue | ridge (+4.0 % over baseline on validation) | 0.182 | 0.165 | 0.82 | weekday mean 0.180 |
| retail units sold | ridge (+7.4 %) | 0.227 | 0.195 | 0.83 | weekday mean 0.204 |

v1 scored test WAPE 0.088. These are **synthetic-data** scores only.

## Phase 2–3: agent and pipeline

**Tools** (`agent/tools.py`; the same functions serve the agent and the UI):
`get_business_overview`, `get_economic_indicators`, `forecast_business`,
`evaluate_restaurant_actions`, `evaluate_inventory_policies`, `assess_cash_risk`,
`get_assumptions`, `create_alert_rule`, `list_alerts`, `get_banking_summary`.

- **Engines:** each tool calls the existing ones — the leaderboard with validation-only
  selection, the selected-model forecast bundle, the Milestone 3 decision engine, the FRED
  connector and the backtested cash risk.
- **Outputs:** each returns a compact aggregate with a `run_id`, the data label and the business
  "as of" date. No raw rows are returned.
- **Persistent assumptions:** changed assumptions live in `ScenarioState`, so follow-ups rerun
  with them.

**Agent** (`agent/agent.py`):
- **Claude mode:** `claude-opus-5-5`, manual tool loop, strict tool schemas, effort `medium`,
  prompt caching on the system prompt, and **server-side refusal fallbacks enabled**
  (`fallbacks: "default"`).
  - Every `tool_use` block is executed, and all results go back in one user message.
  - Tool errors return as `is_error` results.
  - The `refusal`, `pause_turn` and `max_tokens` stop reasons are handled.
- **Offline mode:** used automatically when no Anthropic credential exists. A deterministic,
  **rule-based** analyst (it says so in every reply) maps the demo question types to the same
  tools and fills its templates only with tool outputs.
- **Grounding check** (`agent/grounding.py`):
  - **Rule:** every number in a reply must equal, at its written precision, a number in this
    conversation's tool outputs or the user's own words. Allowed: % ↔ fraction, k/M suffixes, and
    digits inside dates or IDs.
  - **Claude mode:** a violating reply is regenerated once; any remaining violation is shown
    with a ⚠️ "Unverified numbers" warning.
  - **It caught three of my own template bugs during this milestone:** "12-month", a help-text
    "$40,000" example, and "28-day" in a fresh conversation. All three are fixed.

## Phase 6: demonstrations (`python -m decisiondesk.product_demo --offline`, exit 0, ~60 s)

### Restaurant: "What should I do if my food costs increase 15%?"

- **Tool:** `evaluate_restaurant_actions` (food +15 %, hypothetical); grounded: True.
- **First call:** 44 s (builds three leaderboards); reruns take under 1 s.

| Rank | Action | Expected 28-day profit | Change vs no action (90 % range) |
|---|---|---|---|
| 1 | Raise menu prices 3 % | $2,476 | +$1,033 (+$654..+$1,435) |
| 2 | Cut discretionary spending 30 % | $2,431 | +$988 (+$675..+$1,296) |
| 3 | Negotiate 4 % lower supplier prices | $1,663 | +$220 (−$250..+$627) |
| 4 | Do nothing | $1,443 | — |
| — | Raise menu prices 8 % | excluded (> 5 % limit) | |

- **Recommendation:** raise prices 3 %, rated *assumption-sensitive*. Versus the runner-up it is
  only +$45, with P(better) 54 %, so the top two are not clearly separated.
- **Assumption:** elasticity −0.8 (range −1.2..−0.4), a default that needs confirmation.

### Follow-up: "What happens if demand becomes more sensitive to price increases?"

- **Rerun:** a new run, with elasticity −1.2 (−1.6..−0.8, now *user_declared*). The +15 % food
  cost is carried forward.
- **New ranking:** cut discretionary spending **+$988** first; raise prices 3 % falls to
  **+$480** (+$102..+$862).
- **Result:** the recommendation changes because the assumption changed. The calculation was
  re-run, not replayed.

### Follow-up: "What do current U.S. food-price indicators show?"
- **Tool:** `get_economic_indicators(refresh=true)`. **Retrieved live from FRED with your key.**
- **Values:**
  - food-at-home CPI 322.535 (2026-10), **+1.77 %** YoY (2026-09; Oct-2025 was never published);
  - food-away-from-home CPI **+3.46 %**;
  - unemployment 4.1 %, **−0.2 pp**.
- Grounded.

### Retail: "What should I do with a $40,000 inventory budget?"
- **Result: no feasible policy.**
- **Smallest relaxation:** the weekly reorder (up to 2,042 units, 95 % service) needs **$52,891**.
  The other policies break the 25 % stockout tolerance after the first delivery: maintain 98 %,
  reduce 100 %, no order 100 %.

### Follow-up: "What if I increase the budget to $45,000?"
- **Rerun with the new budget:** still infeasible. The reorder policy needs $52,891. The engine
  states this rather than forcing a recommendation.
- **Why the result is stable:** it isn't tuned. The synthetic store starts the forecast with
  521 units on hand against about 135 units/day of demand and a 7-day lead time. Rebuilding
  safety stock needs more than one month of normal purchasing.

## Phase 4: interface (`streamlit run app/streamlit_app.py`)

**Tabs:**
- **Business sidebar:** demo selection, or CSV/Excel upload with role mapping and a confirmed
  profile.
- **Overview:** metrics, revenue chart, bills, supported analyses.
- **Forecast:** history plus the 28-day forecast with an 80 % band, the leaderboard and the
  caveat.
- **Economy:** FRED table with sources, dates and a live refresh button.
- **Decisions:** scenario controls, ranking table, assumptions, sensitivity, limitations.
- **Analyst:** chat with tool and grounding footers.
- **Alerts:** rules, run checks, Telegram opt-in.
- **Banking (Nessie).**

**Verification:**
- **Headless AppTest:** no exceptions across load, a Decisions run, chat, switching business and
  the Banking import.
- **Real server:** `streamlit run` answered `/_stcore/health` with 200 within 1 s.
- **Charts:** single series per chart (no legend needed); forecast uncertainty as a band of the
  same hue; tables alongside.

## Phase 5: monitoring (`python -m decisiondesk.monitor add|run|list`)

**Rules:**
- **`fred_release`:** a new or revised observation, with an optional |YoY| threshold.
- **`cash_shortfall`:** simulated frequency ≥ a threshold.

**Behaviour:**
- **State:** stored in `data/cache/monitor_state.json` (gitignored) — rules, last-seen
  observation per series, alerts and sent keys.
- **Re-evaluation:** for a food-CPI release, the restaurant is re-evaluated with the observed
  change as a **hypothetical** food-cost shift. This works on a copy of the scenario state, so the
  user's own what-if is never modified (tested).
- **Live run (cached FRED):**
  - 1 alert: "CUSR0000SAF11: new release … +1.77 % … expected 28-day profit with no action
    $5,041; recommended: cut_discretionary";
  - the second run created 0 duplicates.
- **Telegram:** sent only when a rule opts in **and** the token and chat ID exist (tested with a
  fake transport). It is not configured here, so nothing was sent.

## Nessie banking source (`src/decisiondesk/banking.py`, Banking tab, `get_banking_summary`)

**Live read-only import (prod-api, using your key):**
- **56 transactions**, 0 duplicates.
- Checking inflows $56,563 and outflows $41,559; savings internal-transfer inflow $2,000.
- Sandbox balances (15,000 / 5,000) shown as "reported by sandbox, not used".

**Missing records flagged:**
- the bills list is unreadable on the server;
- no rent payment found;
- no utilities payment found.

**Forecast eligibility: False.** Reasons:
- 28 days < 504;
- known missing bills;
- simulated data.

Forecasts stay on the synthetic business dataset.

**Tests:**
- **Retrieved and normalized:** 56 rows, canonical columns, SIMULATED label.
- **Duplicates:** a duplicated API row is dropped (`duplicates_dropped == 1`); repeated imports
  give identical IDs.
- **Missing bills detected.**
- **Displayed:** the AppTest Banking tab shows "56 transactions", "Missing bill records" and
  "Not used for forecasting".

No Nessie writes were made in this milestone.

## Phase 8: tests (`tests/test_product.py`, 19 tests; full suite 104 passed + 8 live)
| Required check | Test |
|---|---|
| Imported data reaches the engine | `test_uploaded_csv_reaches_forecast_and_decisions` (renamed columns → mapping → confirmed profile → forecast + decisions; missing columns → unsupported) |
| FRED actually retrieved when requested | `test_fred_is_actually_queried_when_refresh_requested` (fake HTTP records `/series/observations` calls; `retrieval == live_api`) + live run above |
| Model outputs passed to scenarios | `test_model_outputs_are_what_scenarios_use` |
| Agent grounded in tool outputs | `test_claude_mode_executes_tools_and_grounds_reply`, `test_claude_mode_invented_number_is_retried_then_flagged`, `test_offline_fallback_and_indicator_questions_are_grounded` |
| Changed assumptions → fresh calculations | `test_offline_agent_reruns_with_changed_assumptions_and_stays_grounded` (new run_id, carried food cost, lower price-action gain) |
| Infeasible actions | `test_infeasible_budget_is_explained_not_forced` |
| Missing data handled safely | `test_missing_information_is_requested_not_invented` |
| Alert rules | `test_alert_rules_trigger_deduplicate_persist_and_respect_thresholds`, `test_cash_rule_and_telegram_opt_in` |
| Offline without credentials | `test_ui_runs_end_to_end_without_credentials` |
| No API keys exposed | `test_no_secrets_in_tool_outputs_or_agent_text` |
| Nessie retrieve / normalize / dedupe / display | three `test_nessie_*` tests |
| Realistic data | `test_v2_dataset_is_consistent_and_not_trivial[restaurant/retail]` (ledger reconciles to the cent, bills on schedule, non-trivial variation, realistic margin) |

```
pytest -q                          -> 104 passed, 8 skipped
MSRD_LIVE=1 pytest -q -m live      -> 8 passed
ruff check src tests scripts app   -> All checks passed!
python scripts/verify_milestone2.py --offline -> PASS 10 / FAIL 0 (no regression)
```

## Known limitations and blockers
1. **Claude mode has not been run against the real API.** No Anthropic credential is
   configured.
   - The loop, tool dispatch, message structure, strict schemas, fallbacks and the grounding
     retry are verified with a scripted fake client only.
   - **To enable it:** set `ANTHROPIC_API_KEY` in `.env` (or `ant auth login`). The app and the
     demo switch automatically.
2. **Telegram has not been sent live** (no token or chat ID).
3. **First-load latency:** about 45 s per business while the leaderboards build (cached for the
   session afterwards). `MSRD_FAST=1` uses 4 models for faster demos and tests, and the app
   labels this.
4. **Offline analyst limits:** it understands only the supported question patterns. Anything else
   gets an honest capability message.
5. **Effects are assumed, not estimated:** elasticity, supplier success and holding cost. The v2
   data contains price variation from which elasticity *could* be estimated, but that is not
   implemented.
6. **Intermittent native error:** a `std::system_error` was seen once, at interpreter exit, in 5
   full test runs (likely a library thread shutting down). The other 4 runs exited 0 with no
   error.
7. **Cash risk** is accrual-based operating cash flow, not bank timing. Calibration is not
   established (6 holdout windows).
8. **No real small-business validation** (see Milestone 3.5).

## Exact demo commands
```bash
source .venv/bin/activate
pip install -e '.[dev,ml,ui,agent]'
python -m decisiondesk.synthetic_v2            # (re)writes data/examples/*_v2_SYNTHETIC.*
python -m decisiondesk.product_demo            # both conversations (Claude if credentialed, else offline)
python -m decisiondesk.product_demo --offline  # force the rule-based analyst
streamlit run app/streamlit_app.py             # the interface
python -m decisiondesk.monitor add --business restaurant --kind fred_release --series CUSR0000SAF11 --threshold 1.0
python -m decisiondesk.monitor run [--live]
pytest -q && ruff check src tests scripts app
```
