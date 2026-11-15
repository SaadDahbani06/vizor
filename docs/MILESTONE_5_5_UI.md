# Milestone 5.5: premium owner experience ("simplify the interface, not the intelligence")

Branch `milestone-5.5-premium-ui` (from 5.4). Committed locally, not pushed or deployed.

## Principle

The owner sees what is happening, why it matters and what they can do. Model names, simulation
methods, calibration statistics and tool names are gone from the main screens. They live in
"How we estimated this" panels, in Settings → Advanced diagnostics, and in the docs. None of
the analytics changed: forecasting, product demand models, Monte Carlo ranges (5.1 calibration),
inventory accounting, cash flow, decision ranking, and number verification all run exactly as
before. Material downside risk stays visible on screen (a downside / expected / upside bar and a
"Main risks" list).

## Design system (`src/decisiondesk/ui/theme.py`)

- **Tokens:** warm off-white canvas, white 16 px cards with hairline borders and soft shadows,
  Inter and Inter Tight, one brand colour (cobalt), and reserved positive, negative and warning
  colours that always come with words.
- **Components:**
  - KPI card with a hover tooltip;
  - delta pill (green when the move is good for the business);
  - status pill;
  - range bar (downside, expected, upside, with a zero line);
  - before-and-after table;
  - price-position bar;
  - plain-language confidence phrase (`confidence_words`).
- **Motion:** cards rise in gently, KPI and product cards lift on hover, and the assistant
  panel slides in.
- **Chart palette:** unchanged and re-validated on white (`validate_palette.js`: all PASS).
- **Trend chart:** a new chart groups sales and profit into 7-day blocks that end on the last
  data day. Calendar weeks would have drawn a false drop in the latest, partial week.

## Pages

- **Header:** logo, business name and one "Demo Business · Sample Data" label (its tooltip
  explains the synthetic data once); the four-page navigation; a notification bell; and a
  Settings menu (switch demo business, Notifications, Data & upload, Advanced diagnostics). The
  Streamlit header and sidebar are hidden.
- **Overview:**
  - a daily briefing sentence ("Sales were lower yesterday, 31% below a usual Friday, mainly
    because fewer customers came in. Two items sold less than usual, and three bills ($4,978)
    are due this week.");
  - up to three items to review;
  - five KPI cards: sales, profit, cash with bill coverage, costs with the cost rising most, and
    inventory health;
  - the sales and profit trend;
  - one opportunity: tacos +5%, expected +$286, "Very likely to help, though a loss is
    possible", with the downside −$5 and upside +$557 shown;
  - where the money goes, and your items.
- **My Business:**
  - Sales & profit: KPI cards, a trend chart for any metric, sales by item, and a revenue
    breakdown;
  - Products: cards, a sortable table, and a product detail view with a back button;
  - Costs, Stock, Cash & bills, and Outlook. Outlook says "Forecast based on your historical
    business performance"; the method sits behind a tip.
- **Decision Lab:**
  - type a what-if ("What if I increase taco prices by 5%?") or pick a kind of change;
  - simple controls with the owner's price limit shown;
  - the profit impact with a confidence phrase and range bar;
  - a before-and-after table (revenue, profit, cash, demand, units sold, ingredients left,
    sell-out chance);
  - main risks, a recommended next step, and approval required;
  - a full checklist, charts, "How we estimated this" (exact range, assumptions, sensitivity,
    range check) and whole-business options in expanders.
- **Local Market:**
  - a freshness status ("Prices up to date · checked Nov 14");
  - your price against the nearby range for all five items (a range bar with the middle price
    and your price);
  - "No fair comparison" where we found no equivalent serving;
  - "Worth investigating" cards. These state price position only, and keep the warning that
    competitors' prices are not a reason to change a price;
  - a one-item comparison with sources and dates;
  - links to the Decision Lab.
- **Ask AI:**
  - a side panel on every page, with a greeting, four suggested prompts while the conversation
    is empty, bubbles, a typing indicator, and visible errors;
  - multi-turn follow-ups, and "New conversation";
  - each reply can carry links that open the matching view, for example "Open this in the
    Decision Lab" with the scenario already applied;
  - the offline replies were rewritten in business language and still pass the number check.

## What was removed

There is no Economy, Messaging or Evidence page, and no Scenario Lab or AI Analyst page. The
sidebar is gone. On the main screens there are no model names, no "simulated futures", and no
origin pill on every chart. Settings → Advanced diagnostics keeps validation, calibration,
reconciliation, assumptions, banking and alert rules.

## Verification (2026-11-14)

- `pytest -q`: **250 passed**, 10 skipped (opt-in live). The UI tests include:
  - no jargon on any main screen;
  - downside visible;
  - the typed what-if;
  - the product detail view;
  - Local Market opportunities;
  - assistant links;
  - a slow Gemini reply that does not block the dashboard;
  - errors shown to the user.
- `ruff check`: clean.
- **Browser:** headless Chrome at 1440 px, offline analyst. The Overview, My Business
  (products), Decision Lab, Local Market and the open assistant panel were inspected visually.
  Issues found and fixed during the review:
  - the last-week dip in the trend chart;
  - uneven card heights;
  - table borders;
  - the page squeezing when the assistant opens on narrower screens (it now overlays below
    1500 px).
- **Numbers:** unchanged. Tacos +5% gives +$286 (−$5 to +$557, 95%), and a supplier +8% change
  gives −$1,820.

## Launch

```bash
cd ~/Desktop/main-street-decision-intelligence
git switch milestone-5.5-premium-ui
./start.sh offline     # opens http://localhost:8501; instant offline analyst
./start.sh             # with Gemini, if GEMINI_API_KEY is in .env
```

## Known limits

- The assistant panel overlays the page on screens narrower than 1500 px.
- Table money columns show no thousands separators (a Streamlit formatting limit).
- The price-position chart's axis labels can crowd at narrow widths.
- "Offline mode" understands a fixed set of question patterns. Free-form questions need Gemini,
  which has taken minutes per tool-calling answer.
