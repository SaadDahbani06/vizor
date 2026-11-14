# Architecture, interfaces and exact maturity

```
User CSV/XLSX (V1 CSV daily aggregate)
  -> schema mapping + validation + private dataset store (future)
  -> daily KPI time series, organization objective / allowed actions
              |                           |
              v                           v
    External connectors                 Model layer
    - FRED observations (client stub)    - 5-model leaderboard (works)
    - SEC companyfacts (raw client)      - rolling backtest (works)
    - USDA/BLS/EIA/NWS (planned)         - Ridge/XGB/CatBoost (works)
    - news event feeds (planned)         - empirical intervals (works)
              |                           |
              +----> feature join <-------+
                             |
                      Scenario engine
                 - pricing/vendor (toy, works)
                 - inventory review (heuristic)
                 - optimizer (planned)
                             |
                       Agent / UI (planned)
                 - grounded explanation
                 - stored monitored alerts
```

## Planned interface contracts
- `BusinessDataset`: organization_id, time granularity, date, numeric targets, schema version, provenance, chosen objective, allowed decision levers, sensitivity flags.
- `ExternalObservation`: provider, series_id, timestamp, value, units, frequency, published_at (if known), retrieved_at, revision vintage, source_url.
- `ForecastResult`: target, origin, horizon, point forecasts, intervals, model/version, rolling-origin metrics, input provenance.
- `ScenarioResult`: action, parameters, baseline, projected effect, sensitivity/uncertainty, assumptions, operational constraints.
- `Recommendation`: actions ranked against baseline, reasons, sources, limitations, human approval requirement.

## Separation of concerns
- A generic forecasting model **cannot** decide which levers a business is authorized to change.
- SEC disclosures are slow and public-company-specific, best for benchmarking/cross-company context, not automatic small-business next-day predictions.
- LLM must call analytics tools, not create financial projections by language-model inference.
- Do not publish financial uploads or alerts without explicit user authorization and privacy controls.
