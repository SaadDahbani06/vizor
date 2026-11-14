# Main Street Decision Intelligence — HackPrinceton starter

**Product:** A data-driven decision copilot for *both financial and operational choices*.

Organization uploads structured internal data → system selects external evidence where relevant → forecasts and tests scenarios → ranks feasible decisions → explains uncertainty and assumptions → (later) monitors and alerts.

## Hackathon demo (Milestone 4.5: final release)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt             # = pip install -e '.[dev,ml,ui,agent,messaging]'
cp .env.example .env                        # optional keys: GEMINI_API_KEY, PHOTON_*, FRED_API_KEY, NESSIE_API_KEY
python -m decisiondesk.precompute           # warm the validated forecast cache (about 2 min once)
streamlit run app/streamlit_app.py          # overview, forecast, economy, decisions, analyst, alerts, messaging, banking
python -m decisiondesk.messaging.worker run # answer iMessages from opted-in owners (Photon 2.0)
python -m decisiondesk.product_demo         # both demo conversations end to end in the terminal
```

- **Business data:** realistic **synthetic** U.S. small businesses (`synthetic_v2`).
- **Economic data:** **real** FRED observations, point-in-time.
- **Banking:** **simulated** Nessie records.
- **Scenarios and effects:** user scenario changes are **hypothetical**; action effects are
  **assumed**.
- **The analyst:** Google Gemini (`GEMINI_MODEL`, default `gemini-3.8-flash`, manual function
  calling over 10 tools) when `GEMINI_API_KEY` is set. If Gemini fails, a rule-based offline
  analyst answers and the reply says so. Every number the analyst states is checked against
  executed tool outputs.
- **Messaging:** Photon 2.0 iMessage (shared line). It sends proactive risk alerts to opted-in
  owners and holds a two-way conversation by text. There is a clearly labelled local simulator.
  SMS is not provisioned.
- **Verification and setup:**
  - `docs/MILESTONE_4_5_RELEASE.md`: what ran live, Photon dashboard steps, and limitations.
  - `docs/PRESENTATION.md`: the two-minute script.
  - `docs/MILESTONE_4_PRODUCT_VERIFICATION.md`: earlier verification.

## What is actually implemented now?

- ✅ Offline **synthetic** restaurant and retail daily samples
- ✅ Auditable ingestion: explicit schema mapping, declared currency/timezone/metric definitions, file SHA-256, ingestion report (no raw rows), strict or `closed_zero` missing-day policy with `is_imputed` flag
- ✅ Leakage-safe origin-anchored lag/rolling/calendar features (perturbation-tested)
- ✅ Model leaderboard: seasonal-naive, 12-week weekday mean, Ridge, XGBoost, CatBoost (optional deps), fixed untuned hyperparameters
- ✅ Rolling-origin validation + untouched final test; MAE, RMSE, WAPE, bias, skill vs seasonal-naive, 80% interval coverage; baseline kept unless ML wins by ≥2% on validation
- ✅ 28-day cash risk from block-bootstrapped out-of-sample error paths, with a holdout calibration check (currently reports *not established*: only 6 windows)
- ✅ Decision engine (`src/decisiondesk/decision/`): baselines from the **selected** forecasting models, explicit scenario formulas, common-random-number simulation, constraints, deterministic ranking for a declared objective, sensitivity analysis, evidence/provenance (`python -m decisiondesk.decide`)
- ✅ Restaurant actions: menu price increase (constant-elasticity demand, assumed range), supplier cost negotiation (uncertain success), discretionary spending cut (possible demand loss)
- ✅ Retail actions: weekly-review inventory policies (order-up-to service level, maintain stock, reduced purchasing, no order) with lead time, holding cost, lost sales, budget/storage/stockout constraints; `Unsupported` when inventory fields are missing
- ✅ Objectives: max expected profit, min operating cost, min cash-shortage risk (requires user-confirmed starting cash), min stockout risk; weighted mixes only with declared weights
- ✅ FRED/ALFRED connector with **point-in-time vintages** (live-tested), key-safe errors, raw-payload cache for offline re-runs
- ✅ Point-in-time macro features aligned to each forecast origin (as of the last observed day; YoY within the same vintage)
- ✅ Ablation: internal-only vs internal + FRED on identical folds/holdout (`python -m decisiondesk.ablation`)
- ✅ Canonical row-level transaction schema (signed amounts, internal-transfer flag, cash vs scheduled) and cash-basis daily aggregation
- ✅ Nessie sandbox (prod-api) seeded live with a fictional restaurant: 1 customer, 2 accounts, 2 merchants, 32 deposits, 8 withdrawals, 12 purchases, 4 transfers, 4 bills; importer round-trips 56/60 records with exact dates and amounts (bills unreadable, see below)
- ✅ SEC companyfacts client (raw, unmapped) with missing-key handling
- ✅ Real U.S. data (Milestone 3.5): Kwick365 independent-restaurant index connector (live, cached; REAL AGGREGATE), real-data leaderboard on daily orders per restaurant, with/without point-in-time FRED on identical windows (`python -m decisiondesk.us_validation kwick365`); see `docs/MILESTONE_3_5_US_VALIDATION.md`
- ✅ Business profile confirmation (industry, U.S. state, objective, constraints), CSV/Excel column-role mapping, supported-analysis detection (`profile.py`); source-selection catalog with literal status (`sources.py`)
- ✅ End-to-end restaurant demo: real FRED + Kwick365 context → selected-model forecasts → Milestone 3 decision engine (`python -m decisiondesk.us_demo restaurant --state TX --confirm`)
- ✅ Tests: 138 offline/mocked pass (incl. 12 Gemini, 20 Photon messaging/webhook/simulator, 2 cache + Messaging-tab) + 10 opt-in live (`MSRD_LIVE=1 pytest -m live`); audits in `docs/MILESTONE_2_VERIFICATION.md`, `docs/MILESTONE_3_VERIFICATION.md` and `docs/MILESTONE_4_5_RELEASE.md`
- ❌ No real individual-business SALES history evaluated (none available); real AGGREGATE restaurant data evaluated (Kwick365); synthetic scores say nothing about real accuracy
- ❌ Iowa store-level purchase validation and procurement demo: importer built and tested on a schema fixture, but BLOCKED until the dataset-1051 export is downloaded to `data/private/iowa_liquor/`
- ❌ Calibrated cash-shortfall probability (needs ≥20 holdout windows)
- ❌ Hyperparameter tuning, SARIMAX/time-series models, holiday features, per-horizon reports
- ❌ Importing Nessie bills: the server returns 400 when listing bills it accepted (its own response model requires `recurring_date`/`upcoming_payment_date`), so rent/linen/utilities are missing from the import and cash-basis fixed costs are understated by $4,380
- ❌ Evidence that FRED features help any forecast: they did not pass validation on the synthetic businesses (which have no economic dependence by construction)
- ❌ Merging Nessie transactions into the model pipeline (28 sandbox days ≪ the 504 days the leaderboard needs)
- ❌ Estimated (not assumed) action effects: price elasticity, supplier success and demand side effects are assumptions; the data has no price or supplier variation to estimate them
- ❌ Cash-shortage modeling for inventory decisions (needs payables timing); continuous action optimization (discrete candidates only); realistic synthetic inventory receipts
- ✅ Realistic synthetic v2 businesses (Austin TX restaurant, Columbus OH hardware store): holidays, events, promotions, supplier-cost drift, recurring bills, inventory receipts, declared opening cash + ledger
- ✅ Tool-calling AI analyst: Gemini (`google-genai`, manual function calling, multi-turn memory, model fallback chain, request budget); Claude kept as an alternative; rule-based offline fallback. Numeric grounding check with one automatic rewrite. Tools: overview, forecasts, FRED, scenarios, inventory, cash risk, alerts, banking
- ✅ Streamlit interface (upload + mapping + confirmed profile, overview, forecast, economy, decisions, analyst chat showing the active analyst, alerts, messaging, Nessie banking)
- ✅ Photon 2.0 iMessage (`messaging/`): recipients authorized only from opted-in Photon assignments, replies only in owner-started conversations, message-ID dedupe and idempotency keys, STOP/START/HELP/OPTIONS, alert consent and preferences, daily send cap, delivery shown only when Photon confirms `delivered`, masked phone numbers, Standard Webhooks verification, local simulator. Live read-only status verified; live send/receive not yet run
- ✅ Validated leaderboard disk cache (keyed by data, config, model list, forecasting source code and library versions): decision view 42.5 s → 0.5 s with identical outputs; `python -m decisiondesk.precompute`
- ✅ In-app monitoring: FRED new/revised releases and cash-shortfall rules, deduplicated, persisted, evidence-backed; Telegram opt-in coded
- ✅ Nessie as a selectable SIMULATED banking source: import, categorize, missing-bill detection, not used for forecasts
- ❌ Claude mode not run live (no Anthropic credential); Telegram not sent live
- ❌ Live Gemini was unreliable on 2026-11-14 (3.8-flash 503/504; 3.5-flash about 2 min per tool-calling turn); see the release doc for exactly what passed
- ❌ SMS (needs a Photon number + approved 10DLC campaign); public webhook endpoint (polling is used)
- ❌ News/event feeds

See `docs/ARCHITECTURE_REVIEW.md` for the problems found and the build plan.

## Where each number comes from

| Data | Origin | Can it support accuracy claims? |
|---|---|---|
| `data/examples/*_SYNTHETIC.csv`, `make_sample()` | **Synthetic business data** generated by us | No. Tests software only |
| FRED/ALFRED series (CPI, food CPI, UNRATE, retail sales) | **Real published economic observations**, with vintage dates | Real inputs, but only useful if a real business history responds to them |
| Kwick365 readings | **Real aggregate** of independent restaurants running KwickPOS (not a probability sample) | Only for the aggregate benchmark itself, labelled REAL_AGGREGATE_INDUSTRY |
| Iowa Liquor Sales (dataset 1051) | **Real store-level purchases** (not customer sales), CC BY 4.0 | Procurement forecasts for that store only (not run yet: download needed) |
| Nessie sandbox records | **Synthetic banking records** in Capital One's simulator, generated from our synthetic restaurant | No. Demonstrates import/normalization only |

## Quick start (macOS / Linux, Python 3.11+)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m decisiondesk.demo --industry restaurant
python -m decisiondesk.demo --industry retail
python -m decisiondesk.decide --scenario restaurant                     # full 9-section decision report
python -m decisiondesk.decide --scenario restaurant --objective min_cash_shortage_risk --starting-cash 200
python -m decisiondesk.decide --scenario retail --budget 45000           # constraints are user-declared
python -m decisiondesk.leaderboard --industry restaurant --target revenue
pytest -q
MSRD_LIVE=1 pytest -q -m live          # live FRED/Nessie/Kwick365/Photon (read-only) + real Gemini calls
python scripts/verify_milestone2.py   # read-only audit evidence
# optional boosting models: pip install -e '.[ml]'
```

External data (keys go in `.env`, never in code or chat; see `.env.example`):

```bash
# FRED: point-in-time ablation (first run downloads + caches vintages to data/cache/fred/)
python -m decisiondesk.ablation --industry restaurant --target revenue
python -m decisiondesk.ablation --industry retail --target units_sold --offline   # cached payloads only

# Nessie sandbox (SYNTHETIC banking data)
python -m decisiondesk.nessie_sandbox plan            # offline: what would be created
python -m decisiondesk.nessie_sandbox init            # dry run: reads only
python -m decisiondesk.nessie_sandbox init --confirm  # creates missing records (idempotent)
python -m decisiondesk.nessie_sandbox import          # GET -> data/cache/nessie_transactions_SYNTHETIC.csv
```

Nessie notes (verified live on `https://prod-api.nessieisreal.com` with a 32-hex key; `nsk_…` keys were rejected on both hosts):
- GET endpoints return `200 []` even for an invalid key, so a successful GET does **not** prove a key works; only a write does.
- Historical `transaction_date`/`purchase_date` values are stored as sent (0 date mismatches on import).
- Deposit/withdrawal/purchase amounts are integers (we send whole dollars).
- Transfers cannot name a receiving account, and come back with `id` (not `_id`) and no payer/payee. Internal transfers are therefore seeded as a transfer out of checking plus a tagged deposit into savings; both legs are flagged internal and excluded from revenue/cost.
- Listing bills fails server-side (400) once a one-time bill exists. `init` never creates records it cannot first check for, so re-runs stay idempotent.

On Windows use `.venv\\Scripts\\activate` in a suitable terminal.

For your own daily-aggregate CSV with `date,revenue,variable_costs,fixed_costs`:

```bash
python -m decisiondesk.demo --csv path/to/business.csv --starting-cash 12000 --currency USD
# closures in the calendar? declare them explicitly:
python -m decisiondesk.leaderboard --csv path/to/business.csv --currency USD --missing-days closed_zero --target revenue
```

**Unit conventions:** all cash metrics are amounts in one currency, costs are positive magnitudes, one row per date, no duplicates; gaps raise unless `--missing-days closed_zero`. The baseline needs 56+ days; the leaderboard needs at least 504 days at a 28-day horizon with 6 test folds (fewer with `--holdout-folds`/`--horizon`). Leaderboards are written to `artifacts/` (gitignored, may contain derived private metrics). The CSV mode requires explicit starting cash. Row-level transactions (currently Nessie only) are normalized by `decisiondesk.transactions` on a cash basis; do not mix cash-basis series with accrual-style daily fixed costs. Operational features need `units_sold` and `inventory_on_hand`.

## Claude Code

1. Open **this folder** in VS Code with the Claude Code extension.
2. Claude will pick up `CLAUDE.md` from the repository root.
3. Paste `CLAUDE_CODE_MASTER_PROMPT.md` or invoke `/build-core`, `/review-apis`, `/train-models`, `/build-decisions`, `/build-agent`, `/verify-demo` progressively.
4. Check actual output of `pytest -q` and the demo. Do not claim advanced accuracy on synthetic data.

## Teammate API review

Send `docs/API_REVIEW.md` and `docs/api_candidates.csv` to the teammate responsible for checking sources. All credentials remain optional in `.env.example`.

## Reliability limitation

This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis.

Restaurant benchmarks: Kwick365 — The Independent Restaurant Index, https://kwick365.com (independent restaurants running KwickPOS; figures carry their date and sample size). Iowa data: Iowa Department of Revenue, Alcohol & Tax Operations Division, Iowa Liquor Sales (Iowa Data Hub dataset 1051), CC BY 4.0.

FRED observations and SEC XBRL contain real economic and public-company data, not a substitute for the organization's own KPI histories. This starter does not establish which intervention would *cause* an improved outcome. Model assumptions must be stated. Data privacy is important: do not commit real uploads or secrets.
