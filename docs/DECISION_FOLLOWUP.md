# Decision follow-up (2026-11-15)

**Why it exists.** Vizor saves the prediction it made when the owner approved a decision and
checks it later against what actually happened. A chat with a general AI assistant (or a dashboard
made by dragging a file into one) ends when the answer is drawn, so it can't do this.

## What it does

1. **Saved prediction.** When "Approve for my records" is pressed in the Decision Lab, the
   simulation is frozen: for every simulated future, the product's daily orders and the business's
   daily profit before fixed costs, with the change and with no change. Nothing is refitted later
   (`src/decisiondesk/decision/followup.py`, `make_receipt`).
2. **Owner marks the change.** "I made this change" plus a date. Vizor never makes the change.
3. **Check.** Once at least 7 days of records exist after that date, actual totals are compared
   with both saved predictions (80% ranges). Verdict: as predicted / above / below, plus whether
   the result can be told apart from making no change. Days the business was closed are left
   out, because the forecasts don't know about closures (they predict about 41 taco orders on
   Thanksgiving, when the demo restaurant is closed).
4. **Vizor AI.** The `get_decision_followups` tool answers "did the taco price change work?" from
   the same numbers (offline and Gemini; the grounding check applies).

## Demo replay (synthetic)

The demo restaurant's records end on 2026-11-13, so no real records exist after an approval.
"Run a demo replay" generates four weeks with the change applied (`decision/replay.py`). It is
labelled **Demo replay · synthetic** everywhere. It continues the demo data **generator's** rules
(guest level, menu mix, ordering, rationing) and never calls Vizor's forecast or simulation (a
test enforces this), so the check isn't judging data the prediction produced itself. The replay
uses a hidden true price sensitivity per product (tacos −1.5; Vizor assumes −1.1, range −1.8 to
−0.6), which the dashboard reveals only in the "Demo check" after the result.

The generator refactor that made this possible (`synthetic_v2.restaurant_cover_level`,
`products.estimate_demand`) was checked to leave the demo data byte-for-byte unchanged (SHA-256 of
the daily, bills, product sales and ingredient ledger, before and after).

## What we measured (synthetic replays only)

Simulation-based check: for each product and price change, 40 replays with a hidden true price
sensitivity drawn from the assumed range; full model set.

**The follow-up check works for 3 of 4 main dishes.** Actual orders fell inside the predicted 80%
range in 35–39 of 40 replays for tacos, quesadillas and nachos (expected about 32). For
**burritos only 16–25 of 40** fell inside: the burrito forecast runs about 8% low in this window
(the generator's winter burrito bump, which the forecast lags behind). The follow-up surfaces
this as "above the predicted range". It cannot tell a forecast miss from an unexpected customer
reaction, and the summary says so.

**Learning price sensitivity from one test does not work, so it is reported and never applied.**

| Design | What happened |
|---|---|
| Before and after (what owners actually do) | Ranges narrowed by a median 0–3% for tacos, quesadillas and nachos at 5–10% price changes, and the error didn't improve. For burritos, the forecast miss was read as a price effect: error rose from 0.17 to 0.19–0.21 and coverage fell. Gating on "the range narrowed at least 25%" didn't help: the gated burrito results contained the truth in only 4/8, 8/17 and 16/28 cases. |
| Switchback (new price on alternate weeks), prototype only | Correctly calibrated (33–39 of 40 inside the 90% range, burritos included), but it narrowed the range only 0–5%. Day-to-day variation in one restaurant's item orders (8–13% over two weeks) is larger than the effect of a 5–10% price change. |

**Conclusion.** One small restaurant's own sales can't pin down an item's price sensitivity from
small price changes in any reasonable number of tests. Each follow-up therefore shows "what this
test tells us about price sensitivity" with its strength and the reason (normal variation vs
expected effect), and the Decision Lab keeps using the assumed range. Evidence pooled across many
businesses is the realistic route to better ranges; it is not built.

## Limits

- Product-level follow-ups need item-level records, which only the demo restaurant has. Uploaded
  or connected businesses can approve business-wide actions but not follow them up yet.
- A result inside the predicted range is consistent with the prediction, not proof the change
  caused it. Anything else unusual during the period (weather, events, competitors) is not
  modelled.
- All figures above are from synthetic data and say nothing about a real business.
- Files live in `data/cache/decisions/` (gitignored): `receipts.json`, `paths/<id>.npz`,
  `actuals/<id>.json`.
