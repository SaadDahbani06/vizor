# Milestone 3 verification — decision engine (2026-11-14)

All outputs below come from commands run on 2026-11-14 (Python 3.13.3) on the bundled
**SYNTHETIC** restaurant and retail histories (730 days each, 2024-01-01..2025-12-30). FRED
values are **REAL** point-in-time observations read from the local cache. Nessie data was **not
used**, and no Nessie records were written.

**Nothing here is evidence of real-world business impact.** Action effects are modeled
assumptions applied to forecasts, not estimated causal effects. The horizon is the validated
28 days, not 90.

## Summary
| Requirement | Status | Evidence |
|---|---|---|
| Phase 1: weekday-average scenario code replaced by selected-model interface | PASS | `demo.py` section 4 now calls the decision engine; `decisions.py` is marked legacy (kept for its starter tests) |
| Phase 2: modular, typed architecture (8 concerns) | PASS | `src/decisiondesk/decision/` (see `docs/ARCHITECTURE.md`) |
| Phase 3: restaurant price / supplier / discretionary scenarios | PASS | `decision/restaurant.py`; closed-form tests |
| Phase 4: retail reorder / maintain / reduce inventory policies | PASS | `decision/retail.py`; hand-calculated tests; `Unsupported` when fields missing |
| Phase 5: deterministic ranking for declared objectives | PASS | `decision/ranking.py`; 4 objectives + declared weighting |
| Phase 6: FRED as context / stress only, not forced into the model | PASS | `decision/context.py`; bundle notes "internal features only" |
| Phase 7: Nessie boundaries | PASS | no writes, not fed to models, balances unused, cash requires user confirmation |
| Phase 8: automated tests | PASS | 20 new decision tests + 3 constant-target tests; **73 offline passed, 7 live passed** |
| Phase 9: CLI demonstrations A and B with 9 sections | PASS (see retail note) | `python -m decisiondesk.decide`; outputs below |
| Phase 10: pytest, Ruff, demos, docs | PASS | below |

## Commands run and results
```
pytest -q                          -> 73 passed, 7 skipped (skips = opt-in live tests)
MSRD_LIVE=1 pytest -q -m live      -> 7 passed
ruff check src tests scripts       -> All checks passed!
python scripts/verify_milestone2.py --offline -> PASS 10, PARTIAL 0, FAIL 0 (no M2 regression)
```
| Demo command | Exit | Time |
|---|---|---|
| `python -m decisiondesk.decide --scenario restaurant` | 0 | 17.3 s |
| `... --scenario restaurant --objective min_cash_shortage_risk --starting-cash 200` | 0 | 15.3 s |
| `... --scenario restaurant --objective min_cash_shortage_risk --starting-cash 1500` | 0 | 15.1 s |
| `... --scenario retail` (default demo constraints) | 0 | 7.8 s |
| `... --scenario retail --budget 45000` | 0 | 8.7 s |
| `... --scenario retail --budget 45000 --max-stockout 1` | 0 | 7.8 s |
| `... --scenario retail --budget 45000 --objective min_stockout_risk` | 0 | 7.9 s |
| `python -m decisiondesk.demo --industry restaurant` | 0 | 32.4 s |
| `python -m decisiondesk.demo --industry retail` | 0 | 21.6 s |

---

