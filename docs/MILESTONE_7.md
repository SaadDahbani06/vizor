# Milestone 7: open-source readiness and real data ingestion

Date: 2026-11-15. Branch `milestone-7-open-source-ingestion` (local, not pushed).

## 1. What worked before

- Synthetic demo restaurant and hardware store with a full dashboard (Overview, My Business,
  Decision Lab, Local Market), Vizor AI on Gemini with an offline fallback, Photon iMessage.
- Forecast leaderboard with rolling-origin validation and an untouched holdout; Monte Carlo
  scenarios against no action; cash risk; number grounding for AI replies.
- A daily-totals CSV/Excel upload with column mapping (kept files on disk under `data/private/`).
- An offline test suite (FRED, Nessie, Gemini and Photon live tests are opt-in).

## 2. What was added

**Ingestion (`src/decisiondesk/ingest/`)**
- `ledger.py`: one normalized ledger for every source (source id, revision, date, amount, currency,
  category, account, counterparty); de-duplication where a newer revision replaces all lines of the
  older one; daily totals that never fill in values, never convert currencies, and refuse days
  where refunds exceed sales; reconciliation against a source's own report.
- `quickbooks.py`: QuickBooks Online over OAuth 2.0 (authorize URL with state check, code exchange,
  refresh with rotation, server-side token file with 0600 permissions), paged queries, 429 and 5xx
  retries, 401 refresh; Invoice, SalesReceipt, RefundReceipt, CreditMemo, Bill, Purchase,
  VendorCredit normalization (tax excluded, credits negative, item lines uncategorized, balance
  sheet lines excluded, home currency instead of a guessed one); Profit and Loss reconciliation;
  CLI `connect | status | pull`.
- `square.py`: Orders and Refunds, net of tax and tips, on the location's local day, refunds scaled
  to the same basis; explicitly sales only.
- `files.py`: transaction exports (signed amount or money in/out), suggested column mapping,
  owner-confirmed category treatment, errors that name columns and rows, stable ids so re-uploads
  do not double count.

**Engine and app**
- `BusinessSession.from_ledger()` feeds ledger data into the existing tools; forecasting, cash
  risk, scenarios and Vizor AI are unchanged.
- Settings → Data now has four tabs (daily totals file, transactions file, QuickBooks sandbox,
  Square sandbox), an import report with reconciliation, and a way back to the demo.
- The header labels every mode: "Demo Business · Sample Data", "QuickBooks sandbox · test company",
  "Your data · uploaded file / uploaded transactions".
- Missing-data handling instead of fabricated results: no forecast or whole-business simulation
  below about 504 days (the page says why); "Not entered" cash without an opening balance (cards
  and Vizor AI); "Not in data" inventory without stock records.
- Privacy: uploaded files go to a temporary file that is deleted after validation; fitted models
  for non-demo data stay in session memory and are no longer pickled to `data/cache/`.
- Bug found in a real browser and fixed: importing data raised a Streamlit error because the page
  was switched after the navigation widget was drawn (regression test added).

**Open source**
- README rewritten; LICENSE (MIT, copyright holder left for the founders); CONTRIBUTING.md;
  SECURITY.md (scan results, rotation steps, data handling, real-customer checklist);
  `docs/MODELS.md`; `docs/INGESTION.md`; `docs/STATUS.md` (the old detailed status list);
  `.env.example` with `QBO_*` and `SQUARE_*`; `.gitignore` for secrets, token files and Excel files;
  GitHub Actions (`ruff` + `pytest` on Python 3.11 and 3.13).

## 3. Tests and what they prove

| Check | Result |
|---|---|
| `pytest -q` | 307 passed, 12 skipped (opt-in live tests) |
| `ruff check .` | clean |
| `tests/test_ingestion.py` | 33 passed, 2 skipped (live sandbox tests, no credentials) |
| Real-browser run (headless Chrome, offline analyst) | transactions file uploaded → categories confirmed → dashboard built, provenance shown, outlook warning shown, no exceptions |

The ingestion tests are **functional**: they show the connectors handle documented API responses,
paging, token refresh, rate limits, revisions, duplicates and reconciliation correctly against
mocks. They do not show forecast accuracy, and they are not a substitute for a live sandbox run.

## 4. Which integrations were actually tested

| Integration | Mocked tests | Live |
|---|---|---|
| QuickBooks Online | yes (OAuth, paging, retries, normalization, reconciliation, app import) | **not run**: no Intuit credentials configured |
| Square | yes (orders, refunds, paging, time zone, minor units) | **not run**: no Square credentials configured |
| Transaction / daily files | yes, plus a real-browser upload of a synthetic bank export | n/a (local) |
| Intuit and Square hosts | reachable (HTTP 401 without credentials, 2026-11-15) | no authenticated call made |

## 5. Which data remains synthetic

All business data in the repository and the demo: the two demo businesses, their product layer and
ingredient ledger, the Nessie sandbox records, and every file used in tests (including the bank
export used in the browser run). The only real data are the public Austin menu prices and the
research datasets (FRED, Kwick365), none of which are business records.

## 6. Credentials and manual steps needed

- **QuickBooks sandbox:** Intuit developer account and app (Accounting scope), redirect URI,
  `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_REDIRECT_URI` in `.env`, then
  `python -m decisiondesk.ingest.quickbooks connect` once. Then `QBO_LIVE_TEST=1 pytest -k live`.
- **Square sandbox:** developer application, sandbox access token and location id in `.env`.
- **Before publishing the repository:** choose the copyright holder in `LICENSE`; replace the
  personal email in commit metadata (see SECURITY.md); enable GitHub private vulnerability
  reporting.

## 7. Still required for a secure real-customer deployment

Authentication and per-tenant storage (today one deployment = one business, and the QuickBooks
connection and messaging store are shared by everyone who can open the app); encrypted secrets;
HTTPS; a Gemini tier that does not train on customer data; Intuit and Square production review;
log and retention policy with disconnect and delete; live sandbox tests and a supervised pilot with
reconciliation checked by the owner; dependency and secret scanning; legal review. Details in
`SECURITY.md`.

Known gaps in ingestion: QuickBooks journal entries, deposits and inventory (item) costs are not
imported, so reconciliation will flag differences for companies that use them; deleted QuickBooks
transactions are only removed by a fresh pull (no Change Data Capture yet); amounts display with a
`$` sign whatever the currency; product-level pages need item data that no connector provides yet.
