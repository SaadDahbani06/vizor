# Paste this into Claude Code in VS Code (repository root)

We are building a **general-purpose, evidence-driven Financial + Operational Decision Intelligence platform** for HackPrinceton's Business & Enterprise track, named Main Street Risk Desk (the working name can change).

I want organizations to import historical data, connect relevant external sources (e.g., FRED, SEC EDGAR, USDA, labor, energy and news), and get **ranked recommendations about what to do next**, including the assumptions, predicted impact, risks, and underlying data. The LLM should orchestrate **real analytical tools**, not invent projections.

## Work with the existing repository
Read `CLAUDE.md`, `README.md`, `docs/PRODUCT.md`, `docs/ARCHITECTURE.md`, `docs/EVALUATION.md`, `docs/API_REVIEW.md`, and the current Python modules before editing. Do not replace working code unnecessarily.

## MVP scope
Support financial decisions (revenue, costs, cash) AND operational decisions (demand, inventory). Keep the platform modular, but demonstrate it with TWO organization types: a restaurant and a retailer. Start with clean daily CSV uploads, optional schema mapping, known objectives and constraints. Do not promise 'any data, any decision' without supporting it.

## Build order, do not skip

### Milestone 1: Reliable data and quant engine
- Improve ingestion with auditable date, currency, metric definitions, missing data handling, and privacy.
- Keep baseline model and add Ridge, XGBoost, and CatBoost only when training data and dependencies allow. Compare against seasonal-naive; try time-series models only when warranted.
- Train with lag and rolling features constructed without future leakage.
- Implement rolling-origin validation and untouched final test, preferably on *real organization-level data if available*. Track MAE, RMSE, WAPE, forecast interval coverage.
- Build a model leaderboard. If advanced ML loses to baseline, use baseline.
- Improve the Monte Carlo/uncertainty engine, test calibration; never call a heuristic bootstrap a calibrated probability.

### Milestone 2: Real-world data adapters and relevance
- Review your teammate's findings in `docs/API_REVIEW.md` before coding new APIs.
- FRED is first priority; SEC EDGAR is a **separate financial benchmarking connector**, not direct daily private-business training data.
- Record observation time, publication time/vintage if available, retrieval time, frequency, units, source URL, and restrictions.
- Avoid historical backtesting using today's revised macro observations; point-in-time data or carefully lagged publication assumptions are required.
- Add relevant USDA/EIA/NWS/BLS or news sources only when the use case requires them and terms allow.
- Missing or invalid API credentials must never crash the offline demo.

### Milestone 3: Evidence-grounded decision engine
- Compare no action vs. realistic allowed interventions such as limited pricing changes, supplier-cost agreements, reorder reviews or spending controls.
- Model quantitative impact and constraints; document price-elasticity and procurement assumptions.
- Give a ranked recommendation only among alternatives actually evaluated, with tradeoffs and confidence/limitations.
- Explain whether the recommendation is an assumed scenario or supported causal effect.

### Milestone 4: Agent, events and alerts
- Agent uses callable Python tools (`load_business_data`, `find_relevant_signals`, `backtest_models`, `forecast`, `compare_actions`, `explain_recommendation`, `save_alert_rule`).
- Event feeds are evidence of possible relevance, not proof of economic impact. Verify sources and explain the path to business exposure.
- Add persistent monitoring only after model + decisions pass tests.
- Human authorization required before actual financial, operational, or messaging actions.
- Add simple Streamlit UI last.

## Mandatory verification
- Never invent accuracy metrics, data sources, dollar savings, live event claims or API availability.
- Keep credentials in environment variables; never commit private CSVs or transmit raw sensitive data to LLM without consent.
- Write pytest tests and run them; show the command output.
- Produce a working command-line demo at every stage.
- If a feature is not done, mark it clearly in README with an unchecked item.

## What to do in this session
Begin by inspecting the starter, running `pytest -q` and `python -m decisiondesk.demo --industry restaurant`. Then propose a compact 3-step plan and **implement the next incomplete analytics milestone**, not merely describing it. Report exact commands, real outcomes, and blockers; ask only for essential credentials or missing data.
