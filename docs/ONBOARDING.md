# Onboarding a real business: plan (high-priority follow-up, not built yet)

**Status, 2026-11-14.** Real businesses can upload **daily totals** today (date, revenue, costs,
optional units and cash): Business & data → Upload. Forecasts, the cash simulation, the
whole-business options and the analyst all work on that upload. **Product-level features**
(products, recipes, stock, sell-out risk, the product Decision Lab, Local Market) work only on the
synthetic demo restaurant. The reason is in the code: the five products, their recipes and the
ingredient list are module constants (`products.PRODUCTS`, `BOM`, `INGREDIENTS`), and the
Decision Lab engine (`decision/product_scenario.py`) indexes them directly.

This plan is the smallest sound path from "demo restaurant" to "your restaurant". It is a
refactor and an importer, not a rewrite. Every step keeps the demo's numbers identical, and tests
prove that.

## The files we would ask for

All are plain CSV exports. No live POS connection in the first version.

| File | Columns | Typical source | Required? |
|---|---|---|---|
| `item_sales.csv` | `date, item, units_sold, net_sales` (+ optional `sold_out_time`) | POS "product mix" / item sales report, daily | Yes |
| `menu.csv` | `item, price, category` (+ optional `price_changed_on`) | POS menu export | Yes |
| `recipes.csv` | `item, ingredient, quantity, unit` | Owner or kitchen manager (one-time) | Yes for stock and supplier-cost features |
| `ingredients.csv` | `ingredient, unit, unit_cost, supplier, delivery_days, shelf_life_days` | Latest invoices | Yes for stock features |
| `inventory_counts.csv` | `date, ingredient, on_hand` | Weekly or daily counts | Recommended (else stock is estimated from purchases minus recipe usage) |
| `invoices.csv` | `date, supplier, ingredient, quantity, unit_price, total, due_date` | Supplier invoices or AP export | Recommended (cost changes, bills due) |
| `daily_finance.csv` | the existing daily-totals upload | Accounting or bank export | Yes (already supported) |

**Revenue must be net of sales tax and tips.** Texas collects 6.25% state plus up to 2% local
sales tax on prepared food, and tips belong to employees (see `docs/MILESTONE_5_4_RESEARCH.md`).
The importer will ask which columns include them, rather than guess.

## Checks before anything is shown (each one blocks or warns, visibly)

1. **Continuity:** no missing days, or explicitly declared closures (the existing validator).
2. **Reconciliation:** the item `net_sales` for each day add up to daily revenue within a stated
   tolerance. Any remainder becomes "other items", as in the demo. Days off by more than 2% are
   listed.
3. **Units and recipes:** every recipe unit converts to the ingredient's unit; every top-selling
   item has a recipe. Recipe coverage of item revenue is reported (for example "recipes cover
   91% of item sales").
4. **Stock sanity:** counts never negative; usage implied by recipes × sales is compared with
   purchases minus count changes ("unexplained usage" = waste, theft or recipe error, reported,
   not hidden).
5. **History length:** at default settings the forecast leaderboard needs at least 504 days of
   daily history (28-day horizon, 6 final-test folds); fewer folds or a shorter horizon lower
   that, with less certain error estimates. Ranges with too few test windows are marked "not
   validated" (already implemented in the UI).
6. **Privacy:** files stay in `data/private/` (gitignored). The analyst sends tool results, never
   raw files, to the language model.

## Smallest sound implementation (in order)

1. **Catalog object (refactor, no behaviour change).** Introduce `Catalog` (products, prices,
   ingredients, recipe matrix, which items customers switch between) and pass it through
   `ProductContext` instead of the module constants. The demo builds its catalog from today's
   constants. *Test:* every demo number (dashboard, Decision Lab, briefing) is bit-for-bit
   identical; the existing 260+ tests stay green.
2. **Item-sales importer.** `item_sales.csv` + `menu.csv` → product sales table, reconciliation
   (check 2) and per-item demand forecasts with the existing leaderboard. This unlocks the
   product table, product charts and the item-mix briefing alerts, without recipes.
3. **Recipes and ingredients.** `recipes.csv` + `ingredients.csv` (+ counts or invoices) →
   ingredient ledger and stock. This unlocks sell-out risk, days of supply, ingredient orders and
   supplier price changes in the Decision Lab.
4. **Invoices → bills and cost alerts.** Supplier price changes detected from invoices feed the
   "ingredient costs are a bigger share of sales" alert with the real supplier and item named.
5. **Generalize names.** Local Market comparisons stay demo-only until a business opts in with
   its city and its own list of comparable items. No automatic competitor matching.

Steps 1–2 are the high-priority follow-up. Each step ships with tests and a sample anonymized
file.

## What stays an assumption, even with real data

- **Price sensitivity.** A restaurant rarely changes one item's price in isolation, so a real
  history usually cannot measure it. It stays a stated range until the owner runs a deliberate
  price test. (If a history does contain clean single-item price changes, estimating from them
  is a separate, explicitly validated step.)
- **Customers switching dishes** and **marketing response**: assumptions with ranges.
- **Causal claims:** none. The Decision Lab compares simulated futures under stated assumptions.

## Pilot plan (to answer the open questions in the research report)

- 5–10 owner interviews (counter-service restaurants and cafés): which morning questions they
  ask, which decisions they make weekly, what they export today.
- 2–3 pilot businesses with enough item-sales history (about 18 months for the default test
  settings): run the same rolling-origin test and report the real forecast error next to "same
  day last week", with no tuning on the test windows.
- Ask, don't assume, what they would pay.
