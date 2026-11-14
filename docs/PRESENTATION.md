# Main Street Risk Desk: two-minute demo script (verified 2026-11-14, Milestone 5.2)

One presenter, the dashboard full-screen at 1440 px or wider. Every number below was produced by
the code on branch `milestone-5.2-demo-readiness` (all models, warm cache). The demo never waits
for a live Gemini reply: live replies took 2–3 minutes.

## Setup, 10 minutes before

```bash
# 1. Stop any old processes: Ctrl-C the old messaging worker and old dashboards.
cd ~/Desktop/main-street-decision-intelligence && source .venv/bin/activate
git switch milestone-5.2-demo-readiness
python -m decisiondesk.precompute                      # seconds when warm
MSRD_LLM_PROVIDER=offline streamlit run app/streamlit_app.py
```

Use `MSRD_LLM_PROVIDER=offline` on stage: the answers come instantly from the same tools. To show
Gemini, start without it and pre-ask the question 5 minutes earlier; the answer waits in the chat.

1. Open `http://localhost:8501/?page=Overview` and leave the period on **Month to date**.
2. Open each page once, so nothing loads on stage.
3. Return to Overview.

## Script

| Time | Click | Say |
|---|---|---|
| 0:00 | Overview, top cards | "Lone Star Taqueria is a fictional Austin restaurant with three years of synthetic records, labelled on screen. Month to date: revenue **$36,704**, up 7.1% on the same days in October. Operating profit **$3,444**, a 9.4% margin. Cash **$168,169** from the ledger. Expenses **$33,260**. Ingredients worth **$2,885** on hand. Supplier bills **$24,931** due in the next two weeks." |
| 0:15 | Scroll to the charts | "Which products earn the money, where it goes (labor is the biggest cost), and inventory health: physical stock is the bar, predicted demand the black tick." |
| 0:25 | Click **Revenue details →**, then **Products** | "Five products. Tacos: $12.75, $3.26 of ingredients, a 74.6% margin, the most gross profit. Each product's demand is forecast by a model chosen by testing it on past windows it had never seen. For tacos the error is **11.3 a day, versus 14.9** for 'same as last week'. Demand isn't sales: on sell-out days we estimate the hidden demand from the sell-out time." |
| 0:50 | Click **Scenario Lab**; drag price to **+8%** | "What if I raise taco prices? At 8% it's flagged: the owner allows at most 5%. We show it, but never recommend it." |
| 1:00 | Drag price to **+5%** | "At 5%, taco demand drops from **1,514 to 1,431**, so revenue barely moves, about +$32; it doesn't rise 5%. Some customers switch to burritos. Profit rises **+$286** over 28 days. The range is **−$5 to +$557**, and profit improves in **95%** of 1,000 simulated futures." |
| 1:20 | Point at **Sensitivity** in the recommendation | "And honestly: if customers are at the most price-sensitive end of our assumed range, it turns to **−$72**. The app says so instead of hiding it." |
| 1:30 | Drag **ingredient orders** to **−40%** | "Cut the taco ingredient orders and customers still want tacos, but the kitchen can't make them. Stock-out risk goes from **20% to 62%** and profit falls to **−$539** versus the plan. Cash and profit move differently because suppliers are paid a week later." |
| 1:40 | Click **Reset**, set **+5%**, click **Ask the AI analyst about this →**, then **Ask** | "The AI analyst explains the same numbers: +$286, −$5 to +$557. It calls our tools and never calculates anything itself. Every number it says is checked against the tool output, including *what* the number is supposed to be." |
| 1:55 | — | "Ranges are checked on past data the model never saw: 22 of 23 past 28-day revenue totals fell inside. The data is synthetic and the price effects are assumptions; testing on a real restaurant is next." |

## If asked

- **"Why does the Overview recommend cutting discretionary spending, but the Lab recommends
  tacos +5%?"** They answer different questions. The business-wide engine compares levers across
  the whole restaurant (+$958). The Lab compares actions for one product.
- **"Why do the four main dishes show the same stock-out risk?"** They share tortillas, meat and
  cheese, so when one runs short, all four do.
- **"Is the 95% a probability?"** It's a frequency across simulated futures under our assumptions,
  not a validated real-world probability.

## Fallbacks

- **The analyst is slow or failing:** use the offline mode (above). Its replies say "Offline
  analyst (rule-based…)"; say that out loud.
- **Something loads slowly the first time:** the caches were cold. Run
  `python -m decisiondesk.precompute`.
- **iMessage:** only show a reply already delivered on the phone. Never text live on stage.

## Not to say

- Don't call accuracy "real-world": all scores come from synthetic history.
- Don't call effects causal: price sensitivity, substitution and marketing response are
  assumptions.
- Don't call the ranges "calibrated probabilities". They were checked on synthetic past windows
  and are on the cautious side (about 90% coverage for 80% ranges).
- Don't say "in under a minute" for text replies.
