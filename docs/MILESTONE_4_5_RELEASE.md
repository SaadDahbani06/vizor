# Milestone 4.5: final HackPrinceton release (2026-11-14)

Every result below was produced by running the code on 2026-11-14. The software stack:

- **Python** 3.13.3.
- **AI analyst:** google-genai 3.3.0.
- **Messaging:** photonhq-api 0.6.0 (Photon 2.0 Beta official client) and standardwebhooks 1.1.0.
- **Interface:** streamlit 1.67.0.

The business data is still **synthetic**, and nothing here is validated on a real small business.

## Summary

| Area | Status | Evidence |
|---|---|---|
| Gemini analyst (10 tools, grounding, memory, fallback) | **PASSED live: two-turn function calling with scenario memory, grounded** (slow: about 3 min for two turns); mocked tests pass | `agent/gemini.py`, `agent/agent.py`, `tests/test_gemini.py` |
| Photon iMessage: read-only connection, line and recipient status | **PASSED live** | `messaging/gateway.py`, live test |
| Photon two-way iMessage (reply to "What should I do?") | **Built; tested against the official request schemas with a fake transport. NOT yet run live** (needs the owner to text the line during a run) | `messaging/service.py`, `messaging/worker.py`, `tests/test_messaging.py` |
| Proactive risk alerts | **Built and tested (mocked); none sent live** | `messaging/risk_signals.py` |
| Streamlit Messaging tab | **PASS (headless AppTest, simulator round trip)** | `app/streamlit_app.py`, `tests/test_release.py` |
| SMS | **Not available:** no SMS number or 10DLC campaign on the project | live status shows 0 SMS lines |
| Webhooks | **Verification and dedupe library tested; no public HTTP endpoint shipped** (the demo uses polling) | `messaging/webhooks.py` |
| Performance | **Decision view: 42.5 s cold → 0.5 s from the validated disk cache, with identical outputs** | `leaderboard.build_leaderboard_cached`, `precompute.py` |
| Native crash at exit | **Not reproduced** (0 failures in 25 runs); unresolved | see below |

## Gemini analyst

**Model and pricing.**
- `GEMINI_MODEL` defaults to `gemini-3.8-flash`. The API's model list was checked with the configured key: generateContent, 1,048,576 input and 65,536 output tokens.
- The free tier is available, but Google may use free-tier prompts to improve its products. The paid tier is not used that way.

**How a question is answered.**
- **Manual function calling.** Automatic function calling is off, so our code executes each of the 10 tools against the business session.
- **History.** Model turns are kept verbatim, which preserves thought signatures. Each function response carries its call ID.
- **Grounding.** Every number in the reply must appear in a tool output or in the user's own text. If one doesn't, Gemini is asked once to rewrite. If it still fails, the reply is flagged "Unverified numbers".
- **Failures.**
  - A 429 or 5xx error before the conversation has a model turn can switch to `GEMINI_FALLBACK_MODELS` (default `gemini-3.7-flash,gemini-3.5-flash`).
  - Any other failure (auth, timeout, budget, or 5xx mid-conversation) answers with the rule-based offline analyst, prefixed "(Gemini unavailable: …)".
  - A local budget caps requests at 120 per hour. The timeout defaults to 60 s, and thinking level to `low` (`GEMINI_TIMEOUT_S`, `GEMINI_THINKING_LEVEL`).
- **Privacy.** Gemini sees tool outputs: aggregates, forecasts and decision tables. It never sees raw transaction rows or credentials.

The UI shows the active analyst ("Active analyst: Gemini analyst (model)" or the offline analyst) and a warning whenever a fallback happened.

**The offline analyst now answers "What should I do?" / OPTIONS.** It ranks the actions under the scenario already in memory. Before this release it returned its help text for that question.

## Photon 2.0 iMessage

### What the code does

- **Recipients are authorized only from Photon.** A handle is authorized only if the project's iMessage assignments list it with opt-in state `received`, meaning the person texted the agent line. Unknown senders are ignored and logged with a masked number.
- **The shared line only replies.** Photon's shared iMessage line cannot start a conversation. We reply only in the owner's existing conversation (space), threaded to their message.
- **Each inbound message is processed once.** IDs are persisted, so restarts and webhook/poll races don't repeat work. Each reply uses `Idempotency-Key: msrd-reply:<message id>`, and alerts use `msrd-alert:<dedup key>`.
- **Old messages are never answered.** The first sync marks history as processed.
- **Consent.**
  - Alerts need an explicit opt-in: the dashboard checkbox or texting START.
  - Each owner sets per-type preferences, and each alert is sent at most once.
  - STOP (and the standard synonyms) is saved immediately and blocks every message except its confirmation. START resumes. HELP lists the commands.
