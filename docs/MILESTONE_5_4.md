# Milestone 5.4: a simpler product with a financial analyst

Branch `milestone-5.4-simple-analyst` (from 5.3, commit `7495229`). Committed locally, not
pushed or deployed. Data: SYNTHETIC demo businesses.

## What changed

**Four pages** (was eight): Overview, My Business, Decision Lab (renamed from Scenario Lab), Local
Market.

- **Overview.** A greeting and yesterday versus a typical same weekday; today's briefing (up to
  five alerts, each labelled observed, forecast or simulated, each with a next step and a link);
  KPI cards (revenue, operating profit, expenses, cash, inventory health, bills due in 7 days),
  each with a "How was this calculated?" popover and a link to the matching section; the sales
  and profit trend; changes worth investigating; one decision to consider, with a checklist and
  "Open in the Decision Lab".
- **My Business.** Sections: Sales & profit (clickable metrics that switch the chart and its
  explanation), Products (table, then one product at a time), Inventory (sell-out risk,
  ingredients, recipes), Expenses (where the money goes, what changed profit), Cash & bills
  (ledger, bills, on-demand 28-day cash simulation), Forecasts.
- **Decision Lab.** Plain-language controls: price, ingredient orders, one-time purchase,
  **supplier ingredient prices (new)**, customer demand, optional marketing spending, horizon.
  Current plan versus your change: revenue, profit, cash flow, demand, units sold, sell-out risk,
  gross margin, ingredients left. **Before you decide**: what, why it might help, expected impact,
  how uncertain, assumptions, what could go wrong, verify-first items as checkboxes, and approval
  required. **Assumptions and risks** expander. The whole-business engine is in an expander
  (open by default for the retailer).
- **Local Market.** Unchanged data and rules; its buttons now open the Decision Lab.

**Your Financial Analyst** (sidebar, every page): message bubbles and history, an input box,
send, page-specific suggested questions, a "thinking" indicator while Gemini runs in the
background (the dashboard stays usable), visible error messages, Clear chat. It knows the page
and the selected product. Follow-ups work in the offline analyst and are passed as conversation to
Gemini:
- "What about 3% instead?" reruns the same product and the same kind of change;
- "Which is safer?" compares the last two scenarios;
- "Why would a price increase lower demand?" explains the assumed price sensitivity;
- "Why did revenue fall yesterday?" and "What should I focus on tomorrow?" use the briefing.
Each reply says which calculations it used and whether every number was verified.

**Notifications without a Messaging page.** A bell in the header lists today's alerts. Under
Settings → Notifications:
- opt in or out of alerts;
- choose alert types;
- opt in to a morning briefing by iMessage;
- send today's briefing to your phone (needs a confirmation checkbox);
- send a test message;
- check risks and send opted-in alerts;
- the scenario used for iMessage answers;
- the conversation and message history;
- the simulator.

Everything from 4.5 and 5 is kept: Photon, the worker, opt-ins, STOP/START, the daily cap,
delivered-only status, the single-poller lock and scenario publishing.

**Scheduled morning briefing.** No timer inside the dashboard: that would be unreliable.
`python -m decisiondesk.messaging.worker briefing --send` sends it only to phones that:
- opted in to alerts **and** to the daily briefing;
- have not replied STOP;
- have not already received that day's briefing.

It also respects the daily cap, takes the poller lock, and checks every number before sending.
Schedule it with cron or launchd (example in `messaging/worker.py`). Without `--send` it only
prints the text.

**Removed from the UI:**
- the **Economy** page and all FRED/macro UI;
- FRED from the analyst's tool list (14 tools, was 15);
- the economy route in the offline analyst, which now says national indicators are not part of
  the product and offers a supplier-cost test instead;
- economic alerts from the default alert scan;
- the **Evidence & Data** page, whose content moved to Settings → Advanced diagnostics (model
  validation, uncertainty calibration, reconciliation, assumptions, Nessie banking, cash alert
  rules) and Settings → Data.

**FRED backend kept**, deliberately. It is used by `ablation.py`, `us_validation.py`, `us_demo.py`,
the business-wide engine's stress test and `test_fred_macro.py`. Removing it would break those
workflows and tests, so per the brief ("remove only if safe") it stays, unused by the dashboard.

**Premium light design.** Warm white canvas, white cards with hairline borders and soft
shadows, rounded corners, Inter, pill navigation. The product palette passes the colour-blind and
contrast checks on the new white surface (`validate_palette.js`: all PASS).

**Research and plans:** `docs/MILESTONE_5_4_RESEARCH.md` (audience, jobs to be done, regulatory
facts, competitors, recommended segment) and `docs/ONBOARDING.md` (real-business onboarding).

