# Milestone 5.4 research: who this is for, what they need, and who else serves them

Researched 2026-11-14. Every figure below has a source link. Where we have no evidence, we say
so. **We did not estimate a market size or what owners would pay**; both are left as questions for
pilots. Competitor descriptions come from the vendors' own pages or press and from third-party
review sites, and are labelled as such. We did not test any competitor product.

The political and regulatory section is factual context for product design (labor and tax rules
that change an owner's costs). It is not political profiling. The product does not collect or use
anyone's political views, and the Economy dashboard is not coming back.

---

## 1. Size of the space (published counts only)

| Fact | Figure | Source |
|---|---|---|
| U.S. small businesses | 36,207,130; 82.3% have no employees and 17.7% have paid employees | [SBA Office of Advocacy, FAQ 2026](https://advocacy.sba.gov/wp-content/uploads/2026/02/FINAL_FAQsAboutSmallBusiness_2026_012826.pdf) |
| Small-business share of private employment | 45.9% (62.3 million people) | same |
| Restaurant locations | "1M+" | [National Restaurant Association, State of the Industry 2025 pocket guide](https://go.restaurant.org/rs/078-ZLA-461/images/SOI-2025-PocketGuide.pdf?version=0) |
| Restaurant workforce | 15.9 million (2025 projection) | [NRA press release](https://restaurant.org/research-and-media/media/press-releases/restaurant-industry-poised-for-growth-in-2025-industry-expected-to-employ-15-9-million-people-and-r/) |
| Operator sentiment | 60% of operators said business conditions got worse in 2025 | [NRA State of the Industry 2026](https://go.restaurant.org/rs/078-ZLA-461/images/SOI-2026-Report.pdf?version=0) |

We do not turn these into a "serviceable market" number. That would need the share of
businesses with usable digital records, and the share willing to pay, and we have neither.

## 2. Target audience: fit by business type

"Fit" means how well the *current* code matches the business's data and decisions. It is our
judgement from the data model, not survey evidence.

| Type | What they decide often | Data they usually have | Fit today | Why |
|---|---|---|---|---|
| Counter-service restaurant / taqueria | menu prices, ingredient orders, staffing | POS item sales, supplier invoices, bank | **High** | Matches the product layer exactly: recipes, shared ingredients, sell-outs, Mon/Thu deliveries |
| Café / coffee shop | prices, pastry and milk orders, hours | POS item sales, invoices | **High** | Same recipe-and-ingredient structure; many small, frequent items |
| Bakery | production quantities, prices, waste | POS sales, recipes, invoices | **Medium-high** | Recipes fit; baking *production* planning (bake today, sell tomorrow) is not modelled yet |
| Food truck | menu, stock per day, locations | POS sales (often Square/Toast), invoices | **Medium** | Recipes fit; location changes drive demand and are not modelled; phone-first use suits iMessage |
| Small retail shop | reorder points, budgets, markdowns | POS item sales, purchase orders | **Medium** | Business-wide inventory policies exist; product-level retail (no recipes) is not built yet |
| Service business (salon, repair) | staffing, prices, cash | bookings, bank | **Low** | No inventory; only the cash, bills and forecast parts apply |

## 3. Jobs to be done (hypotheses to test in interviews)

1. *When I open in the morning*, tell me what changed yesterday and what needs attention, so I
   don't dig through the POS. (Briefing, Overview.)
2. *When a supplier raises prices*, show what it does to my profit this month and what I could do
   about it. (Supplier ingredient prices in the Decision Lab.)
3. *Before I change a price*, show what could happen to orders and profit, and how sure we are,
   so I don't guess. (Decision Lab: likely range, chance of beating the current plan, assumption
   edges.)
4. *Before the next delivery*, warn me if something will run out, so I don't 86 a best seller.
   (Sell-out risk, days of supply.)
5. *This week*, tell me whether cash covers the bills. (Cash & bills, cash simulation.)
6. *Between rushes*, let me ask a question from my phone and get a straight answer. (iMessage.)

None of these is validated with real owners yet. The pilot plan in `docs/ONBOARDING.md` tests
them.

## 4. Political, economic and regulatory context (facts only)

These rules change an owner's costs and the decisions the product should support. The product
models none of them automatically; the owner enters cost changes as scenarios.

| Topic | Fact | Source | What it means for the product |
|---|---|---|---|
| Federal minimum wage | $7.25/hour | [U.S. DOL, tipped minimum wage table](https://www.dol.gov/agencies/whd/state/minimum-wage/tipped) | Labor is the largest cost in the demo (37% of expenses); wage changes should be a cost scenario |
| Federal tipped wage | Cash wage at least $2.13/hour; maximum tip credit $5.12; tips plus cash wage must reach $7.25 each workweek; overtime is calculated on the full $7.25 | [DOL Fact Sheet #15](https://www.dol.gov/agencies/whd/fact-sheets/15-tipped-employees-flsa) | Full-service restaurants need tips handled separately from revenue when importing |
| Tip ownership | Employers may not keep employees' tips | [DOL Fact Sheet #15A](https://www.dol.gov/agencies/whd/fact-sheets/15a-flsa-tip-ownership) | Tips are not business revenue; the importer must exclude them |
| State rates differ | Many states set minimums above the federal rate | [DOL state table](https://www.dol.gov/agencies/whd/state/minimum-wage/tipped) | The owner states the wage; we never assume one rate |
| Texas minimum wage | Texas adopts the federal rate ($7.25) under the Texas Minimum Wage Act | [Texas Workforce Commission](https://www.twc.texas.gov/programs/wage-and-hour/texas-minimum-wage-law) | The demo restaurant is in Austin |
| Texas sales tax | 6.25% state plus up to 2% local (8.25% maximum); food ready to eat is taxable | [Texas Comptroller, sales tax](https://comptroller.texas.gov/taxes/sales/) and [local tax FAQ](https://comptroller.texas.gov/taxes/sales/faq/local.php) | Sales tax is collected for the state, so imports must take revenue *net of* sales tax |
| Texas franchise tax | Entities with annualized revenue at or below $2.65 million (2026 reports; $2.47 million for 2024–2025) owe no franchise tax but still file an information report | [Texas Comptroller, April 2026 Tax Policy News](https://comptroller.texas.gov/taxes/tax-policy-news/2026-april.php) | Not modelled; a bill-calendar reminder at most |

Out of scope: forecasting policy changes, election outcomes, or anything about owners' or
customers' political views.

## 5. Competitive positioning

| Product | What it does (as described by the source) | Price (source) | Overlap with us |
|---|---|---|---|
| **Toast IQ** | Conversational assistant in Toast; a "For You" feed of business and local-market insights; proactive recommendations; acting from chat ([Toast support](https://support.toasttab.com/en/article/Toast-IQ-Overview), [Toast blog](https://pos.toasttab.com/blog/on-the-line/restaurant-ai-tools)). Toast Benchmarking compares local sales (Milestone 5.3 research) | "Included with Toast POS at no additional cost" ([Toast AU page](https://pos.toasttab.com/au/products/toast-iq)) | **High**: briefing, Q&A, local context |
| **Square AI / Managerbot** | Square AI answers questions about sales, customers and staff ([Square press](https://squareup.com/us/en/press/square-ai-open-beta)). Managerbot (open beta, April 2026) watches the business, flags problems and proposes actions on inventory, scheduling and marketing **for the seller to approve** ([Square press](https://squareup.com/us/en/press/managerbot-open-beta), [VentureBeat](https://venturebeat.com/data/block-introduces-managerbot-a-proactive-square-ai-agent-and-the-clearest)) | No additional cost for most non-franchise U.S. food, retail and beauty sellers ([PYMNTS](https://www.pymnts.com/artificial-intelligence-2/2026/new-square-ai-agent-automates-daily-tasks-for-main-street-sellers)) | **High**: proactive alerts, approval-first actions |
| **MarginEdge** | Invoice capture (photo, email, EDI), recipe costs that update from invoice prices, vendor price comparison ([marginedge.com](https://www.marginedge.com)) | $350/month per location (vendor site, per [xpay.sh snapshot](https://www.xpay.sh/saas-pricing/marginedge/)) | **Medium**: food cost; it has the invoice data we lack |
| **MarketMan** | Inventory, ordering, receiving, recipe costing, price-change alerts, demand forecasting ([marketman.com](https://marketman.com/), [Capterra](https://www.capterra.com/p/136439/Marketman-Restaurant-Inventory/reviews/Capterra___4733969/)) | About $199–249/month in third-party listings, which conflict ([The Restaurant HQ](https://www.therestauranthq.com/technology/marketman-review/)) | **Medium**: ordering and stock |
| **Restaurant365** | Accounting plus operations (inventory, scheduling) for 1–50+ locations ([restaurant365.com](https://www.restaurant365.com/blog/8-affordable-restaurant-software-options-for-small-teams-and-growing-chains/)) | Sales-led; third parties quote about $249 per location per month and up, inconsistently ([Capterra](https://www.capterra.com/p/139768/Restaurant365/pricing/)) | **Low–medium**: back office |
| **General chat assistants** (ChatGPT, Claude, Gemini) | Flexible explanations of anything the owner pastes in | Free to paid tiers | **Medium**: they explain well, but have no built-in link to the owner's records and no check that each number matches a calculation on them |

**Honest read.** The briefing, the chat, and approval-before-action are no longer unusual: Toast
and Square now bundle them at no extra cost for their own POS customers. A newcomer cannot win on
"an AI that talks about your sales".

**What is different in what we built** (we found no public description of these in the
materials above, which is not proof they are absent):

1. **What-if simulation against the current plan.** Change a price, an ingredient order or a
   supplier cost, and see revenue, profit, cash, demand and sell-out risk with a likely range,
   the chance of beating the current plan, and what happens at the edge of each assumption.
2. **Every number is checked.** Analyst answers are verified against the calculations,
   including what each number is said to be.
3. **Uncertainty is tested, not asserted.** Ranges are checked on past windows the model never
   saw (synthetic data so far).
4. **POS-independent by design** (CSV import). This is also a weakness: Toast and Square see the
   data live, and we need an export.

## 6. Recommended first segment

**Independent, single-location counter-service restaurants and cafés with a short, recipe-based
menu, whose POS exports item-level daily sales, and whose owner sets prices and places supplier
orders personally.**

Why:
- The code already models this business: recipes, shared ingredients, Mon/Thu deliveries,
  sell-outs, price sensitivity and supplier cost changes.
- The owner makes the decisions the Decision Lab tests (prices, orders), with no head office.
- Supplier prices and labor are their largest controllable costs (labor was 37% of demo
  expenses; on real businesses this must be measured).
- Single location keeps onboarding to a few files (see `docs/ONBOARDING.md`).

Second: food trucks and bakeries (same structure, plus location or production planning).
Not now: multi-unit groups (they already buy R365-type tools) and service businesses (no
inventory).

**Open questions for pilots (not answered here):**
- Do owners already on Toast or Square want a separate what-if tool? (Priority: interview owners
  on other POS systems first, and check for each POS whether it already includes an assistant.)
- What would they pay? (Unknown; ask, don't assume.)
- Is the forecast accurate on a real restaurant? (Unknown until a pilot; the same rolling-origin
  test will measure it.)
