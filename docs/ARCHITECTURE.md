# Architecture, interfaces and exact maturity (after Milestone 3, 2026-11-14)

```
Daily CSV (canonical schema) ──> data.py: DatasetSpec, IngestionReport, missing-day policy
        │                                   (Nessie transactions -> transactions.py, cash basis;
        │                                    NOT fed to models: 28 days < 56-day minimum)
        v
Model layer (Milestone 1)                           External data (Milestone 2)
  features.py  origin-anchored, leakage-tested        connectors/fred.py  ALFRED vintages (live)
  models.py    seasonal-naive, weekday mean,          macro.py            point-in-time features
               Ridge, XGBoost, CatBoost               ablation.py         internal vs +FRED
  leaderboard.py rolling-origin validation +                              -> FRED rejected
               untouched test, validation-only        connectors/nessie.py sandbox (synthetic)
               selection, empirical intervals         connectors/edgar.py  raw, benchmarking only
        │
  connectors/kwick365.py  real aggregate restaurant index (target + context)
  connectors/iowa_liquor.py official export importer (store purchases)
  profile.py / sources.py  confirmed profile, role mapping, capability + source selection
        │
        v   forecast_selected() + out-of-sample residual paths
Decision engine (Milestone 3)  src/decisiondesk/decision/
  types.py      OrganizationContext, StartingCash, Objective/ObjectiveSpec, Action, Assumption,
                Constraint, MetricSummary, ScenarioOutcome, RankedAction, Recommendation,
                Unsupported
  baseline.py   ForecastBundle = selected model per target + residual paths;
                SimulationDraws = common random numbers (7-day blocks, shared fold indices)
  restaurant.py price (constant elasticity), supplier (Bernoulli success), discretionary cuts
  retail.py     weekly-review inventory policies: none / order-up-to / replace sales / reduced
  engine.py     simulate all actions on the same draws, paired deltas, constraint checks
  ranking.py    deterministic ordering, defensibility rules, sensitivity, declared weighting
  context.py    observed FRED context (as of origin), hypothetical stress tests
  runner.py     wiring + evidence/provenance
        │
        v
decide.py (CLI report, 9 sections)   demo.py (section 4 now uses the decision engine)
        │
        v
Agent / alerts / UI (Milestone 4, NOT built)
```

## Layer contracts (implemented)
| Concern | Object(s) | Where |
|---|---|---|
| Organization and dataset context | `OrganizationContext`, `StartingCash` (must be user-confirmed), `IngestionReport` | `decision/types.py`, `data.py` |
| Objective | `Objective` → `ObjectiveSpec` (metric, unit, direction, tie-breakers) | `decision/types.py` |
| Candidate actions | `Action` (kind, parameters, implementation cost, baseline flag) | `restaurant.py`, `retail.py` |
| Assumptions | `Assumption` (value, unit, source tag, range, distribution) | same |
| Forecast + simulation | `ForecastBundle`, `SimulationDraws` | `decision/baseline.py` |
| Constraints | `Constraint` (`param:` or `metric:<name>:<stat>`, max/min) | `decision/engine.py` |
| Ranking | `Recommendation`, `RankedAction` | `decision/ranking.py` |
| Evidence and explanation | evidence dict + deterministic rationale text | `decision/runner.py`, `decide.py` |

Assumption `source` is one of: `user_declared`, `derived_from_data`, `default_needs_confirmation`,
`observed_external`, `hypothetical`. Economic statements carry a `category`:
`observed_economic_data`, `historical_relationship` (none claimed), `hypothetical_change`,
`estimated_intervention` (always assumed, never estimated causally).

## Key rules enforced in code
- Baselines come only from `leaderboard.forecast_selected`. The decision layer never chooses
  models and never sees the final test differently from Milestone 1.
- Every action is simulated on the same random draws. Deltas are paired, and no-action deltas
  are exactly zero.
- Infeasible actions are listed with reasons and never ranked. If nothing is feasible, the
  smallest relaxation is reported and the user decides.
- A recommendation needs P(better) ≥ 0.6 against no action **and** against the runner-up
  (≥ 0.8 for "robust"), a gap of at least 1 % of the reference outcome, and stability under
  assumption bounds. Otherwise it is labelled weak, assumption-sensitive, not distinguishable
  or no difference.
- Cash-shortage risk needs a user-confirmed starting balance. Static Nessie balances are never
  used.
- Units are never mixed. `rank_weighted` requires declared weights and states its min-max
  normalization.
- No action is executed. `Recommendation.requires_human_approval` is always true.

## Separation of concerns
- A generic forecasting model **cannot** decide which levers a business is authorized to change;
  actions and constraints are declared per organization.
- SEC disclosures are slow and public-company-specific: benchmarking context only, never private
  transaction data.
- An LLM (Milestone 4) may call these tools and phrase their outputs; it must not create
  projections, rankings or effect sizes itself.
- Financial uploads stay local. Artifacts and caches are gitignored.

## Maturity
| Component | Status |
|---|---|
| Ingestion, leaderboard, backtested 28-day cash risk | works, tested (synthetic data only) |
| FRED point-in-time + ablation | works, live-tested; features rejected on synthetic data |
| Nessie sandbox import | works on 28 days of synthetic records; bills unreadable (server bug) |
| Decision engine: restaurant price/supplier/discretionary | works, tested; effects are assumptions |
| Decision engine: retail weekly-review inventory policies | works, tested; synthetic inventory illustrative |
| Calibrated cash-shortage probability | not established (needs ≥ 20 holdout windows) |
| Elasticity / supplier-success estimation from data | not possible with current data (no price variation) |
| Optimizer over continuous action sizes | not built (discrete candidate actions only) |
| Kwick365 connector + real-aggregate validation | works, live-tested; target only (no vintages) |
| Business profile / mapping / capability detection; source catalog | works, tested |
| Iowa purchase importer + procurement-reserve decisions | tested on schema fixture; real run blocked on download |
| LLM agent, alerts, UI | not built (Milestone 4) |
