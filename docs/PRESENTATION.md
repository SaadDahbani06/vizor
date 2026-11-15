# Main Street Risk Desk: two-minute demo script (verified 2026-11-14, Milestone 5.4)

One presenter, the dashboard full-screen at 1440 px or wider. Every number below was produced by
the code on branch `milestone-5.4-simple-analyst` (all models, warm cache, offline analyst). The
demo never waits for a live Gemini reply: live replies have taken 2–3 minutes.

## Setup, 10 minutes before

```bash
# 1. Stop any old processes: Ctrl-C old messaging workers and old dashboards.
cd ~/Desktop/main-street-decision-intelligence && source .venv/bin/activate
git switch milestone-5.5-premium-ui
python -m decisiondesk.precompute                      # seconds when warm
./start.sh offline        # opens http://localhost:8501 in the browser
```

1. The Overview opens by itself.
2. Click each page once so nothing loads on stage, then return to Overview.
3. Clear the chat if anything is in it.

## Script

| Time | Click | Say |
|---|---|---|
| 0:00 | Overview, top | "This is the owner's morning view. It's a demo business with sample data, labelled at the top. The briefing says it in one sentence: sales were lower yesterday, 31% below a usual Friday, mainly because fewer customers came in; two items sold less than usual; three bills, $4,978, are due this week." |
| 0:15 | KPI cards | "Sales this month **$36,704**, up 7%. Profit **$3,444**. Cash **$168,169**, which covers this week's bills. Costs **$33,260**, and the card names the cost rising most. Inventory is healthy." |
| 0:30 | **Opportunity** card | "One idea: raise taco prices 5%. Expected **+$286** over four weeks. *Very likely to help, though a loss is possible.* The bar shows the downside (**−$5**) and the upside (**+$557**). We never hide the downside." |
| 0:45 | **Explore in Decision Lab** | "Before and after, side by side. Revenue barely moves, profit up 5%, taco orders down 5.5%. Main risks: if customers react to price more strongly than we assumed, it turns into a loss (**−$72**). The next step is a short price test. It needs the owner's approval; nothing happens automatically." |
| 1:05 | Type **What if supplier costs rise 8%?**, then **Run** | "You can just type the question. Supplier prices up 8%: profit drops about **$1,820** this month, and it says plainly not to absorb it as is." |
| 1:20 | **Ask AI** (bottom right): **Which product is making the most money?**, then the link **See your products** | "The assistant answers from the same calculations: every number is checked against the data. Its answers link straight to the right view." |
| 1:40 | **Local Market** | "Our prices against nearby restaurants: tacos are 15% below the local middle, worth testing. Competitors' prices show position, not how our customers will react, and the app says so." |
| 1:55 | — | "Behind this: validated forecasts, simulations with checked ranges, and number verification. The owner just sees what's happening and what to do. The data is a demo; connecting a real restaurant is next." |

## If asked

- **"How is this different from Toast IQ or Square's Managerbot?"** Those assistants come free
  with their POS and already do briefings, questions and approve-before-action. What we focus on
  is the what-if: compare a change with the current plan, with a likely range and the assumptions
  that could flip it, and verify every number. (Research: `docs/MILESTONE_5_4_RESEARCH.md`.)
- **"Is the 95% a probability?"** It's a frequency across simulated futures under our
  assumptions, not a validated real-world probability.
- **"Why do the four main dishes show the same sell-out risk?"** They share tortillas, meat and
  cheese, so when one runs short, all four do.
- **"Where did the economy page go?"** We removed it: national indicators did not improve the
  forecasts in our validation, and the product focuses on the owner's own costs. Supplier price
  changes are tested directly in the Decision Lab.

## Fallbacks

- **The analyst is slow or failing:** use offline mode (above). Its replies start with "Offline
  analyst (rule-based…)"; say that out loud.
- **Something loads slowly the first time:** the caches were cold. Run
  `python -m decisiondesk.precompute`.
- **iMessage:** only show a message already delivered on the phone. Never text live on stage.

## Not to say

- Don't call accuracy "real-world": all scores come from synthetic history.
- Don't call effects causal: price sensitivity, switching and marketing response are assumptions.
- Don't call the ranges "calibrated probabilities". They were checked on synthetic past windows
  and are on the cautious side.
- Don't say the briefing explains *why* sales fell: it shows what moved compared with a typical
  day.