- **Limits.**
  - A daily send cap (`MSRD_MAX_MESSAGES_PER_DAY`, default 40) protects credits.
  - Replies are trimmed to 1,200 characters, and Gemini is told to keep iMessage replies to 600.
- **Delivery is reported only on `delivered`.**
  - `dispatched` and `accepted` are shown as such.
  - An `accepted` message with an acceptance code (for example `delivery_confirmation_unavailable`) is final and never shown as delivered.
- **Owners are isolated.** Each owner gets their own agent: their own conversation memory, scenario state and tool log. Fitted forecasts are shared read-only.
- **Secrets are protected.** Phone numbers are masked (`•••1234`) in the UI, logs and outputs. API errors are scrubbed of the key and project ID. Messaging state (which holds handles) lives in `data/cache/` (gitignored).
- **Alerts use only tool figures.** Signals cover cash shortfall, low runway, food-cost increase, FRED CPI release, bill-category jump, revenue-forecast decline and an infeasible inventory budget. Alert text must pass the same numeric grounding check before sending.
- **The event source is polling.** Polling the documented list-messages endpoint (every 4–5 s) needs no public URL. `webhooks.py` verifies Standard Webhooks signatures (`webhook-id`, `webhook-timestamp`, `webhook-signature`), dedupes by webhook ID, and re-reads the message from the API. Only a public HTTPS server needs it.

### Cost

On the project's plan, iMessage on the shared line has no per-message fee, so the iMessage demo does not use the $100 credit. SMS would cost a number ($2/month) plus 10DLC registration (about $15/month plus $20 per campaign submission) plus per-segment fees.

### What to configure in the Photon dashboard

These are already done for this project (checked live, read-only):

1. **Project and API key.** A Photon 2.0 project exists. `PHOTON_PROJECT_ID` starts with `pho_prj_` and `PHOTON_API_KEY` with `pho_sk_`; both are in `.env`.
2. **Recipient.** The shared iMessage line is active, and your phone (•••4982) is assigned to line •••8199 with opt-in `received`.

Needed for each new recipient:

3. **Add the recipient.** On the project's iMessage page, add their phone as a sender. Then have them text the assigned number from that phone. Until they do, the app shows them as not authorized.

Optional:

4. **Webhooks.** Only if you deploy a public HTTPS endpoint:
   - Add a webhook destination for the message events.
   - Copy its signing secret (`whsec_…`) into `PHOTON_WEBHOOK_SECRET`.
   - Photon does not deliver to localhost, so the local demo uses polling instead.
5. **SMS.**
   - Buy a U.S. number in the project.
   - Register a 10DLC brand and campaign, and wait for carrier approval.
   - Until then, the app shows "SMS is not provisioned" and no SMS code path runs.

`PHOTON_AGENT_USER_ID` is not needed. The agent's user ID is learned from the owner's own conversation.

## Live results (2026-11-14)

| Integration | What ran | Result |
|---|---|---|
| Photon read-only status | `pytest -m live -k photon`; `python -m decisiondesk.messaging.worker status` | **PASSED.** Project connected, shared iMessage mode, 1 recipient opted in, 0 SMS lines; no phone number or key in the output |
| Photon send / receive | not run | **Not run.** No live message was sent in this session, and no delivery is claimed |
| Gemini, `gemini-3.8-flash` | minimal prompt and tool-calling probes | **503 UNAVAILABLE / 504 DEADLINE_EXCEEDED on every attempt** (Google-side capacity) |
| Gemini, `gemini-3.5-flash` | one real tool-calling turn on the taqueria ("food costs +8%") | **PASSED.** Called `get_assumptions` and `evaluate_restaurant_actions`; grounded; 477 characters; **121 s** end to end |
| Gemini two-turn live test | `MSRD_LIVE=1 pytest -m live -k gemini` | **PASSED** on the second run (193 s). Turn 1: "food costs +8%" → `evaluate_restaurant_actions`, scenario 8%, grounded. Turn 2: "12% instead" → tool re-run with 12%, grounded. The first run's turn 2 got 503 → offline fallback → test SKIPPED (not counted as a pass); one SDK retry on 408/429/5xx was then added. The serving model was chosen by the fallback chain and not recorded |
| FRED, Nessie, Kwick365 | `MSRD_LIVE=1 pytest -m live -k "not gemini and not photon"` | **9 passed** (includes the Photon status test) |

