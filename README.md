# Vizor

Vizor helps the owner of a small restaurant or shop answer one question each day: *what should I
look at, and what happens if I change something?* It reads the business's daily sales and costs,
forecasts the next four weeks with tested models, simulates decisions such as a price change or a
new supplier cost against the current plan, and explains the results in plain language. Vizor AI
(Google Gemini) only explains: every number comes from deterministic Python code and is checked
before it is shown.

> **Status: hackathon project (HackPrinceton), not production software.** The demo businesses use
> synthetic (computer-generated) records. The QuickBooks Online and Square connectors are tested
> against mocked API responses only; they have not yet been run against a live sandbox company.
> No accuracy claim in this repository applies to a real business. See [Limitations](#limitations).

## The problem

Owners of independent restaurants and shops make pricing, ordering and staffing decisions with
little time and no analyst. Point-of-sale and accounting tools show what already happened. Vizor
tries to fill the next step: a tested forecast with an honest range, and a side-by-side comparison
of "do nothing" against the changes the owner can actually make, with the assumptions written down.

## Features

- **Overview:** yesterday compared with a typical same weekday, up to five labelled alerts, month to
  date revenue, profit, cash and expenses.
- **My Business:** sales and profit, products, costs, stock, cash and bills, and a four-week outlook.
- **Decision Lab:** change a price, an order quantity, a supplier cost, demand or optional spending
  and compare with the current plan: likely range, chance it is better, what to verify. Any action
  needs the owner's approval; nothing is executed automatically.
- **Decision follow-up:** an approved decision's prediction is saved, and once the owner marks the
  change as made, Vizor compares it with later sales (as predicted / above / below). The demo
  shows this with a clearly labelled synthetic replay. Learning price sensitivity from one test
  was measured not to work, so it is reported, never applied. See
  [docs/DECISION_FOLLOWUP.md](docs/DECISION_FOLLOWUP.md).
- **Local Market:** dated, manually verified public menu prices of comparable Austin restaurants.
- **Vizor AI:** a chat panel on every page. Common questions are answered instantly from verified
  numbers; analysis questions run the engines and Gemini writes one explanation that must cite the
  same numbers; open questions use Gemini tool calling. A rule-based offline mode needs no API key.
- **Your data (Settings → Data):** load a daily-totals file, a transaction export (bank, accounting
  or POS) with column mapping and category confirmation, or connect a QuickBooks Online or Square
  sandbox. The header always says where the numbers come from.
- **iMessage (optional):** morning briefings, alerts and questions over Photon, opt-in only.

## How it works

```
 data sources                      normalized ledger           engine                        interface
 ───────────────                   ─────────────────           ──────                        ─────────
 demo (synthetic)  ─────────────────────────────────┐
 daily-totals CSV/Excel ────────────────────────────┤
 transactions CSV/Excel ─┐                          ├─► validated daily     ─► forecasting       ─► Streamlit
 QuickBooks Online API ──┼─► ledger entries ─► daily│   totals (one row      leaderboard          dashboard
 Square API (sales only)─┘   (dedupe, revisions,    │   per day, gaps        (rolling-origin)  ─► Vizor AI
                              currency, P&L check) ─┘   reported)          ─► Monte Carlo         (explains
                                                                              scenarios          only)
                                                                           ─► cash risk,       ─► iMessage
                                                                              ranking vs          (Photon)
                                                                              no action
```

- `src/decisiondesk/ingest/`: connectors and the shared ledger (`ledger.py`, `quickbooks.py`,
  `square.py`, `files.py`). See [docs/INGESTION.md](docs/INGESTION.md).
- `src/decisiondesk/data.py`, `profile.py`: validation of daily totals and column mapping.
- `src/decisiondesk/leaderboard.py`, `models.py`, `backtest.py`, `calibration.py`: forecasting and
  out-of-sample checks.
- `src/decisiondesk/decision/`: scenario simulation and ranking against no action.
- `src/decisiondesk/agent/`: tools, router, Gemini client and the number-grounding check.
- `src/decisiondesk/messaging/`: Photon iMessage worker.
- `app/streamlit_app.py`, `src/decisiondesk/ui/`: the dashboard.

The models, assumptions and their limits are described in [docs/MODELS.md](docs/MODELS.md).

## Installation

Python 3.11 or newer, macOS or Linux (Windows works with `.venv\Scripts\activate`).

```bash
git clone https://github.com/SaadDahbani06/vizor.git && cd vizor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt      # = pip install -e '.[dev,ml,ui,agent,messaging]'
cp .env.example .env                 # every key is optional; the demo runs offline
```

## Run the demo

```bash
python -m decisiondesk.precompute    # optional: warm the forecast cache once (a few minutes)
./start.sh offline                   # opens http://localhost:8501 with the offline analyst
./start.sh                           # same, with Vizor AI on Gemini if GEMINI_API_KEY is set
```

The demo opens on a synthetic Austin taco restaurant. A synthetic hardware store is available from
the header. Optional extras:

```bash
python -m decisiondesk.messaging.worker run      # answer iMessages (needs PHOTON_* keys; one poller only)
python -m decisiondesk.ingest.quickbooks connect # connect a QuickBooks sandbox company (docs/INGESTION.md)
python -m decisiondesk.demo --industry restaurant
```

## Data modes

| Mode | Header label | What it needs |
|---|---|---|
| Demo | Demo Business · Sample Data | nothing |
| Daily totals file | Your data · uploaded file | date, revenue, variable costs, fixed costs |
| Transactions file | Your data · uploaded transactions | date and amount (or money in / out), confirmed categories |
| QuickBooks Online | QuickBooks sandbox · test company | Intuit developer app, sandbox company, `QBO_*` in `.env` |
| Square | (preview only) | sandbox access token and location; sales only, so it cannot be analysed alone |

Every analysis checks it has enough data first. With less than about 504 days of history the
four-week forecast and the whole-business simulations are not shown, and the page says why;
records, profit and costs are still shown. Without an opening cash balance there is no cash figure.

## Tests

```bash
pytest -q                     # offline and mocked: no network, no keys
ruff check .
MSRD_LIVE=1 pytest -m live    # optional live checks of FRED, Nessie, Gemini, Photon (read-only)
QBO_LIVE_TEST=1 pytest tests/test_ingestion.py -k live   # optional, after connecting a QuickBooks sandbox
```

The ingestion tests (`tests/test_ingestion.py`) are functional tests of API handling, normalization,
de-duplication and reconciliation against mocked responses. They do not measure forecast accuracy.
GitHub Actions runs ruff and the test suite on Python 3.11 and 3.13 (`.github/workflows/ci.yml`).

## Limitations

- **No real business has been evaluated.** Forecast accuracy figures come from synthetic data
  (software checks) and one real aggregate restaurant index (Kwick365). They say nothing about how
  well Vizor forecasts a particular business.
- **Action effects are assumptions.** Price sensitivity, customers switching between items, and
  supplier outcomes are stated ranges, not effects estimated from data. Forecasts are not causal.
- **Integrations not live-tested:** QuickBooks Online and Square have been built against Intuit's
  and Square's documented APIs and tested with mocks only.
- **Single tenant.** One deployment serves one business. QuickBooks tokens and the messaging store
  are per deployment, not per user. See [SECURITY.md](SECURITY.md) before handling real data.
- **Product-level pages** (products, Scenario Lab, Local Market) need per-item sales, recipes and
  stock; only the demo restaurant has them. Uploaded or connected data gets the business-level pages.
- **Currency:** amounts are never converted between currencies; the display always uses a `$` sign.
- The full milestone-by-milestone status, including what failed, is in [docs/STATUS.md](docs/STATUS.md).

## Documentation

- [docs/INGESTION.md](docs/INGESTION.md): QuickBooks, Square and file import setup.
- [docs/MODELS.md](docs/MODELS.md): financial models, assumptions and limitations.
- [docs/MILESTONE_7.md](docs/MILESTONE_7.md): ingestion and open-source readiness report.
- [docs/STATUS.md](docs/STATUS.md): detailed implementation status.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/EVALUATION.md](docs/EVALUATION.md),
  [docs/PRESENTATION.md](docs/PRESENTATION.md), [docs/DEVPOST.md](docs/DEVPOST.md).

## Team

Built at HackPrinceton by [@SaadDahbani06](https://github.com/SaadDahbani06) and
[@GiosArchive](https://github.com/GiosArchive).

## Contributing, security and license

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Vizor is released under the
MIT License ([LICENSE](LICENSE)).

## Attribution

This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St.
Louis. Restaurant benchmarks: Kwick365, The Independent Restaurant Index (https://kwick365.com).
Iowa Liquor Sales: Iowa Department of Revenue, CC BY 4.0. QuickBooks is a trademark of Intuit Inc.;
Square is a trademark of Block, Inc.; Vizor is not affiliated with either.