## New calculations, and how default numbers were protected

- `ProductScenario.ingredient_cost_change_pct` (−50 to +100, default 0). It multiplies ingredient
  purchase prices, cost of goods, other food cost and the value of stock left. Customer demand,
  sales and revenue do not change. Tests: a 0% change is **bit-for-bit identical** to the
  baseline; profit falls by exactly the extra cost; the effect is linear (20% ≈ 2 × 10%).
- Briefing alerts (`briefing.py`):
  - sales decline;
  - product mix shift;
  - ingredient cost share of sales (last 7 days vs the 28 before, alert at +1.5 points);
  - ingredient running out before the next delivery;
  - simulated sell-out risk ≥ 25% in 7 days;
  - cash below bills due in 7 days;
  - bills due.

  Sorted by severity and capped at 5, with the number suppressed reported.
- `decision_checklist()` is shared by the briefing, the Overview card and the Decision Lab.
- Default numbers are unchanged. Tacos +5% is still **+$286 (−$5 to +$557), better in 95%**; the
  business-wide engine and all 225 earlier tests still pass. Owner alert defaults now exclude
  economic releases and daily briefings: new owners get neither unless they opt in.

## Test results (2026-11-14)

```
pytest -q    →  248 passed, 10 skipped (opt-in live tests), 0 failed   (~54 s)
ruff check   →  clean
```

New or rewritten in 5.4:
- `tests/test_simple_analyst.py` (17): cost shock, alerts, checklist, follow-ups, page context,
  briefing by iMessage (confirmation, opt-in, STOP, once a day), alert defaults, no FRED in the
  scan.
- `tests/test_dashboard_ui.py` (15): four pages, no Economy, Messaging, Evidence or FRED text on
  any page, KPI links, popovers, clickable metrics, Decision Lab controls, supplier cost slider,
  publishing, sidebar chat (multi-turn with page context, suggestions, slow Gemini in the
  background, visible errors), Settings outside the navigation.
- Updated navigation in `test_product.py`, `test_release.py`, `test_market_briefing.py` and
  `test_gemini.py` (tool count 14).

Live tests were **not** re-run in 5.4 (no live Photon or Gemini claims are made). During
development, one ad-hoc headless run picked up the Gemini key from `.env` and sent one demo
question ("What if I raise taco prices 5%?", synthetic data only) to Gemini in the background;
the test suite itself blanks all keys.

**Browser check:** all four pages and Settings were rendered in headless Chrome at 1440 px
(offline analyst, fast models). There were no exceptions; first load took about 3 s and page
switches under 0.4 s. The sidebar stays at Streamlit's default width (about 300 px); the chat is
readable but narrow.

## Launch

```bash
cd ~/Desktop/main-street-decision-intelligence && source .venv/bin/activate
git switch milestone-5.4-simple-analyst
python -m decisiondesk.precompute                      # seconds when warm
MSRD_LLM_PROVIDER=offline streamlit run app/streamlit_app.py     # instant answers on stage
# or without MSRD_LLM_PROVIDER to use Gemini (answers in the background)
# optional iMessage worker (only one poller at a time):
python -m decisiondesk.messaging.worker run
python -m decisiondesk.messaging.worker briefing       # preview the morning text (sends nothing)
```

Deep links: `/?page=Decision%20Lab&product=Tacos&price=5&cost=8`,
`/?view=settings&section=Advanced%20diagnostics`.

## Limits (unchanged unless noted)

- All accuracy and calibration figures come from synthetic history.
- Price sensitivity, customers switching dishes and marketing response are assumptions.
- The supplier cost lever changes every ingredient by the same percent (new; per-ingredient
  changes are a follow-up).
- Product features work only on the demo restaurant (see `docs/ONBOARDING.md`).
- The offline analyst understands a fixed set of question patterns; free-form questions need
  Gemini, which has taken 2–3 minutes per tool-calling answer.

## What a real business still needs (priority order)

1. Catalog refactor and item-sales importer (`docs/ONBOARDING.md` steps 1–2).
2. Recipes, ingredient counts and invoices importer (steps 3–4); per-ingredient supplier
   changes.
3. A real pilot: the same rolling-origin test on 2–3 restaurants, reported honestly.
4. Labor as a scenario lever (wage or hours change), since labor is the largest cost.
5. Revenue net of sales tax and tips at import (a hard check, not a guess).
6. Account login and per-business data separation before more than one owner uses one server.
7. A faster language model path for chat (streaming, or a smaller model), measured.
