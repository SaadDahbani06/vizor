# Vizor

A financial analyst for small businesses that tests a decision before you make it. It
forecasts what's coming, flags what changed, simulates your options with honest ranges, and
answers your questions by chat or iMessage. Every number is checked, and nothing happens without
your approval.

## Inspiration

Small business owners make money decisions every week:
- raise the taco price or not;
- order more tortillas before the weekend;
- absorb a supplier's price increase or pass it on.

Most of these decisions are made on gut feel. The data exists in the POS, the invoices and the
bank account, but the tools around it mostly show the past. A dashboard tells you that sales were
down yesterday. It doesn't tell you what would happen to profit if you changed a price, or how
sure anyone can be about that.

Point-of-sale companies are starting to add AI assistants that answer questions about sales. We
wanted something different: a tool that lets an owner **try a decision before making it**. It
should show what is likely to happen, the realistic range of outcomes including the downside, and
what to check first. It should do that without asking the owner to understand forecasting or
statistics, and without pretending to be more certain than it is.

## What it does

Vizor watches a business's daily numbers, explains what changed, and helps the owner evaluate
decisions. The demo business is **Lone Star Taqueria**, a fictional Austin restaurant with three
years of synthetic records, labelled "Demo Business · Sample Data" in the app.

Four pages:

📊 **Overview**: how am I doing, what needs attention, what can I do?
- A **Business attention** bar with the most important issue and a button for it: Investigate,
  Simulate decision or Review. For example: "Sales were 31% below a usual Friday; nachos and
  quesadillas dropped more than customer traffic."
- Six cards: revenue, operating profit, cash, expenses, inventory health and upcoming bills.
- One recommended decision to review, with expected profit, downside and upside.

🏪 **My Business**: sales and profit trends, the five products (price, what you keep per order,
expected orders next week, stock), ingredient stock and sell-out risk, where the money goes, cash
and bills, and a 4-week outlook.

🧪 **Decision Lab**: change a price, an ingredient order, supplier prices, customer traffic or
marketing spend, or type a question such as "What if I increase taco prices by 5%?".
- Vizor shows today's plan next to the change: revenue, profit, cash, orders, units sold,
  ingredients left and the chance of selling out.
- Tacos +5% gives about **+$286** profit over 4 weeks. The range runs from **−$5 to +$557**, and
  the change came out ahead in **95%** of the simulated futures.
- A **Review action** panel says what to do, why it may help, the expected impact, the main
  risks, and what to check. For tacos, the key risk is that if customers react more strongly to
  price than assumed, the change turns into a loss of $72.
- "Approve for my records" logs the decision. Vizor never changes a price or places an order.

🗺️ **Local Market**: your prices next to comparable Austin restaurants, read by hand from their
official menus. Servings are matched (three single tacos count as one plate of three), and every
price has a date and a source link. It shows price position only. Competitors' prices are never
treated as a reason to change your price.

✦ **Vizor AI**: a chat panel on every page.
- Simple questions ("How much cash do we have?") are answered instantly from your records.
- Scenario questions run the same simulations as the Decision Lab, and Gemini explains the result
  in plain language.
- Follow-ups work: "What about 3% instead?", then "Which is safer?".
- Every number in an answer is checked against the calculations. If Gemini writes a number it
  can't back up, the verified version is shown instead.

