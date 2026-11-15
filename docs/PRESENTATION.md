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
MSRD_LLM_PROVIDER=offline streamlit run app/streamlit_app.py
```

1. Open `http://localhost:8501/` (Overview). Leave the period on **Month to date**.
2. Click each page once so nothing loads on stage, then return to Overview.
3. Clear the chat if anything is in it.

## Script

| Time | Click | Say |
|---|---|---|
| 0:00 | Overview, top | "This is Lone Star Taqueria, a fictional Austin restaurant with three years of synthetic records, labelled on screen. The owner opens it in the morning. Yesterday, Friday, sales were **$2,713**, **31% below a typical Friday**. The briefing says why, as far as the data can: guests were down 34%, and nachos and quesadillas fell even more than traffic. Each item says whether it's observed, forecast or simulated, and what to check next." |
| 0:20 | Point at the KPI cards; open one **How was this calculated?** | "Month to date: revenue **$36,704**, operating profit **$3,444**, cash **$168,169**, and **$4,978** of bills due this week. Every card explains its own calculation." |
| 0:35 | **One decision to consider** → **Open in the Decision Lab** | "It suggests one decision: tacos +5%. In the Decision Lab, taco demand drops from **1,514 to 1,431**, so revenue barely moves (+$32), but profit rises **+$286** over 28 days. The likely range is **−$5 to +$557**, and it beats the current plan in **95%** of simulated futures." |
| 0:55 | Scroll to **Before you decide** | "Before deciding: why it might help, what could go wrong, and what to verify. Honestly: if customers are at the most price-sensitive end of our assumed range, it turns to **−$72**. Nothing changes until the owner does it." |
| 1:10 | Click **Reset to current plan**; drag **Supplier ingredient prices** to **+8%** | "Now the supplier raises prices 8%. Demand doesn't move, but profit falls by **$1,820** this month, between −$1,999 and −$1,593." |
| 1:25 | Sidebar chat: suggestion **What if I raise tacos prices 5%?**, then type **What about 3% instead?**, then click **Which is safer?** | "The analyst in the sidebar uses the same calculations. It remembers the conversation: 3% gives **+$171**, and it compares the two. It never computes numbers itself, and every number it says is checked against the calculation." |
| 1:45 | Click the **bell** → **Notification settings** | "Alerts and the morning briefing can go to the owner's phone by iMessage, only after they opt in, and STOP always works." |
| 1:55 | — | "The data is synthetic and the price effects are assumptions. Connecting a real restaurant's item sales is next." |

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
