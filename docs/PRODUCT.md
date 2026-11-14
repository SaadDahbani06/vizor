# Product definition — decision intelligence, not another dashboard

## Pitch
Business leaders and agencies upload financial or operational data, choose what they want to improve, and receive evidence-backed forecasts and ranked next actions informed by relevant economic events.

## Scope
Both financial and operational decisions, but V1 isn't magic on arbitrary spreadsheets. V1 supports daily business metrics and two initial industries (restaurant and retail). Other organizations are extensibility goals, not currently implemented.

## User steps
1. Import data. Confirm field mapping, target, granularity, business type, currency, and timezone.
2. Choose objective (e.g., preserve cash, grow margin, reduce stockouts) and business constraints (max price change, purchasing budget, inventory lead time).
3. Data-quality diagnostics. If insufficient data, say why and use a simple baseline or a purely hypothetical demo.
4. Match pertinent sources: macro prices from FRED, operational events weather/EIA, benchmark SEC/industry data. Missing APIs must never prevent the demo.
5. Rolling-origin model evaluation and forecasting with credible uncertainty.
6. Evaluate permitted interventions compared with 'do nothing'; report effect assumptions and uncertainty, not invented causal guarantees.
7. Display ranked action cards: action, expected benefit, cash/operational tradeoffs, confidence/assumptions, cited sources, recommended next human check.
8. Alert only when new data changes a monitored risk threshold; do not execute business actions automatically.

## Two judge demo cases
**Restaurant**: revenue and food-cost history; a user-specified 12% supplier-price shock; compare price change or supplier discount in cash terms.
**Retail**: sales / demand history and inventory on hand; forecast 28-day demand; flag a reorder review given explicit replenishment lead time. Later tie macro/weather news only when series and impact are relevant.

## Decision quality bar
Never claim 'the best decision' in an absolute sense. State 'best among the modeled alternatives under the stated assumptions' and show alternative tradeoffs.
