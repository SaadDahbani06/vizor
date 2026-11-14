# Milestone 5.3: local market, competitive research and differentiation (2026-11-14)

**Research method.** All research was done on 2026-11-14 with live web search and page fetches.
- **Fetched:** a source marked *fetched* was read directly on that date.
- **Search summary:** a source marked *search summary* comes from a search-engine summary of that
  page, because the page itself blocked automated access. Treat it as secondary.
- **Not verified:** nothing that could not be verified was filled in.

**Business data.** Lone Star Taqueria and all of its records remain synthetic.

## Contents

- A. Local competitor research (Austin)
- B. Data availability
- C. Software competition
- D. Competitive gaps
- E. Recommended features (scored)
- F. What was built
- G. Positioning for judges and investors

---

## A. Local competitor research (Austin)

**Coverage.** 13 restaurants were researched. **6 had prices that could be verified on their own
official websites** (verified 2026-11-14). The other 7 are logged as unavailable, not estimated.
The data is in `data/market/austin_menu_prices.csv` (one row per item) and
`data/market/austin_research_log.csv`.

### Verified prices (official menus, fetched 2026-11-14)

| Restaurant | Type | Address | Item | Listed price | Per our serving | Match | Source |
|---|---|---|---|---|---|---|---|
| El Chilito | counter-service taqueria, independent (2 locations) | 2219 Manor Rd 78722; 4501 Manchaca Rd 78745 | Al pastor taco | $3.80 (tacos $3.30–$4.40) | $11.40 (3 tacos) | constructed | [elchilito.com/menu](https://www.elchilito.com/menu) |
| El Chilito | | | Al pastor burrito | $7.50 | – | similar (size not stated) | same |
| El Chilito | | | Quesadilla + protein | $7.00 + $3.00 | $10.00 | constructed | same |
| El Chilito | | | Agua fresca 16 oz | $3.75 | – | similar | same |
| Taco Joint | counter-service taqueria, independent (2 locations) | 2809 San Jacinto Blvd 78705 | Lunch taco (all varieties) | $4.95 | $14.85 | constructed | [austintacojoint.com/menu](https://austintacojoint.com/menu) |
| Taco Joint | | | Mexican Coke / Jarritos | $3.50 / $2.50 | – | similar | same |
| Juan in a Million | Tex-Mex restaurant, independent | 2300 E Cesar Chavez St | Taco (beef or chicken) | $5.00 | $15.00 | constructed | [juaninamillion.com/menu](https://www.juaninamillion.com/menu) |
| Juan in a Million | | | Taco Dinner (2 tacos + beans, rice, salad) | $13.60 | – | **unmatched** (2 tacos + sides) | same |
| Juan in a Million | | | Quesadilla with beef or chicken | $12.50 | $12.50 | exact | same |
| Juan in a Million | | | Nachos with ground beef | $14.75 | $14.75 | exact | same |
| Juan in a Million | | | Bottled Mexican soda | $4.25 | – | similar | same |
| Tamale House East | Tex-Mex restaurant, independent | 1707 E 6th St 78702 | Roasted chicken taco | $6.00 | $18.00 | constructed | [tamalehouseeast.com/menu](https://www.tamalehouseeast.com/menu) |
| Tamale House East | | | Deluxe Taco Plate (2 tacos + beans, rice) | $16.00 | – | **unmatched** | same |
| Tamale House East | | | Chicken quesadilla | $12.00 | $12.00 | exact | same |
| Guero's Taco Bar | Tex-Mex restaurant, independent | not on the fetched page | Taquito al pastor | $3.50 | $10.50 | similar (taquito may be smaller) | [gueros.com/menu](https://www.gueros.com/menu) |
| Guero's Taco Bar | | | Nachos Compuesto | $18.25 | $18.25 | exact | same |
| Matt's El Rancho | full-service Tex-Mex, independent | 2613 S Lamar 78704 | Chicken fajita quesadilla / nachos | $18.00 / $18.00 | $18.00 | exact | [mattselrancho.com/menu](https://www.mattselrancho.com/menu) |
| Matt's El Rancho | | | Tacos al pastor (dinner) | $17.00 | – | **unmatched** (count not stated) | same |

**Our servings.** Each comparison is against one of our servings:
- tacos: a plate of 3, no sides, $12.75;
- burrito with rice and beans: $13.25;
- quesadilla with protein: $11.50;
- loaded nachos: $10.75;
- agua fresca or soda: $3.50.

**How comparisons are counted.**
- One representative item per restaurant.
- Only *exact* and *constructed* matches count by default. *Similar* can be switched on.
- *Unmatched* items are shown with their reason but never counted.

**Resulting peer positions:**

| Our product | Verified comparables | Peer range | Median | Our price | Position |
|---|---|---|---|---|---|
| Tacos (3) | 4 | $11.40–$18.00 | $14.93 | $12.75 | within the range, 14.6% below the median |
| Quesadilla | 4 | $10.00–$18.00 | $12.25 | $11.50 | within the range, 6.1% below |
| Nachos | 3 | $14.75–$18.25 | $18.00 | $10.75 | below the range. **All 3 are sit-down Tex-Mex**, so this is a tier difference, not proof of underpricing |
| Burrito | 0 (1 similar) | – | – | $13.25 | not enough comparables |
| Drinks | 0 (3 similar) | – | – | $3.50 | not enough exact comparables (drink types differ) |

### Researched, prices not available (not used)

| Restaurant | Why not used |
|---|---|
| Granny's Tacos (1401 E 7th St) | The official site's menu page lists items without prices. A delivery listing exists, but delivery prices are not in-store prices |
| Papalote Taco House | No official priced menu found. Review prices are undated |
| Cuantos Tacos (1108 E 12th St) | The official site was not readable by our fetcher. Review figures (~$2 tacos) are undated |
| Tacodeli (local chain) | Official menu and location pages have no prices. The printed PDF has no text layer |
| Torchy's Tacos (chain) | The official menu has no prices. It is a chain, so excluded from primary peers anyway |
| Vaquero Taquero | Prices are only on Toast ordering, which returned HTTP 403 to automated access. Not collected |
| Changos Taqueria | Third-party listing without a date. Not used |

**Public aggregate.** Toast's public *Menu Price Monitor* reports a national median burrito price
of **$13.70 (September 2026)**, across all burritos and not adjusted for portion. This is a search
summary of [pos.toasttab.com/blog/data/burrito-prices](https://pos.toasttab.com/blog/data/burrito-prices);
the page returned 403 to a direct fetch. It is shown in this report only, not in peer statistics.

**Limits of this dataset.**
- **Small samples:** 3–4 comparables per product.
- **Mixed tiers:** counter-service taquerias and sit-down Tex-Mex are mixed. The page shows this
  as a warning and offers a filter.
- **One date, no history:** a single verification date (no price history yet), and city-wide
  rather than by radius, because the fictional restaurant has no address.
- **Prices only:** no competitor sales, demand, costs or margins were collected or inferred.

---

## B. Data availability

| Data | Source | Classification | Access, limits, cost |
|---|---|---|---|
| Menu prices, independents | Official restaurant websites | **Public and verified** (when posted) | Manual check. Many independents post no prices, or post them only through ordering platforms |
| Menu prices via ordering platforms | Toast online ordering, DoorDash, Uber Eats, order.online | **Licensed or permission-dependent** | Toast pages returned 403 to automated access. Delivery prices differ from in-store prices. Not used |
| Locations and attributes | Google Places API | Licensed | Paid per request by field tier. Google's terms allow caching coordinates for up to 30 days, and secondary summaries say building a POI database is prohibited ([terms](https://cloud.google.com/maps-platform/terms/maps-service-terms/index-20231025), [billing](https://developers.google.com/places/web-service/usage)). Treat as licensed; read the current terms first |
| Reviews and ratings | Yelp Fusion API | Licensed | Free access ended in 2024. Reported pricing ranges from per-call tiers to about $229/month ([TechCrunch](https://techcrunch.com/2024/08/02/yelps-lack-of-transparency-around-api-charges-angers-developers)). About 24-hour caching. Reviews are not sales |
| Restaurant locations | City of Austin open data: Food Establishment Inspection Scores ([catalog](https://catalog.data.gov/dataset/food-establishment-inspection-scores), dataset `ecmv-9xxi`) | **Public and verified** | Free CSV/JSON/API. Names and addresses of inspected establishments; inspection scores, not sales. A good free source for finding nearby restaurants |
| Price benchmarks | Toast Menu Price Monitor (national medians: burgers, coffee, burritos …) | Public (aggregate) | Free articles. National, not portion-adjusted, Toast restaurants only |
| Peer sales benchmarks | Toast Benchmarking (inside Toast) | Licensed (Toast customers only) | Aggregated Toast data: net sales, average check, category mix, "effective pricing", filterable by ZIP code. Not available to non-customers |
| Competitors' sales, demand, margins | – | **Unavailable** | No public source. Must never be inferred from reviews or prices |
| Our business's data | Synthetic generator (demo); owner uploads | Synthetic, or private | – |

**Monitoring we can support responsibly:**
1. Re-verifying a small set of official menus by hand on a schedule (we set 45 days, with an
   in-app stale warning).
2. Optionally, the City of Austin dataset to discover new nearby restaurants.

**What we can't support yet:**
- **Real-time competitor prices:** there is no reliable, permitted real-time source for
  independent restaurants' menus.
- **Automated collection from ordering platforms:** they block it, and terms restrict it.

---

## C. Software competition

**Status codes:**
- **V:** verified available, from official documentation or a press release.
- **P:** partially supported.
- **?:** not verified. This does **not** mean unavailable.
- **X:** verified unavailable. None were found; we don't claim a gap from silence.

| Capability | Toast | Square | MarginEdge | MarketMan | Restaurant365 | 7shifts | Tenzo | Crunchtime | **Us** |
|---|---|---|---|---|---|---|---|---|---|
| Sales dashboards | V | V | V (sales reporting) | V (reports) | V | ? | V | V | Implemented |
| Product / menu analytics | V (Toast IQ menu analysis) | V (item performance, Square AI) | V (menu analysis) | V (menu profitability) | ? | ? | ? | ? | Implemented (demo restaurant) |
| Demand / sales forecasting | ? (weather and events in insights) | P ("projecting sales" mentioned) | V (SmartPrep, +$95) | ? (not on the pricing page) | V (in workforce) | ? | V (hourly; weather and events) | V (AI forecasting) | Implemented, validated by date-ordered back-tests (synthetic) |
| Inventory | ? | V (Plus plan) | V | V | V | – | ? | V | Implemented (ingredient ledger, demo only) |
| Recipe costing | ? | ? | V | V | V | – | ? | V | Partial (fixed recipe table) |
| Supplier price alerts | ? | ? | V (price tracking) | V | V | – | ? | ? | Not implemented |
| Cash-flow forecasting | ? | ? | ? | ? | ? (real-time financials) | – | ? | ? | Implemented (net cash; shortfall frequency, not validated) |
| Scenario / what-if simulation | ? | ? | ? | ? | ? | ? | P ("dynamic modeling") | ? | Implemented (product Scenario Lab, business engine) |
| Monte Carlo uncertainty with published calibration | ? | ? | ? | ? | ? | ? | ? | ? | Implemented (calibration checked on synthetic data) |
| Local benchmarking | **V** (Toast Benchmarking: sales, check size, category mix, effective pricing by ZIP) | P (local weather, events, news, reviews; "trending locally") | ? | ? | ? | ? | ? | ? | Partial: verified menu-price comparison, 6 restaurants, manual |
| Item-level competitor menu prices with sources | ? (effective pricing, not menus) | ? | ? | ? | ? | ? | ? | ? | Implemented (small manual dataset) |
| AI questions | V (Toast IQ, since Oct 2025) | V (Square AI, open beta) | V ("AI tools") | V (AI recipe and ordering) | P ("R365 AI") | ? | ? | V (AI forecasting) | Implemented (Gemini, grounded; offline fallback) |
| Proactive insights / briefing | V (Toast IQ "For you" feed) | P | ? | ? | ? | ? | ? | ? | Implemented (daily briefing + checklist) |
| Messaging alerts | ? | ? | ? | ? | P (compliance alerts) | ? | ? | ? | Implemented (iMessage via Photon; one live reply) |
| POS integrations | is the POS | is the POS | V (Toast users pay extra) | V | V | ? | V | V | Not implemented (CSV/Excel upload of daily totals only) |
| Setup | hardware + contract | free plan | onboarding fees | – | 6–8 weeks reported (secondary) | – | – | training needed (reviews) | Minutes for the demo; no real onboarding yet |
| Published price | $0 Starter / $69 POS plan (search summary) | Free / Plus / Premium; KDS $20–30 per device, Kiosk $99 (fetched) | **$350 per location/month** (fetched) | **$249 / $299 / from $449** (fetched) | quote only (fetched) | Free / $39.99–$134.99 (secondary) | contact vendor | on request | – |

**Sources** (fetched 2026-11-14 unless noted):
- **MarginEdge:** [pricing](https://www.marginedge.com/pricing).
- **MarketMan:** [pricing](https://www.marketman.com/pricing).
- **Restaurant365:** [pricing](https://www.restaurant365.com/pricing/).
- **Square:**
  - [restaurant pricing](https://squareup.com/us/en/point-of-sale/restaurants/pricing);
  - [Square AI release, Oct 8, 2025](https://squareup.com/us/en/press/square-releases-ai): "access the web for local information and data related to weather, events, news, and reviews". It does not describe comparisons of competitors' prices, or scenario simulation.
- **Toast:**
  - Benchmarking [overview](https://intercom.help/toast-central/en/articles/9541305-toast-benchmarking-overview) and [FAQ, May 6, 2026](https://intercom.help/toast-central/en/articles/9541308-toast-benchmarking-faq): Benchmarking Lite is free; Essentials is part of the Restaurant Management suite.
  - Toast IQ: [press release, Oct 29, 2025](https://pos.toasttab.com/news/toast-expands-toast-iq-smart-ai-assistant) (search summary; the page returned 403).
  - Pricing: [Toast pricing blog via search](https://www.owner.com/blog/toast-pricing) (secondary).
- **7shifts:** pricing from a secondary summary ([noon.ai](https://www.noon.ai/blog/articles/452-7shifts-pricing-2026)); the official page renders prices dynamically.
- **Tenzo:** [Capterra](https://www.capterra.com/p/177965/Tenzo/), [FitGap](https://us.fitgap.com/products/tenzo).
- **Crunchtime:** [Software Advice](https://www.softwareadvice.com/food-service-management/crunchtime-profile/), [crunchtime.com/ai-info](https://www.crunchtime.com/ai-info).

### What customers dislike (attributable, dated)

- **Toast.**
  - On **Oct 29, 2026 the FTC and eight states sued Toast**, alleging hidden and unauthorized fees, deceptive subscription and cancellation practices, and unreliable "24/7/365" support claims ([FTC press release](https://www.ftc.gov/news-events/news/press-releases/2026/10/ftc-states-take-action-against-toast-inc-over-hidden-unauthorized-fees-deceptive-subscription)).
  - These are **allegations**; Toast disputes them and the case is pending.
  - Secondary review aggregators repeat themes of contracts, fees and support waits; much of that comes from competitors' marketing.
- **Restaurant365.** A recurring pattern across Capterra, SelectHub and NerdWallet:
  - demanding setup and a steep learning curve;
  - cost "a bit much for a small restaurant" (Capterra reviewer);
  - an implementation fee (NerdWallet).

  Ratings are still solid: 4.1/5 on Capterra (71 reviews) and about 4.4 on G2 ([Capterra](https://www.capterra.com/p/139768/Restaurant365/reviews/), [NerdWallet](https://www.nerdwallet.com/article/small-business/restaurant365-review)).
- **MarginEdge.** Mostly positive (4.6/5 on G2 and Capterra). The cons that appear are terminology and the learning curve, plus lengthy onboarding ([G2](https://www.g2.com/products/marginedge/reviews), [CFO Club](https://thecfoclub.com/tools/marginedge-review/)). We found no evidence of the complaints we were told to look for (invoice delays, price accuracy).
- **Crunchtime.** Reviewers note that the forecasting tools need training; enterprise pricing.
- **Not researched in depth** (no reviews analysed): Square, MarketMan, 7shifts and Tenzo.

**The pattern:** cost and setup effort for small single-location restaurants, and multiple modules
priced separately (MarginEdge +$95 for forecasting, +$50/month at Toast for its API). This is a
pattern across several sources, not proof that any product is bad. Many reviews are positive.

### Compared with ChatGPT, Claude and Gemini

We found **no independent evidence** comparing restaurant analysis by general LLMs; the search
returned only vendor blogs. The following is **our analysis, not a measured result**.

**What a capable owner can already do with an LLM plus Python:** upload a spreadsheet and get a
one-off forecast, chart or what-if.

**What we may do better**, as hypotheses to test with real users:
1. The analysis is the same every time. Fixed models and seeds give the same answer to the same
   question, and an LLM session doesn't guarantee that.
2. Models were selected by date-ordered back-testing, and their ranges were checked on unseen data.
3. Inventory, recipes and policy limits are built into the simulation.
4. Every number the AI states is checked against computed results.
5. The business context persists, so there is no re-uploading.
6. A daily briefing and alerts arrive by iMessage without the owner asking.

**What an LLM does as well or better:** flexible ad-hoc questions, and explanations.

---

## D. Competitive gaps

**What competitors do better today:**
- **POS integration and live data:** Toast and Square *are* the POS.
- **Real peer sales benchmarks:** Toast Benchmarking uses actual transaction data. This is far
  stronger than public menu prices for local positioning, for Toast customers.
- **Invoices, supplier prices, recipe costing and accounting:** MarginEdge, MarketMan, R365.
- **Labor scheduling:** 7shifts, R365.
- **Mature AI assistants with actions:** Toast IQ can edit menus and shifts.

**What we lack:**
- a real POS or accounting integration;
- product-level onboarding for real restaurants;
- supplier invoice price tracking;
- multi-location support;
- validation on any real business.

**What we can realistically differentiate on**, as unverified gaps in competitors and not
claimed absences:
1. **Decision simulation with uncertainty for one owner's choice.** Estimate demand, stock,
   profit and cash for "raise taco prices 5%", with a calibrated-on-history range, assumption
   sensitivity and the owner's policy limits. No reviewed competitor documents this.
2. **Plain-language, approval-first action plans tied to those simulations.**
3. **Complementing, not replacing, the POS.** It runs on the exports a restaurant already has,
   for owners who won't pay $350/month for a back-office suite.
4. **For non-Toast owners:** a sourced, item-level view of local menu prices.

---

## E. Recommended features (scored 1 to 5; 5 is best, and for difficulty and cost, 5 is easiest or cheapest)

| Feature | Usefulness | Differentiation | Data available | Ease | Math reliability | Demo value | Low running cost | Total |
|---|---|---|---|---|---|---|---|---|
| Daily Owner Briefing + action checklist (1-click decision) | 5 | 3 (Toast IQ has a feed) | 5 (own data) | 4 | 4 | 5 | 5 | **31** |
| Real-restaurant product onboarding (3 files) | 5 | 3 | 3 | 2 | 4 | 3 | 5 | 25 |
| Risk-aware decisions (already built: ranges, sensitivity, limits) | 4 | 4 | 5 | 5 | 4 | 4 | 5 | 31 (done) |
| Local Market price check → Scenario Lab | 3 | 2 (Toast Benchmarking for Toast users) | 2 (few priced menus) | 4 | 4 (prices only, no demand) | 5 | 3 (manual re-verification) | **23** |
| Supplier invoice price alerts | 4 | 1 (MarginEdge, MarketMan, R365 do it) | 2 (needs invoices) | 2 | 4 | 3 | 4 | 20 |
| Automated competitor price monitoring | 2 | 2 | 1 (no permitted real-time source) | 1 | 3 | 3 | 1 | 13 |
| One-click "why did profit change" | 4 | 2 | 5 | 5 (exists as a tool) | 4 | 3 | 5 | 28 (exists) |

**Built now:**
1. **The Daily Owner Briefing with an action checklist.** This is the highest value: it uses data
   we have and saves owners time.
2. **The Local Market MVP.** You asked for it, it has strong demo value, and it is honest about its
   limits.

**Next, and most important after the hackathon:** real-restaurant product onboarding. It turns
everything else from a demo into a product.

---

## F. What was built (branch `milestone-5.3-local-market`)

- **`data/market/`** contains:
  - verified, sourced and dated public menu prices (23 rows, 6 restaurants);
  - a research log of the 13 restaurants checked.

  It is kept separate from private business data, and never used in forecasts or simulations
  (tested).
- **`src/decisiondesk/market.py`:**
  - serving normalization (3 × single taco = a plate of three);
  - match levels;
  - one representative item per restaurant;
  - peer minimum, median and maximum, sample size and position;
  - chain, type and freshness filters (stale after 45 days);
  - wording that never recommends a price.
- **`src/decisiondesk/briefing.py`:**
  - yesterday compared with the same weekday over the previous four weeks;
  - product flags only when a product fell more than guest traffic;
  - stock to watch, and bills due within 7 days;
  - one decision from the Scenario Lab's own ranking, with impact, risks, things to verify
    (including local price position) and approval required;
  - all text passes the number check (tested).
- **Analyst tools:** `get_local_market_prices` and `get_daily_briefing`, so Gemini has 15 tools.
  Offline routes cover "how do my taco prices compare…" and "what needs attention today?".
- **Interface:**
  - a new **Local Market** page: product, type filter, similar-serving toggle, price-position
    chart, sourced table with links, excluded items with reasons, an all-products table, and a
    monitoring log;
  - buttons that open the Scenario Lab at −5%, +5% or the peer median;
  - **Today's briefing** at the top of the Overview, with "Open in the Scenario Lab".
- **Tests:**
  - **225 passed** offline (17 new);
  - lint clean;
  - live tests not run;
  - existing forecasting, calibration, Scenario Lab, Gemini and Photon tests unchanged and
    passing.

**Launch:**
```bash
source .venv/bin/activate && git switch milestone-5.3-local-market
python -m decisiondesk.precompute
MSRD_LLM_PROVIDER=offline streamlit run app/streamlit_app.py
```

**Limitations.**
- Six verified restaurants, entered by hand on one date.
- City-wide comparison, not by radius (the fictional restaurant has no address).
- Mixed restaurant tiers in the default view.
- The briefing's "decision to consider" is for the best-selling product only.
- The briefing is not yet sent by iMessage on a schedule (the tools exist; a scheduled send needs
  the owner's opt-in).

---

## G. Positioning

1. **Why choose us over Toast or Square?**
   - Don't frame it as "instead of": they run the restaurant. If you're on Toast, use Benchmarking
     for peer sales.
   - We add one thing they don't document: test a specific decision before making it. You see the
     likely profit, the range, the stock impact and when the answer flips, written as a short
     checklist.
   - For owners on other systems, or paying for no back-office suite, we work from the exports
     they already have.
2. **Why not just upload a spreadsheet to ChatGPT?** You can, once. We give you the same answer
   every day:
   - from models tested on your own history;
   - with ranges checked against past mistakes;
   - with kitchen limits and your price rules built in;
   - with every number verified against the calculation;
   - and a briefing that arrives without you asking.

   These are hypotheses to prove with users, not proven advantages.
3. **What problem do we solve better?** "Should I change this price or this order, and what could
   go wrong?" Answered in minutes, with the downside shown, without a spreadsheet.
4. **What can we prove today?** The following, all on synthetic data:
   - correct accounting;
   - fair model selection;
   - ranges that hold on unseen windows;
   - a working simulation with policy limits;
   - grounded AI answers;
   - a live iMessage round trip;
   - verified local menu prices.
5. **What is only a hypothesis?**
   - That real owners will act on ranges.
   - That our forecasts work on a real restaurant.
   - The assumed price sensitivity.
   - That a daily briefing saves meaningful time.
   - Willingness to pay.
6. **How would we get the first paying customer?**
   - Find one independent Austin taqueria not on a back-office suite.
   - Offer a free 30-day pilot on its point-of-sale export, with a weekly briefing by text.
   - Ask, in advance, to pay a small monthly fee if the briefing saves an hour a week or flags one
     costly stock-out.
7. **What is the smallest thing an owner would pay for?** The daily briefing by text:
   - yesterday versus a normal day;
   - what needs attention;
   - one decision with its range and risks.

   It needs only the owner's daily sales export.

**30-second pitch.** "Small restaurant owners make money decisions by gut feel, because the tools
that help are built for chains. Main Street Risk Desk works like a part-time analyst. Every
morning it texts the owner what happened yesterday, what needs attention and one decision worth
considering, like a 5% taco price rise. It tests that decision against a thousand possible months
from the restaurant's own history, accounting for running out of ingredients, the owner's price
rules and where local menus sit. It shows the likely gain and when the answer would flip. It
doesn't replace the POS; it turns the data the owner already has into a clear daily decision. And
every number is checked."
