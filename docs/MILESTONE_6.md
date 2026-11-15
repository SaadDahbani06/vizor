# Milestone 6: final product: premium dashboard, fast analyst, proactive Photon agent

Branch `milestone-6-final-product` (from 5.5). Committed locally, not pushed or deployed. Data:
the demo business is synthetic and labelled "Demo Business · Sample Data".

The LEGR screenshots and Devpost text were not attached to the request. The visual direction
comes from the written description (cream background, black borders, bold large figures,
high-contrast cards, editorial details). Nothing was taken from LEGR's branding or assets.

## 1. What changed

### Website (four pages; Economy, Messaging and Evidence stay removed)

- **Design system** (`ui/theme.py`):
  - warm cream canvas (#F3EEE3), cream cards with 1.5 px ink borders and a 4 px offset ink shadow;
  - Fraunces serif for headings and Inter Tight for bold figures;
  - one dark hero card, plus green, blue, amber and red tints only where a status needs them;
  - the chart palette passes the colour checks on the cream card (one contrast warning for the
    orange, which always appears with a legend or a table).
- **Header:** Main Street Risk Desk brand with the "Demo Business · Sample Data" label (its
  tooltip explains the synthetic data), the four pages, a business selector, the bell and
  Settings.
- **Overview:**
  - a one-sentence briefing;
  - a **Business attention** bar that shows the top alert with its action button
    (Investigate, Simulate decision or Review), plus up to two more alerts;
  - six KPI cards: revenue (dark hero), operating profit, cash available, expenses (with the cost
    rising most), inventory health and upcoming obligations;
  - revenue and profit trend, product performance, and expense breakdown;
  - a **Recommended decision to review** card with expected impact, downside and upside, key
    risk, and a Decision Lab button.
- **Investigate** (the Observe → Detect → Explain step):
  - from a product alert, the product view opens with "Why this was flagged": recent orders
    against a typical weekday, and a note that this shows *what* changed, not *why*;
  - buttons to simulate a price change, simulate ordering more, or ask the assistant;
  - a sales-drop alert opens Sales & profit with each item's usual-versus-yesterday table.
- **Decision Lab:**
  - before-and-after table, profit impact with a downside/expected/upside bar, and the owner's
    price limit;
  - a new **Review action** panel: 1 what to do, 2 why it may help, 3 estimated impact, 4 main
    risks, 5 what to check before acting;
  - **Approve for my records**, which needs an "I've reviewed the risks" tick and writes to a
    local decision log (`data/cache/decision_log.json`, gitignored, with
    `executed_automatically: false`). Nothing is executed. There is no approve button when the
    change would lose money or break the owner's limit.
- **My Business** and **Local Market**: as in 5.5, restyled.

### Fast, reliable analyst (`agent/agent.py`, `agent/gemini.py`, `agent/tools.py`)

Each question takes the cheapest path that gives a verified answer:

1. **Fast facts**: yesterday, this month, cash, bills, best seller, current price. These come from
   the new read-only `get_key_numbers` tool, with no model call.
2. **Deterministic engines**: scenarios, follow-ups ("3% instead", "which is safer"), the
   briefing, local prices, profit drivers, cash outlook. Gemini then writes **one** short
   explanation of the verified results, with no tools.
   - If the explanation contains an unverified number, the verified engine text is shown instead.
3. **Gemini's tool loop**: only for open questions the engines don't recognise. At most 4 rounds,
   compact tool results, and a 40 s overall deadline.
4. **Failures fall back immediately**:
   - no silent SDK retries;
   - a 90 s circuit breaker per model after a 429, 5xx or timeout;
   - a fallback chain: gemini-3.5-flash-lite, gemini-3.1-flash-lite, gemini-3.5-flash;
   - if the model is unreachable on an open question, the owner gets their key numbers, not a
     menu of commands.
5. **Read-only tool results are cached** per scenario state. Scenario simulations always rerun.
6. **Chat:** answers that finish within 3 s appear immediately; longer ones show a status that
   refreshes every second. Duplicate submissions are blocked while a question is running.
   Replies link to the matching dashboard view.

### Photon (`messaging/service.py`, `briefing.py`, `messaging/worker.py`)

- **Notification levels** on every briefing alert:
  - **routine**: dashboard and optional daily briefing only;
  - **important**: texted only if the owner chose the "important" level;
  - **decision needed**: texted to owners who opted in to alerts.
- **"Decision needed" text:** for example "Decision needed: Tortillas may run out before the next
  delivery. … Reply DETAILS for the numbers, OPTIONS for what you could do, or STOP. Nothing is
  ordered or changed automatically."
  - Sent at most 2 per run, once per alert per day, never after STOP, and numbers are checked
    first.
- **DETAILS** replies with the last alert's figures and next step. **OPTIONS** runs the matching
  analysis (more ingredients for a stock alert, cash outlook for a cash alert). The last alert is
  saved, so both work after a restart.
- **Phone replies** no longer carry the "Offline analyst" prefix or markdown stars. The analyst
  mode is still recorded in each message's evidence.
- **State migration:** old state files load cleanly (unknown fields are ignored), and the retired
  `economic_release` preference is removed.
- **`worker alerts`** sends "decision needed" texts plus the existing risk alerts. **`worker
  briefing`** previews the morning text; `--send` sends it to opted-in owners (see section 4).

## 2. Before and after: the interface

| | Before (5.5) | After (6) |
|---|---|---|
| Look | Generic light SaaS: white cards, soft shadows | Editorial cream and ink: bordered cards, offset shadows, serif headings, bold figures |
| KPI cards | 5 equal cards | 6 cards with roles: dark revenue hero, tints only for status (profit up, cash covers bills, stock healthy or at risk) |
| Alerts | A list inside the briefing card | **Business attention** bar above the KPIs, with Investigate / Simulate decision / Review |
| Charts on Overview | Trend only | Trend, product performance, expense breakdown |
| Decision | Opportunity card | Recommended decision with expected impact, key risk and downside |
| Decision Lab | Risks and next step | **Review action** in 5 steps plus recorded approval |
| Investigate | Opened the page | Opens the evidence: "Why this was flagged" with recent-versus-typical charts |
| Header | Brand; Settings menu held the business switcher | Brand, four pages, **business selector**, bell, Settings |

Screenshots were taken in headless Chrome at 1440 px and 1280 px (laptop) with the offline
analyst: Overview, Decision Lab, My Business products, and Local Market. Issues found during the
review and fixed:
- the navigation wrapped onto two lines at 1440 px;
- the bell and Settings labels were truncated;
- the revenue sparkline had a white box behind it on the dark card;
- product bar labels were clipped;
- the brand name wrapped at 1280 px.

## 3. Gemini response time: measured before and after

All runs used the real Gemini API with this project's key, the synthetic demo restaurant, and
warm forecast caches, on 2026-11-14. Supported models were checked with `models.list`: gemini-3.8,
3.7, 3.6 and 3.5 Flash, plus 3.5 and 3.1 Flash-Lite.

**Before** (original pipeline: gemini-3.8-flash, 8 tool rounds, 60 s timeout, SDK retry,
three-model chain), 7 questions:

| Question | Time | What happened |
|---|---|---|
| How much revenue did we make yesterday? | 19.6 s | 3.8 and 3.7 returned 429 (quota); 3.5 Flash took 2 calls (7.0 s + 6.8 s) |
| Which product sold the most? | **74.9 s** | 3.7 returned 429 after **61.3 s** (SDK retry and backoff); 3 more calls; final answer had an unverified number |
| How much cash do we have? | 9.3 s | All three models 429 → offline |
| What happens if taco prices rise by 5%? | 16.9 s | 2 × 429, 1 call ok, then 429 → offline |
| What about 3% instead? / Which is safer? / Why did profit drop? | 2.4 / 9.0 / 8.3 s | All ended in offline fallbacks |

Median **9.3 s**, worst **74.9 s**, **6 of 7** fell back to offline, 1 unverified answer. Tool
time was 26–372 ms per call. The time went into failed model requests, SDK retries, and several
model rounds per question, not into our calculations. This explains the earlier 2 min 31 s.

**After**, 16 mixed questions (fast facts, briefing, scenarios, follow-ups, open questions):

| Path | Questions | Median time | Model calls |
|---|---|---|---|
| Fast facts | 3 | **0.01 s** | 0 |
| Engine only (incl. "which is safer", discarded explanations) | 5 | 0.84 s | 0–1 |
| Engine + one Gemini explanation | 6 | **2.1 s** | 1 |
| Gemini tool loop (open question) | 1 | 7.4 s | 3 |
| Model unreachable (rate-limited) | 1 | 0.24 s | 2 failed fast |

Overall: median **1.5 s**, p95 **2.9 s**, worst **7.4 s** (n = 16, so p95 is the second-slowest
answer). The successful-answer rate was 15 of 16. In the 16th case the model was rate-limited; at
the time that answer was the generic help text, and the outage path has since been changed to
return key numbers. One open-question answer had a number that could not be verified, and it
was flagged to the user. Explanations were rejected for unverified numbers 2 times, and the
verified engine text was shown instead.

Also measured: with gemini-3.5-flash-lite as the model, every explained scenario took 1.1–1.4 s.
With the default left at 3.8 Flash (quota exhausted), the circuit breaker gave up in 0.1 s and the
verified answer came back in 0.6 s.

**Model choice:** the default is now `gemini-3.5-flash-lite`, the fastest model that had quota.
`.env` currently sets `GEMINI_MODEL` to a model whose free-tier quota was exhausted on the test
day. The fallback chain handles it, but removing that line (or setting it to
gemini-3.5-flash-lite) saves a failed request after each 90 s breaker window.

Limits:
- Free-tier per-minute limits were reached after about 12 requests in about 30 s.
- Live latency depends on Google's service.
- The p95 comes from 16 questions.

## 4. Photon: integration and reliability

| Check | How | Result |
|---|---|---|
| Credentials and connection | `worker status` (read-only, masked) | connected; shared iMessage line •••8199; 1 authorized recipient •••4982 (opt-in received 2026-11-14); no SMS lines |
| Consent state | Read the state file (masked) | authorized, alerts **opted out**, not stopped, conversation exists; 1 earlier outbound message, `delivered` |
| Single poller | Lock holder check | nobody polling; `run`, `alerts` and `briefing --send` take the lock; the dashboard's auto-check takes it too |
| Inbound parsing, idempotency, duplicates, STOP/START, daily cap, delivered vs accepted | Existing mocked tests (`test_messaging.py`, 30+ tests) | pass |
| Decision-needed texts, levels, once per day, STOP, DETAILS/OPTIONS, persistence across restart, old-state migration | New tests (`test_milestone6.py`) | pass |
| Shared scenario between dashboard and phone | Existing tests (published scenario file) | pass |
| Secrets | Keys never printed; phone masked in status, logs and docs | ok |
| **Live phone round trip** | Needs you to text the line | **not yet verified in this milestone**; steps below |

**Phone-side test:**
1. From the authorized phone, text the shared Photon line: "How did we do yesterday?"
2. Run `python -m decisiondesk.messaging.worker run --once`.
3. The reply should arrive within seconds; it is a fast fact, so no model is needed.
4. Then text "What happens if taco prices rise 5%?", then "DETAILS", then "STOP", and run
   `run --once` after each.

Status is reported only as Photon returns it: "delivered" only when Photon confirms delivery.

**Morning briefing:** `python -m decisiondesk.messaging.worker briefing --send`
- It sends only to owners who opted in to alerts **and** to the daily briefing, never after
  STOP, once per day, and within the daily cap.
- **Schedule it with launchd or cron.** Example crontab line:
  `0 8 * * * cd ~/Desktop/main-street-decision-intelligence && .venv/bin/python -m decisiondesk.messaging.worker briefing --send`
- This runs only while the laptop is awake. It is **not** an always-on production service, and
  scheduled sending has not been tested end to end with a real phone.

## 5. Proactive workflow

**Observe → Detect → Explain → Simulate → Recommend → Request approval**

| Step | Where |
|---|---|
| Observe | Records → `daily_briefing` |
| Detect | Alerts with severity and **level**: sales decline, item mix shift, ingredient cost share, low stock, sell-out risk, cash below bills, bills due |
| Explain | Investigate → "Why this was flagged": recent versus typical, what changed (not why) |
| Simulate | Simulate decision → the Decision Lab, preset to that item and kind of change |
| Recommend | Recommended decision card; Review action in 5 steps; best tested option |
| Request approval | Tick, then "Approve for my records" (logged, never executed); by phone: decision-needed text, then DETAILS / OPTIONS |

At most 5 alerts appear in the dashboard and at most 2 texts per run, and routine items are
never texted. These limits avoid notification fatigue.

## 6. Tests

- `pytest -q`: **274 passed**, 10 skipped (opt-in live tests, not re-run). Ruff is clean.
- **New:**
  - `tests/test_milestone6.py` (22): fast facts and their numbers, engines and follow-ups, tool
    cache, no model request for fast facts, one request without tools for explanations,
    unverified explanation falls back, open-question tool loop, circuit breaker, outage answer,
    deadline, notification levels, opt-in, once per day, STOP, DETAILS/OPTIONS, restart
    persistence, state migration;
  - UI tests: Business attention to Investigate to Simulate; Review action approval recorded and
    not executed, with no approval button outside the owner's limit.
- **Updated:** UI labels, the business selector, and provider tests pinned to the model tool loop
  (`MSRD_ROUTING=llm`).
- **Engines preserved:** forecasting leaderboards, rolling-origin validation, the 5.1
  whole-window Monte Carlo, calibration, reconciliation, scenario consistency (Tacos +5% =
  +$286, −$5 to +$557, 95%), sensitivity, decision ranking and the number check are unchanged
  and covered by the existing tests.

## 7. Startup

```bash
cd ~/Desktop/main-street-decision-intelligence
git switch milestone-6-final-product
python -m decisiondesk.precompute       # seconds when warm
./start.sh                              # opens http://localhost:8501; Gemini if GEMINI_API_KEY is set
./start.sh offline                      # backup: no AI model, every answer instant and verified
# optional, second terminal (only one poller at a time):
python -m decisiondesk.messaging.worker run          # answer iMessages
python -m decisiondesk.messaging.worker alerts       # decision-needed texts (opted-in owners only)
```

## 8. Built versus planned

**Built and verified in this milestone:**
- the redesigned four-page site;
- fast routed analyst with measured latency;
- engine-plus-explanation path;
- fallbacks, breaker and deadline;
- read-only tool cache;
- Investigate panels;
- Review action and recorded approval;
- notification levels, decision-needed texts, DETAILS/OPTIONS, persistence (mocked tests);
- read-only Photon status.

**Built but not verified live:**
- the phone round trip (steps in section 4);
- scheduled morning texts (needs launchd/cron and a phone);
- Gemini answers to open questions beyond the one measured.

**Planned:**
- real-business onboarding (`docs/ONBOARDING.md`);
- always-on hosting for the worker and scheduler;
- streaming model output;
- per-ingredient supplier changes;
- labour scenarios.

## 9. Open issues

- `.env` pins an exhausted Gemini model (see section 3). It works, but is wasteful.
- On the free tier, bursts of questions hit per-minute limits. The app then answers from the
  engines. A paid key, or spacing questions out, avoids it.
- Gemini's open-question answers can still contain numbers the check cannot verify. They are
  flagged in the reply, never hidden.
- The chat panel overlays the page below 1500 px.
