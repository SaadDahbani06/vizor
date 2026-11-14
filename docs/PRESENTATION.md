# Main Street Risk Desk: two-minute demo script (Milestone 5)

One presenter, the dashboard on a laptop at 1440 px or wider. Times are cumulative. Lines in
*italics* are actions, not speech. The demo **never waits for a live Gemini reply**: live replies
have taken 2–3 minutes.

## Before you go on stage (10 minutes earlier)

```bash
source .venv/bin/activate
python -m decisiondesk.precompute              # warm caches (about 2 s warm; a few minutes cold)
streamlit run app/streamlit_app.py             # http://localhost:8501
```

1. **Open the pages once** in separate tabs, so nothing loads on stage:
   - `http://localhost:8501/?page=Overview`
   - `http://localhost:8501/?page=Revenue%20%26%20Operations&section=Products`
   - `http://localhost:8501/?page=Scenario%20Lab&product=Tacos`
2. **Pre-ask the analyst.** On **AI Analyst**, click "What happens if burrito prices increase by
   5%?" now. With Gemini the answer arrives in the background in a few minutes, and the page
   says so while it works. To guarantee an instant answer instead, start the app with
   `MSRD_LLM_PROVIDER=offline`. The page then says "Offline analyst (rule-based…)", and you say
   that too.
3. **Optional iMessage.** Have the phone open on an already delivered Main Street Risk Desk
   reply. Do not text live on stage.

## Script

**0:00–0:30 · What's happening now** *(Overview)*

"This is Lone Star Taqueria, a fictional Austin restaurant. Every number comes from three years
of synthetic records, labelled as such. Month to date: revenue, operating profit and margin
against the same days last month, cash from the ledger, expenses, ingredient stock at cost, and
supplier bills due in the next two weeks. Below: revenue and profit over time, which products
earn the money, where it goes (labor is the biggest cost), and inventory health. Physical stock
is the bar, predicted demand is the black tick."

*Click "Revenue details →".*

**0:30–1:00 · Why** *(Revenue & Operations → Products)*

"Five products. Tacos make the most gross profit; nachos have the lowest margin. Demand and sales
are not the same thing: when an ingredient runs out, sales are capped, so we estimate demand from
the point-of-sale sell-out time. Each product's demand forecast is a model chosen by testing it
on past data in date order against simple baselines; for tacos the error is 11 a day versus 15
for 'same day last week'. And because tacos, burritos and nachos share tortillas and meat, a
shortage in one hits all of them."

*Click the Scenario Lab tab.*

**1:00–1:45 · What if** *(Scenario Lab)*

"Let's raise taco prices." *Drag the price to +8%.* "It's flagged: the owner allows at most 5%,
so we show it but never recommend it." *Set it to +5%.* "Demand falls, so revenue does not rise
5%. Some customers switch to burritos, and profit rises by about $410 over 28 days. The range
comes from 1,000 simulated futures, and the price sensitivity is an assumption we state and
stress-test." *Drag ingredient orders to −40%.* "Now demand exceeds what the stock allows, stock-out risk
jumps, profit falls. Cash and profit move differently because ingredients are paid a week later.
The recommendation below comes from the same simulations, with the ways it could reverse."

**1:45–2:00 · Explain** *(AI Analyst, pre-asked answer)*

"The AI analyst explains the same numbers. It calls these tools and never computes anything
itself, and every number it states is checked against the tool output, including what the number
is supposed to mean. The same answers come to the owner by iMessage." *Optionally hold up the
phone with the delivered reply.* "It's synthetic data and assumed effects; validating it on a
real restaurant is the next step."

## If something fails on stage

- **Gemini is slow or failing.** Use the pre-asked answer, or say: "the language model is slow
  today, so the rule-based analyst answers from the same tools." Start the app with
  `MSRD_LLM_PROVIDER=offline` to guarantee it.
- **A page is slow the first time.** The caches were not warmed: run
  `python -m decisiondesk.precompute`.
- **iMessage.** Only show an already delivered message. Delivery counts only when Photon reports
  `delivered`.

## Not to say

- Do not call any accuracy "real-world": the forecasting scores are on synthetic history.
- Do not call the ranges "calibrated probabilities". Say: "we checked the 80% ranges on past
  forecast windows they had never seen: 22 of 23 past 28-day revenue totals fell inside, so they
  are on the cautious side." Stock-out and cash-shortfall percentages are simulation frequencies.
- Do not call the effects causal: price, substitution and marketing effects are assumptions.
- Do not say "in under a minute" for text replies.
