# Main Street Risk Desk: two-minute demo script (verified 2026-11-14, Milestone 5.4)

One presenter, the dashboard full-screen at 1440 px or wider. Every number below was produced by
the code on branch `milestone-5.4-simple-analyst` (all models, warm cache, offline analyst). The
demo never waits for a live Gemini reply: live replies have taken 2–3 minutes.

## Setup, 10 minutes before

```bash
# 1. Stop any old processes: Ctrl-C old messaging workers and old dashboards.
cd ~/Desktop/main-street-decision-intelligence && source .venv/bin/activate
git switch milestone-5.4-simple-analyst
python -m decisiondesk.precompute                      # seconds when warm
./start.sh offline        # opens http://localhost:8501 in the browser
```

1. The Overview opens by itself.
2. Click each page once so nothing loads on stage, then return to Overview.
3. Clear the chat if anything is in it.

## Script

| Time | Click | Say |
|---|---|---|
| 0:00 | Overview | "This is Lone Star Taqueria, a made-up Austin restaurant with three years of demo records; the banner says so. The owner opens it in the morning and sees three numbers: sales this month **$36,704**, 7% more than last month; profit **$3,444**, about 9¢ of every dollar; and cash that covers this week's **$4,978** of bills." |
| 0:15 | **What needs your attention** | "Yesterday was slower than a usual Friday: sales were **31%** below normal, and there were 34% fewer customers, which explains most of it. Nachos and quesadillas dropped even more. Each item says what to check next. It shows what moved, not why." |
| 0:30 | **One idea to consider**, then **Try it in the Decision Lab** | "One idea: raise taco prices 5%. About **+$286** profit over the next four weeks. It could be anywhere from **−$5 to +$557**, and it came out ahead in **19 of 20** test runs." |
| 0:50 | Scroll to **Before you decide** | "Before deciding: what could go wrong and what to check first. If customers react to price more strongly than we assumed, it turns into a loss (**−$72**). Nothing changes until the owner makes the change." |
| 1:05 | Click **Supplier prices change**; drag to **+8%** | "Now the supplier raises prices 8%. Orders don't change, but profit drops by about **$1,820** this month." |
| 1:20 | Click **Ask your analyst** (bottom right): **What if I raise tacos prices 5%?**, then type **What about 3% instead?**, then **Which is safer?** | "The analyst uses the same calculations and remembers the conversation: 3% gives about **+$171**, and it compares the two. Every number it says is checked." |
| 1:45 | Click the **bell** → **Get these on my phone** | "The briefing and alerts can go to the owner's phone by iMessage, only after they opt in, and STOP always works." |
| 1:55 | — | "The data is a demo and the price effects are assumptions. Connecting a real restaurant's item sales is next." |

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