📱 **iMessage through Photon**: owners can text Vizor ("How did we do yesterday?", "What if taco
prices rise 5%?").
- Owners who opt in get a short "Decision needed" text when something important comes up, such
  as an ingredient that may run out before the next delivery. They can reply DETAILS, OPTIONS or
  STOP.
- Nothing is sent without opt-in, and STOP always wins.

**The decision loop:**

```
Vizor notices:  "Nachos sold 21 vs a usual 46 on Friday, a bigger drop than customer traffic"
  → Owner clicks Investigate → recent Fridays vs typical (what changed, not why)
  → Owner clicks Simulate → Decision Lab, preset to that item
  → Tests tacos +5% → +$286 expected, −$5 to +$557 range, risks listed
  → Reviews: what to check first (try the price for a few weeks, watch orders)
  → Approves for their records → makes the change themselves
```

The owner never sees a model name or a statistics term. The full details are one hover away.

## How we built it

Our main design decision was that **the language model explains numbers; it never produces
them.** Every figure comes from tested Python code: forecasts, simulations and accounting. The AI
layer only routes questions and writes explanations, and a checker compares every number it
writes against the tool outputs.

```
┌──────────────────────────────────────────────────────────────────────┐
│                            DATA LAYER                                │
├──────────────────┬──────────────────┬───────────────┬────────────────┤
│ Daily business   │ Product layer    │ Austin menu   │ Nessie bank    │
│ records (CSV     │ 5 items, recipes,│ prices (public│ sandbox        │
│ upload or demo)  │ ingredient ledger│ hand-checked) │ (simulated)    │
└────────┬─────────┴────────┬─────────┴───────┬───────┴───────┬────────┘
         ▼                  ▼                 │               │
┌──────────────────────────────────────────┐  │               │
│  VALIDATION + RECONCILIATION             │  │               │
│  continuity, no train/test leakage,      │  │               │
│  products + other items = total revenue  │  │               │
│  (to the cent)                           │  │               │
└────────────────────┬─────────────────────┘  │               │
                     ▼                        │               │
┌──────────────────────────────────────────┐  │               │
│  FORECASTING LEADERBOARD                 │  │               │
│  seasonal naive · weekday mean · 28-day  │  │               │
│  mean · Ridge · XGBoost · CatBoost       │  │               │
│  rolling-origin validation (25 folds) +  │  │               │
│  untouched final test (6 folds)          │  │               │
│  one demand model per product            │  │               │
└────────────────────┬─────────────────────┘  │               │
                     ▼                        │               │
┌──────────────────────────────────────────┐  │               │
│  MONTE CARLO SIMULATION                  │  │               │
│  replays whole past forecast-error       │  │               │
│  windows; same simulated futures for     │  │               │
│  every option (common random numbers);   │  │               │
│  assumption ranges (price sensitivity,   │  │               │
│  dish switching, marketing response)     │  │               │
└──────┬───────────────┬───────────────────┘  │               │
       ▼               ▼                      ▼               ▼
┌─────────────┐ ┌──────────────┐  ┌───────────────┐ ┌──────────────────┐
│ DECISION LAB│ │ DAILY        │  │ LOCAL MARKET  │ │ ADVANCED         │
│ price,      │ │ BRIEFING     │  │ COMPARISON    │ │ DIAGNOSTICS      │
│ orders,     │ │ same-weekday │  │ serving       │ │ validation,      │
│ supplier    │ │ comparisons, │  │ matching,     │ │ calibration,     │
│ cost,       │ │ alerts with  │  │ peer range,   │ │ reconciliation,  │
│ demand,     │ │ levels:      │  │ freshness     │ │ banking import   │
│ marketing,  │ │ routine /    │  │               │ │                  │
│ owner price │ │ important /  │  │               │ │                  │
│ limits      │ │ decision     │  │               │ │                  │
│             │ │ needed       │  │               │ │                  │
└──────┬──────┘ └──────┬───────┘  └───────┬───────┘ └────────┬─────────┘
       └───────────────┴──────────────────┴──────────────────┘
                                  │  tools (same numbers everywhere)
                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     VIZOR AI ROUTER                                  │
│  1. fast facts      → records, no model call         (~0.01 s)       │
│  2. scenarios       → simulation engines + one short                 │
│                       Gemini explanation             (~2 s)          │
│  3. open questions  → Gemini tool loop (≤4 rounds, 40 s deadline)    │
│  number check on every reply · circuit breaker · offline fallback    │
└──────────────┬───────────────────────────────────────┬───────────────┘
               ▼                                       ▼
┌───────────────────────────────┐    ┌─────────────────────────────────┐
│  DASHBOARD (4 pages)          │    │  PHOTON iMESSAGE WORKER         │
│  Overview · My Business ·     │    │  single-poller lock · opt-in ·  │
│  Decision Lab · Local Market  │    │  STOP/START · idempotency keys ·│
│  + Vizor AI side panel        │    │  "Decision needed" texts with   │
│                               │    │  DETAILS / OPTIONS replies ·    │
│                               │    │  status only as Photon confirms │
└───────────────┬───────────────┘    └────────────────┬────────────────┘
                └──────────────┬───────────────────────┘
                               ▼
                 ┌───────────────────────────┐
                 │  OWNER APPROVAL           │
                 │  decision log; nothing is │
                 │  ever executed by Vizor   │
                 └───────────────────────────┘
```

**Stack:**

| Layer | Tech |
|---|---|
| Forecasting | pandas, NumPy, scikit-learn (Ridge), XGBoost, CatBoost; rolling-origin leaderboard |
| Simulation | NumPy Monte Carlo (whole-window error replay, common random numbers) |
| Interface | Streamlit + Altair, custom design system |
| AI analyst | Google Gemini (`google-genai`, manual function calling); Gemini 3.5 Flash-Lite by default |
| Messaging | Photon iMessage API (`photonhq-api`), polling worker, webhooks supported |
| Banking demo | Capital One Nessie sandbox (read-only import) |
| Market data | Hand-verified official restaurant menus (Austin, TX), dated and sourced |
| Testing | pytest (274 offline tests), ruff, headless Chrome for visual checks |

## Challenges we ran into

- **A 2½-minute AI answer.** Our first real iMessage reply took 2 min 31 s. When we profiled it,
  most of the time went to quota errors, hidden SDK retries (one request waited 61 s before
  failing) and several model calls per question. Our own calculations took under 0.4 s.
  - We rebuilt the pipeline so simple questions skip the model and scenarios need one short model
    call.
  - Measured on real requests: median **9.3 s → 1.5 s**, worst **74.9 s → 7.4 s**.
- **A model that stated a wrong number with confidence.** In an early live test, the assistant
  quoted a price-sensitivity value that wasn't in our data. Because the number appeared somewhere
  in the tool outputs, a value-only check let it through. We changed the checker to also match
  *what each number is said to be*.
- **Uncertainty ranges that were too narrow.** Our first simulation sampled forecast errors in
  weekly blocks. When we tested the 28-day totals on past windows the model hadn't seen, only
  about 68% fell inside what was meant to be an 80% range. Replaying whole historical error
  windows fixed it: 22 of 23 past 28-day revenue totals now fall inside.
- **A bug that sold 149 million burritos.** Unclipped demand shares in the dish-switching logic
  blew up one scenario. We fixed it and added a regression test.
- **Messaging edge cases.**
  - A shared iMessage line can only reply in a conversation the owner started.
  - "Accepted" is not the same as "delivered".
  - Two pollers could answer the same text twice; we added a single-poller lock and idempotency
    keys.
  - A text went unanswered once simply because no poller was running.
- **Honest data.** National economic indicators (FRED) didn't improve our forecasts in validation,
  so we removed them from the product. One restaurant ordering site blocks automated access, so we
  didn't scrape it; we only used prices we could verify by hand.

## Accomplishments that we're proud of

- **Measured accuracy.** Every forecast is chosen by testing it on past periods it never saw, and
  the uncertainty ranges are checked the same way. We report those results rather than claim
  accuracy.
- **Honest uncertainty.** The Decision Lab shows the downside next to the expected gain, and says
  when a result depends on an assumption (for tacos: a loss of $72 if customers are more
  price-sensitive than assumed).
- **Checked numbers.** Vizor AI never invents a number: anything it says is checked against the
  calculations, and failures fall back to the verified answer.
- **Live iMessage round trip.** A real iMessage was answered with verified numbers and confirmed
  delivered by Photon 1.4 s after sending.
- **Simple on the surface.** An owner can use Vizor without knowing what Monte Carlo or Ridge
  regression is. The details are on hover or in Advanced diagnostics.

## What we learned

- Most of an AI assistant's latency is plumbing (retries, quotas, extra rounds), not thinking.
  Routing simple questions away from the model mattered more than picking a faster model.
- "The model explains, the code calculates" is a good rule for anything involving money.
- An 80% range has to be tested on past data the model never saw.
- Owners don't want more numbers. They want to know what changed, why it matters and what to try,
  in a few words, with the detail available when they ask.

## What's next for Vizor

- **Connecting a real business:** import item-level sales, recipes, inventory counts and invoices
  (the onboarding plan is written), and run the same validation on real restaurants' data.
- **Always-on hosting** for the iMessage worker and scheduled morning briefings. Today they run on
  a laptop.
- More decisions to simulate: staffing and hours, and per-ingredient supplier price changes.
- Pilots with independent counter-service restaurants and cafés, to learn what owners would
  actually use and pay for.

**Honesty note:** the demo uses synthetic business data, so all accuracy and calibration results
come from synthetic history. Simulated effects (for example, how customers react to a price
change) are stated assumptions with ranges, not proven causal effects.

## Built with

python · streamlit · altair · pandas · numpy · scikit-learn · xgboost · catboost · google-gemini ·
photon · imessage · capital-one-nessie · pytest
