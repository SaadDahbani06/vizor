# Main Street Risk Desk: two-minute demo script (verified 2026-11-14, Milestone 5.4)

One presenter, the dashboard full-screen at 1440 px or wider. Every number below was produced by
the code on branch `milestone-5.4-simple-analyst` (all models, warm cache, offline analyst). The
demo never waits for a live Gemini reply: live replies have taken 2–3 minutes.

## Setup, 10 minutes before

```bash
# 1. Stop any old processes: Ctrl-C old messaging workers and old dashboards.
cd ~/Desktop/main-street-decision-intelligence && source .venv/bin/activate
git switch milestone-6-final-product
python -m decisiondesk.precompute                      # seconds when warm
./start.sh offline        # opens http://localhost:8501 in the browser
```

1. The Overview opens by itself.
2. Click each page once so nothing loads on stage, then return to Overview.
3. Clear the chat if anything is in it.

## Script

| Time | Click | Say |
|---|---|---|
| 0:00 | Overview, top | "This is a small business's morning view: a demo restaurant with sample data, labelled at the top. In one sentence: sales were lower yesterday, 31% below a usual Friday, mainly because fewer customers came in." |
| 0:12 | **Business attention** bar | "It doesn't just show numbers: it noticed this, and two items, nachos and quesadillas, dropped more than traffic. Every alert has a next step." Click **Investigate →** on Nachos. |
| 0:25 | Nachos: **Why this was flagged** | "Here's the evidence: orders against a typical Friday. It shows what changed, not why; weather and events aren't in the data. From here I can simulate." Click **Simulate a price change**. |
| 0:38 | Decision Lab: set **+5%** on Tacos (or type it in the box) | "Every change is compared with today's plan: revenue, profit, cash, orders, sell-out risk. Expected profit **+$286** over four weeks, but the downside is shown too: **−$5**, and if customers react more strongly than we assumed, **−$72**." |
| 0:58 | **Review action** | "Five steps: what to do, why, impact, risks, what to check. Approval only records the decision; nothing is changed automatically." Tick, then **Approve for my records**. |
| 1:12 | **✦ Ask AI**: type *How much cash do we have?*, then *What happens if taco prices rise by 5%?*, then *What about 3% instead?* | "Simple questions answer instantly from the books. Scenarios run our simulation, and Gemini explains it in about two seconds. Every number is checked; earlier this took minutes." |
| 1:35 | Bell → **Get alerts on my phone** | "Important risks can reach the owner by iMessage: 'Decision needed… reply DETAILS, OPTIONS or STOP.' Only after opt-in, never more than twice a day." |
| 1:50 | — | "Behind it: validated forecasts, simulations with checked ranges, and number verification. The owner just sees what's happening, why it matters, and what to do." |

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

- **Gemini slow, rate-limited or down:** nothing to do. The app answers from the verified engines
  within a second. To remove all model calls, restart with `./start.sh offline`.

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
