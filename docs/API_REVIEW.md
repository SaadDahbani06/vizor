# API research assignment for teammate — November 14, 2026

## Mission
We want external data to improve **financial and operational recommendations** for organizations that upload their own internal data. Your job is to verify which sources can be used, what data they expose, when observations become available, and whether they actually support a meaningful *action*. This is an API/data feasibility review, not a request to invent integrations.

## Suggested order to test

| Tier | Source | Financial or operational relevance | Credential/access starting point | Critical caveat |
|---|---|---|---|---|
| P0 | **FRED / ALFRED** | Inflation, interest rates, industry cost trends; revision-aware history | Key needed for API; https://fred.stlouisfed.org/docs/api/fred/ | Economic indicators ≠ actual company supplier bills; record release/vintages |
| P0 | **SEC EDGAR XBRL** | Public company statements, margins, sector benchmarks, competitor context | Public JSON API without auth: https://www.sec.gov/search-filings/edgar-application-programming-interfaces | Quarterly/annual *public-company* results, NOT daily internal transactions; use identifying User-Agent and respect rate policies |
| P1 | **USDA ERS Food Price Outlook** | Restaurant and food retail costs, sector food CPI/PPI | Official CSV/XLSX downloads: https://www.ers.usda.gov/data-products/food-price-outlook | Not necessarily a real-time API, selected tables are forecasts, careful with archived method changes |
| P1 | **EIA Open Data** | Fuel and electricity prices, operating and logistics costs | Key required: https://www.eia.gov/opendata/documentation.php | Validate frequency, geography, series/units and limits |
| P1 | **NWS weather.gov** | Local demand, logistics interruptions, scheduling | Public API docs: https://www.weather.gov/documentation/services-web-api | Weather impacts must be learned from internal data; check client headers, archive availability and geography |
| P1 | **BLS** | Wages, employment, CPI/PPI and labor-market costs | Public API v1; registered API v2: https://www.bls.gov/developers/ | Separate from FRED duplicates; data often monthly, not instantaneous |
| P2 | **Census Bureau APIs** | Regional market, demographics, retail sector and business benchmarks | https://www.census.gov/data/developers/data-sets.html | Many series annual; weak fit for daily short-term recommendations |
| P2 | **GDELT news/event feeds** | Potential events such as port strikes and supply chain disruption | https://www.gdeltproject.org/ and https://gdeltcloud.com/gdelt-api | News coverage ≠ an economic impact; verify events, coverage, rights and relevance; avoid sentiment-based certainty |
| Optional | **Capital One Nessie (sandbox)** | Simulated accounts/transactions for banking integration demos | https://nessieisreal.com/docs | **Simulation**, not real customer records; verify hackathon access and rate terms |
| Sponsor-specific | **CoStar Group / property data** | Commercial real estate market conditions for retail location decisions | Organizer/sponsor documentation or licensed endpoint, if supplied | Assume no public usable API until sponsor explicitly grants access |
| Optional | **LLM provider (Anthropic or other)** | Natural-language orchestration/explanations only | Provider credentials / SDK | Not the ML forecasting data or source of factual numeric claims |

**Status convention:** The official documentation above establishes these sources as candidates. No live credentialed integration has been authenticated or tested by this team in the starter. `src/decisiondesk/connectors/fred.py` and `edgar.py` are opt-in fetchers; these are not production-ready pipelines.

## Your deliverable for each candidate

1. Exact name of usable dataset/endpoint and a **working example request** (without disclosing a secret).
2. Is key required? How to request it? Is event/hackathon sponsor access required?
3. Which metric does it give? Geography, units, industry, observation frequency, timestamp timezone and lag.
4. How far back does history go? Can we backtest **as of** historical release dates (revision/vintage)?
5. Any fee, quota, fair-access, redistribution restrictions, prohibited scraping, or attribution requirement?
6. Reliability: does a sample request actually return data today? What HTTP errors? Pagination?
7. What exact organizational dataset field could it join on (date, location, industry, supplier category)?
8. What candidate decision could it influence and via which testable assumption? What could go wrong?
9. Priority: must-have / useful / skip for hackathon, and why.
10. Save one legal/sample response in `data/cache/` **without secrets**, and write a tiny mocked automated test before integrating.

## Suggested concrete tests

### FRED
Find an index applicable to either restaurant/retail input costs, retrieve historical observations, check frequency and publication lag. Verify what *ALFRED/vintage* facilities can recover for a historical forecast. Do not use today's revised observation as though a model knew it a year ago.

### SEC EDGAR
Fetch `companyfacts` for one selected public restaurant/retail CIK with an appropriate HTTP User-Agent. Identify one tag for revenue and one for operating expenses, the units and dates of filings, then derive a clearly labelled public-company benchmark; avoid treating reported annual totals and quarterly totals as the same series.

### Operational feed
Choose EIA or NWS and relate it to one plausible decision: delivery scheduling, energy cost, inventory procurement, or staffing. Explain the observed historical relationship needed before claiming a forecast adjustment.

## Decision rubric
Score each: **API accessibility (0–2), relevant signal (0–2), historical testability (0–2), integration speed (0–2), clarity of rights and provenance (0–2)**. Add up to 10. Start with the highest-value sources; don't connect everything.

## Product/business notes
- Internal uploads remain the truth about *that* organization. External macro data provides context and explanatory features, not ground truth for the organization's revenue or decisions.
- SEC gives public-company benchmarking; EDGAR cannot replace data imported by a nonprofit, university or small company.
- A recommendation needs an objective, decision levers the user controls, constraints, a no-action baseline, and transparent counterfactual assumptions.
- Exact event/news attribution should be conservative and sourced, not a claim that an event caused a particular revenue result.

## Start of official reference links
- FRED: https://fred.stlouisfed.org/docs/api/fred/
- FRED vintage dates: https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html
- SEC API: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- USDA ERS: https://www.ers.usda.gov/data-products/food-price-outlook
- EIA: https://www.eia.gov/opendata/documentation.php
- BLS: https://www.bls.gov/developers/api_FAQs.htm
- NWS: https://www.weather.gov/documentation/services-web-api
- Census: https://www.census.gov/data/developers/data-sets.html
- Nessie: https://nessieisreal.com/docs