## Tests

| Kind | Count | Command |
|---|---|---|
| Offline and mocked: analytics, decisions, product, Gemini (scripted fake client using real `google.genai.types`), Photon (fake transport validated by Photon's generated request models and `PlatformMessage` schema), webhooks (real signatures), Streamlit (AppTest) | **138 passed** (34 new in this release: 12 Gemini, 20 messaging, 2 cache/UI) | `pytest -q` |
| Live (opt-in; read-only except the real Gemini calls) | 10: **10 passed** (9 + Gemini on its second run) | `MSRD_LIVE=1 pytest -q -m live` |

Lint: `ruff check src tests scripts app` is clean.

Regression run on 2026-11-14:
- `python -m decisiondesk.demo --industry restaurant` works.
- `python -m decisiondesk.product_demo --offline` runs both conversations in 3.3 s.
- `python -m decisiondesk.decide --scenario restaurant` saves its report.
- `scripts/verify_milestone2.py` gives 12 PASS, 3 PARTIAL, 0 FAIL, unchanged. The partials are known Nessie limits.

## Performance

**Diagnosis.** The ~45 s first load was the leaderboard. It fits every candidate model on every rolling-origin fold for each target.

**The cache.** `build_leaderboard_cached` stores the fitted leaderboard on disk, keyed by a SHA-256 of:
- the exact data, target and evaluation config;
- the model list;
- the source of `features.py`, `models.py` and `leaderboard.py`;
- the numpy, pandas, scikit-learn, xgboost and catboost versions.

Any change to the data or the code means a refit, never a stale result. A test checks that a cache hit equals a fresh fit (same selected model, same metrics table except wall-clock fit time, identical residuals) and that a one-cent data change misses the cache.

**Warming and timings.**
- `python -m decisiondesk.precompute` warms all 9 dashboard boards in about 2 minutes.
- The restaurant decision view then loads in 0.5 s instead of 42.5 s, with identical decision outputs.
- Other tabs load lazily.

## Native crash at shutdown

An intermittent `std::system_error` at interpreter exit was seen once in five runs during Milestone 4. It was attributed to xgboost or catboost native thread pools. In 25 runs that fit both libraries and exit, it did not recur (0 failures), so it is **unresolved**. It happens only after results are produced and saved, so it does not affect any output.

## Limitations and blockers

- **Gemini.** Live Gemini reliability on 2026-11-14 was poor: 3.8-flash returned 503 and 504, and 3.5-flash took about 2 minutes per tool-calling turn. The app degrades to the offline analyst and says so, but a live demo may be slow.
- **Two-way iMessage.** The live round trip needs you at the phone. Run the steps below. Then check that the reply arrives and that the Messaging tab shows its status. Only `delivered` counts as delivered.
- **SMS.** Not provisioned, so the SMS registration rules are respected by not sending SMS at all.
- **Webhooks.** No public endpoint is deployed, so polling is the event source.
- **Data and effects.** All business data is synthetic. Action effects (price elasticity, supplier success) are assumptions, not estimated causal effects. Simulated shares are not guarantees.

## Launch commands

```bash
source .venv/bin/activate
pip install -r requirements.txt                    # = -e .[dev,ml,ui,agent,messaging]
python -m decisiondesk.precompute                  # warm caches (once per data/code change)
streamlit run app/streamlit_app.py                 # dashboard incl. Messaging tab
python -m decisiondesk.messaging.worker status     # read-only Photon check (masked)
python -m decisiondesk.messaging.worker run        # answer iMessages (polls every 4 s; Ctrl-C to stop)
python -m decisiondesk.messaging.worker alerts     # scan risks, send alerts to opted-in owners only
pytest -q                                          # offline + mocked suite
MSRD_LIVE=1 pytest -q -m live                      # live checks (makes real Gemini calls)
```

**Live two-way demo.** Run the worker, or turn on auto-check in the Messaging tab. Then text "What should I do?" from your opted-in phone to the agent line. The reply is threaded to your message and logged in the Messaging tab with its Photon status.
