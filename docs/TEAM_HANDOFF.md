# Team split and handoff

## Person A — Quant and evaluation
Own `src/decisiondesk/forecast.py`, `backtest.py`, `risk.py`, `docs/EVALUATION.md` and model extensions. Deliver **measured rolling-origin scores** and baseline comparison; document synthetic vs real.

## Person B — External data/API research
Own `docs/API_REVIEW.md`, `docs/api_candidates.csv`, `src/decisiondesk/connectors/`. Start FRED, SEC, then EIA or NWS. Validate availability, legal access, historical point-in-time availability, useful exact series, relevance to an organization and an action. Do not share API keys in chat/Git.

## Person C — Decisions and product
Own `src/decisiondesk/decisions.py`, product spec and future app/agents. Improve scenarios, feasibility constraints, UI only after real calculations; demonstrate one **financial** and one **operational** recommendation.

## Branch approach
`git checkout -b quant-models`, `git checkout -b external-connectors`, `git checkout -b decision-engine`; merge via pull requests, don't run concurrent Claude Code edits on the same source files.

## Decision-making interface contract to agree on first
- Organization uploads a time-indexed dataset and confirms an objective.
- A feature can only be used if observed (or genuinely known) by forecast creation time.
- Forecaster returns a metric, horizon and uncertainty.
- Scenario evaluator returns baseline, alternative impact, assumptions and feasibility.
- Agent reports evidence and explicitly acknowledges uncertainty.

## Judge statement
"We combine internal company metrics with relevant external economic signals to forecast outcomes and compare feasible financial and operational decisions. The demo shows what the models have actually calculated and tested, not generic AI advice."