## Phase 1: audit of the old weekday-average path
| Location | Before | After |
|---|---|---|
| `demo.py:77` | `compare_actions(seasonal_forecast(history))`, i.e. 12-week weekday mean, linear elasticity | Decision engine on `forecast_selected` baselines (reuses the demo's leaderboards) |
| `demo.py:79` | `operational_inventory_signal`, a 28-day mean heuristic | Retail inventory policies on the selected `units_sold` model |
| `decisions.py` | used by the demo | kept for backward-compatible tests only, docstring marked LEGACY |
| `risk.simulate_cash_risk`, `backtest.backtest` | weekday baseline | unchanged legacy, not used by any demo (the M1 backtested risk remains the headline) |

Model-selection logic and final-test separation were not changed. One Milestone 1 defect was
fixed (see "Defects found"); it did not alter any previously reported number.

## Phase 2: architecture
`types.py` (typed objects) → `baseline.py` (ForecastBundle from the selected models +
common random numbers) → `restaurant.py` / `retail.py` (explicit formulas) → `engine.py`
(paired evaluation, constraints) → `ranking.py` (ordering, defensibility, sensitivity, declared
weights) → `context.py` (FRED context, hypothetical stress) → `runner.py` (wiring, evidence) →
`decide.py` (CLI). Every `Recommendation` names its objective, metric, unit and horizon. No
LLM is involved anywhere.

## Phase 3: Scenario A, restaurant (actual output, objective = maximize expected 28-day profit)

**1. Selected models**

| Target | Model | Validation WAPE | Test WAPE |
|---|---|---|---|
| revenue | ridge | 0.086 | 0.088 |
| variable_costs | ridge | 0.093 | 0.093 |
| fixed_costs | seasonal_naive | 0.000 | 0.000 (an exact weekly schedule in the synthetic data) |

Each model contributes 18 out-of-sample error paths.

**2. Baseline, 2025-12-31..2026-01-27.** Point forecasts: revenue $57,153, variable costs $23,248,
fixed costs $28,040. Simulated baseline profit: mean $6,053 (90 %: $4,818..$7,376).

**4–6. Actions, outcomes and ranking** (paired Δ vs no action, 90 % range):

| Rank | Action | Expected profit | Δ profit | P(Δ>0) | Feasible |
|---|---|---|---|---|---|
| 1 | Raise menu prices 3 % ($150 menu cost) | $6,795 | +$741 (+$461..+$1,032) | 100 % | yes |
| 2 | Cut discretionary spending 30 % | $6,637 | +$583 (+$354..+$815) | 100 % | yes |
| 3 | Negotiate 4 % lower supplier prices ($250, from day 7) | $6,158 | +$105 (−$250..+$377) | 59 % | yes |
| 4 | No action | $6,053 | 0 | — | yes |
| — | Raise menu prices 8 % | $8,203 | +$2,150 | 100 % | **no**: 0.08 > max price increase 0.05 |

**7. Uncertainty and assumptions.**
- Assumptions:
  - price elasticity −0.8 (triangular range −1.2..−0.4, *default, needs confirmation*);
  - supplier share 0.85;
  - supplier success probability 0.6 (uniform 0.4–0.8);
  - discretionary share of fixed costs 0.10;
  - demand loss from the discretionary cut 0.5 % (0–1.5 %).
- **Sensitivity:** the top action switches to the discretionary cut when elasticity is −1.2,
  or when the cut's demand loss is 0.
- **Hypothetical stress** (variable costs +1.92 %, sized to the observed food-at-home CPI YoY for
  2025-11, published 2025-12-18): the top action is unchanged; baseline profit falls to $5,600.

**8. Recommendation.**
- **Raise menu prices 3 %, strength *assumption-sensitive*.**
- Versus the runner-up (discretionary cut): +$158 (−$218..+$544), P(better) = 76 %. The rating
  is therefore at most "weak", and the sensitivity flip makes it "assumption-sensitive".
- Under "minimize operating cost", the engine picks the discretionary cut (robust).
- Human approval is required, and nothing was executed.

**9. Provenance.** Data label, model and validation/test periods, assumption sources, FRED
series with observed / published / as-of / retrieved dates, the simulation seed and method,
and "no historical relationship claimed".

**Cash-shortage objective (requires user-confirmed starting cash):**

| Starting cash (user-declared) | no action | +3 % price | supplier | discretionary | Recommendation |
|---|---|---|---|---|---|
| $200 | 5.2 % | 15.9 % | 62.2 % | 0.0 % | **cut discretionary** (robust; −5.2 pp, MC s.e. 0.4 pp) |
| $1,500 | 0 % | 0 % | 0 % | 0 % | none, *no difference*: the objective does not separate the actions |

Up-front implementation costs are why the price and supplier actions raise short-term
shortage risk. Without `--starting-cash`, the objective is reported as *unavailable*.

## Phase 4: Scenario B, retail (actual output)
- **1. Selected model:** units_sold → ridge (validation WAPE 0.130, test WAPE 0.132).
- **2. Baseline demand:** 1,292 units over 28 days.
- **Derived from the data:** current stock 300 units, unit price $56.00 and unit cost $29.75
  (both from the last 84 days).
- **Policy sizing:** the order-up-to level is 701 units, the 95 % quantile of 14-day demand,
  sized on an independent draw set.

**Default demo constraints** (purchase budget $40,000, storage 1,200 units, stockout-after-arrival
≤ 25 %, all *default_needs_confirmation*):
- Every action is infeasible. "Maintain", "reduce" and "no order" break the stockout limit
  (97–100 %). The weekly reorder needs $40,528, over budget.
- Engine output: *"No candidate action satisfies the declared constraints. Smallest relaxation:
  'Reorder weekly up to 701 units' becomes feasible if 28-day purchase budget to 40,528 USD
  (declared 40,000)."*
- No recommendation is forced.

**With the user-declared budget of $45,000 and no stockout limit (`--max-stockout 1`):**

| Rank | Policy | Expected profit | Δ vs no order | Stockout after arrival | Lost units | Purchase outlay |
|---|---|---|---|---|---|---|
| 1 | Reorder weekly up to 701 units | $33,192 | +$25,336 (+$23,750..+$26,789) | 11.4 % | 24 | $40,528 |
| 2 | Maintain stock (replace sales) | $31,776 | +$23,919 | 97.3 % | 79 | $36,982 |
| 3 | Reduce purchasing (replace 50 %) | $17,759 | +$9,903 | 100 % | 615 | $14,094 |
| 4 | No order | $7,856 | 0 | 100 % | 1,004 | $0 |

- **Recommendation:** reorder, *robust*. Versus the runner-up (maintain): +$1,416
  (+$186..+$2,569), P = 97 %.
- **Sensitivity:** across holding cost 0.15–0.35/yr, the top action never changes.
- **Hypothetical −10 % demand stress:** the reorder policy stays on top (weak).
- **Other objectives:** "minimize operating cost" picks *no order* (ordering always costs
  money). "Minimize stockouts" picks *reorder*. Cash-shortage risk is *unavailable* for inventory
  decisions.

With the $45,000 budget and the 25 % stockout limit kept, reorder is the *only feasible
option*, and the output says it is chosen for feasibility.

## Phase 5: ranking rules (tested)
- **Ordering key:** objective mean in its declared direction, then the declared tie-breakers,
  then `action_id`. The result is identical when the input order changes.
- **Reference:** no action. If no action is infeasible, the runner-up feasible action is used.
- **USD objectives:** the top action must beat both no action and the runner-up, with
  P ≥ 0.6 (P ≥ 0.8 for "robust"), and by at least 1 % of the reference outcome. Without the 1 %
  floor, paired draws would make a $1 gap "certain".
- **Probability objectives:** the improvement must exceed 2 Monte Carlo standard errors and
  1 percentage point.
- **Labels:** robust / weak / assumption-sensitive / not distinguishable / no difference /
  only feasible option / unavailable.
- **Mixing units:** only `rank_weighted` combines metrics with different units. It requires
  explicit weights, normalizes each metric by min-max across the feasible actions, and says so in
  its output.

## Phase 6: economic context
- **observed_economic_data:**
  - CUSR0000SAF11: 2025-11 = 314.8, +1.92 % YoY, published 2025-12-18;
  - CUSR0000SEFV: +3.66 %;
  - UNRATE: 4.6 %, +0.4 percentage points;
  - CPIAUCSL: +2.71 %;
  - RSXFS: 2025-10, +3.38 %.
  - All are as of 2025-12-30 (the forecast origin), so nothing after the origin is used.
- **historical_relationship:** none claimed, because the FRED features failed validation in
  Milestone 2. FRED is not in the forecasting models.
- **hypothetical_change:** the cost and demand stress tests are labelled hypothetical.
- **estimated_intervention:** every action effect is an assumption.
- SEC EDGAR remains an unmapped benchmarking connector and is not used.

## Phase 7: Nessie boundaries
- **No sandbox writes.**
- **Nessie history is not used** in any model or calculation, and ingestion would reject it
  anyway (28 < 56 days).
- **Cash risk needs a user-confirmed starting balance.** `StartingCash(confirmed_by_user=False)`,
  for example a Nessie balance, makes the cash objective *unavailable*
  (`test_cash_objective_requires_user_confirmed_starting_cash`).
- Because no Nessie data enters any calculation, the missing bills cannot be silently excluded.

## Phase 8: test coverage (`tests/test_decision.py`, 20 tests)
| Requirement | Tests |
|---|---|
| Correct intervention calculations / analytic expectations | `test_restaurant_simulation_matches_closed_form_expectations` (price and discretionary exact to 1e-12; supplier within 4 Monte Carlo s.e.), `test_price_increase_does_not_raise_revenue_proportionally` (revenue change = (1+p)^(1+e) − 1; negative when e = −1.5), `test_retail_inventory_dynamics_match_hand_calculation` (lost units, holding cost, $32,700 outlay), `test_cash_risk_counts_upfront_costs_and_reports_no_difference` |
| No-action baseline consistency | `test_no_action_paired_deltas_are_exactly_zero`, `test_zero_size_actions_reproduce_the_baseline_exactly` |
| Constraint enforcement | `test_constraints_exclude_actions_and_report_relaxation` |
| Sensitivity to assumptions | `test_sensitivity_moves_with_elasticity_and_flags_rank_changes` |
| Missing required data | `test_missing_inventory_fields_return_unsupported`, `test_cash_objective_requires_user_confirmed_starting_cash` |
| Deterministic ranking | `test_ranking_is_deterministic_and_input_order_invariant`, `test_exact_ties_are_broken_by_action_id` |
| Reproducible stochastic simulation, common random numbers | `test_simulations_are_reproducible_and_use_common_random_numbers` (paired Δ std < 0.5 × unpaired) |
| No future-data leakage | `test_decisions_do_not_use_data_after_the_origin` (post-origin data ×50 → identical decision samples) |
| Uses the selected forecasting model | `test_bundle_uses_the_selected_model_and_its_out_of_sample_errors` |
| Different objectives | `test_objectives_change_the_recommendation`, `test_weighted_ranking_requires_declared_weights_and_states_normalization` |
| Defensibility | `test_small_uncertain_advantage_is_not_recommended`, `test_strength_also_requires_beating_the_runner_up`, `test_unknown_action_kind_raises` |
| M1 fix | `test_constant_anchored_target_is_handled[ridge, xgboost, catboost]` |

## Defects found and fixed during Milestone 3
1. **Milestone 1 leaderboard crashed on a constant anchored target.** Synthetic `fixed_costs` is
   an exact weekly schedule, so `y − anchor ≡ 0`, and CatBoost refuses constant targets.
   `AnchoredRegressor` now fits the constant. This adds tests and changes no prior result.
2. **The first retail model used a single order** sized for 14 days but evaluated over 28 days,
   which made every policy stock out. It was replaced with weekly-review policies.
3. **The "maintain stock" policy description was wrong:** it orders a week of expected demand
   first. A test caught this; the documentation and label were corrected.
4. **UNRATE's 12-month change was shown as a % of a rate.** It is now shown in percentage points.
5. **Defensibility gaps:**
   - P(better) against no action alone was not enough, so the runner-up comparison was added;
   - paired draws made immaterial $1 gaps "certain", so the 1 % materiality floor was added;
   - when no action is infeasible, the reference is now the runner-up feasible action, or the
     result is labelled "only feasible option";
   - when no action changes the metric, the result is labelled "no difference".
6. **Constraint violations printed as `4.053e+04`.** Numbers are now human-readable.

## Incomplete or unsupported by the available data
- **No real organization data.** Every result is synthetic, so real business impact and
  recommendation accuracy are unknown.
- **Effects are assumed, not estimated:** price elasticity, supplier success, discretionary
  demand loss, holding cost and order cost. The data has no price or supplier variation to
  estimate them from.
- **No validated 90-day forecast.** Cash-risk calibration is not established (6 holdout windows).
- **Retail:** cash-shortage risk is not modeled (needs payables timing); lead time is fixed;
  lost sales are not backordered; the synthetic inventory level is illustrative.
- **Retail demo constraints:** under the default demo constraints, no retail action is feasible.
  Ranked retail recommendations appear only after the user declares a budget. This is shown, not
  hidden.
- **Restaurant `fixed_costs`:** the forecast is exact only because the synthetic schedule is
  deterministic.
- **Not built (Milestone 4):** a continuous-action optimizer, the LLM agent, alerts and a UI.

## Recommendation
**READY FOR MILESTONE 4: YES.** All required checks pass.

Milestone 4 must keep four things true:
- the agent phrases tool outputs only, and never invents effect sizes;
- every assumption marked `default_needs_confirmation` must be confirmed by the user before a
  recommendation is presented as actionable;
- constraints remain user-declared;
- human approval is still required for any real action.
