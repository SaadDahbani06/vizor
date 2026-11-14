# Main Street Decision Intelligence — project rules for Claude Code

Read `README.md`, `docs/PRODUCT.md`, `docs/ARCHITECTURE.md`, `docs/EVALUATION.md`, and `docs/API_REVIEW.md` before significant architectural changes.

## Mission
An extensible quantitative decision system for BOTH financial and operational actions. An organization uploads internal time-series data; relevant official external data may add context; statistical models forecast targets; decision tools compare interventions; an LLM eventually orchestrates and explains without fabricating calculations.

## Current code vs roadmap
Baseline offline demo and tests currently exist. This is a starter, not a trained/validated agent. Maintain the honest implementation status in `README.md`.

## Non-negotiable rules
- Always run `pytest -q` after modifying analytics. Report actual test results, failures, and limitations.
- Do not show real-business accuracy from synthetic-only tests. Do not invent accuracy, event attribution, causal improvement, or availability of an API.
- Never use current/revised economic values in historical forecasts if not available at the historical origin. Store `observed_at`, `published_at`/vintage, `retrieved_at`, units, source, and frequency when provided.
- The user chooses business type, objective/target, decisions they can actually make, and constraints. **Do not** assume universal predictive or causal capabilities from arbitrary uploads.
- Rank supported actions against a no-action baseline; report impact assumptions, ranges, uncertainties and constraints. Keep forecast/prediction separate from causal inference.
- Financially consequential actions, bank transfers, purchasing, staffing changes, contacting suppliers and alerts need explicit user approval. Never perform them automatically.
- Financial and operational business data is private. No hardcoded secrets, avoid sending raw data to the LLM, no commits of private CSVs or `.env`.
- Avoid duplicate API integration logic. Use `ExternalDataConnector` contract and separate source data from normalized features.
- Avoid UI polish until at least one end-to-end validated vertical slice works.
- Scope V1 to two daily-aggregate examples (restaurant and retail), 2 target classes (revenue and demand), and three interventions (price, supplier costs, reorder review).

## Commands
- Run demo: `python -m decisiondesk.demo --industry restaurant`
- Run tests: `pytest -q`
- Optional dependencies: `pip install -e '.[dev,ml,ui,agent]'` only when that stage is needed.
- Paths: `src/decisiondesk/` source, `tests/` tests, `docs/` review, `.claude/skills/` commands.

## Build order
1. Validate ingestion and ensure time-series continuity / train-test isolation.
2. Implement real-data FRED client tests with mocked HTTP and publication-aware joins. Design SEC benchmark ingestion separately.
3. Train/test seasonal naive, Ridge, XGBoost/CatBoost when enough training data; compare and save leaderboard with actual time-aware evaluation.
4. Improve uncertainty calibration and scenario engine, including operational objective and constraints.
5. Add LLM tool calling + event relevance checks and action explanation.
6. Add monitored alerts and minimal UI. Never claim a feature is complete until shown working.
