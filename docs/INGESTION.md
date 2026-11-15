# Connecting data: QuickBooks Online, Square and files

All sources are converted into one **ledger** (`src/decisiondesk/ingest/ledger.py`), one row per
transaction line, before anything is analysed:

| Field | Meaning |
|---|---|
| `entry_id` | `source:entity:source_id:line`, the de-duplication key |
| `source_id`, `sync_token` | the provider's own id and revision, so every number traces back |
| `txn_date` | transaction date (QuickBooks: invoice/bill date, accrual basis) |
| `amount`, `currency` | positive magnitude in the source's currency; refunds and credits are negative |
| `category` | `revenue`, `variable_cost`, `fixed_cost`, `uncategorized` or `excluded` |
| `account_*`, `counterparty`, `description` | as reported by the source |

Rules that apply to every source:

- **Nothing is invented.** Records without an id, date or amount are skipped and counted in the
  import report. Missing currency is never guessed.
- **No double counting.** Re-importing the same data changes nothing. When QuickBooks returns a
  newer revision (`SyncToken`) of a record, it replaces every line of the older one.
- **No currency conversion.** Entries in other currencies are left out of the totals and reported.
- **Honest daily totals.** Days without transactions count as zero and are reported. A day where
  refunds exceed sales is refused, not clipped.
- **Uncategorized stays out.** Lines Vizor cannot classify are excluded from profit and reported
  with their total, so the owner can map them.

The import report (records read, entries, duplicates, skipped, totals, reconciliation, warnings)
is shown on the Data page before anything else.

## QuickBooks Online (sandbox first)

**Status: implemented and tested with mocked API responses. Not yet run against a live sandbox.**

What is imported (`ingest/quickbooks.py`):

| QuickBooks entity | Ledger treatment |
|---|---|
| Invoice, SalesReceipt | revenue, total minus sales tax |
| RefundReceipt, CreditMemo | negative revenue |
| Bill, Purchase (account-based lines) | Cost of Goods Sold → variable cost; Expense / Other Expense → fixed cost, or variable if the account name matches a variable-cost keyword (food, supplies, packaging, merchant fees, freight...); balance-sheet accounts → excluded |
| Purchase with `Credit=true`, VendorCredit | negative cost |
| Item-based expense lines | uncategorized (inventory is not an expense until sold) |
| Payment, BillPayment | not imported: they settle invoices and bills already counted |

The Profit and Loss report (accrual) for the same period is downloaded and compared with the
ledger's revenue and total costs (1% tolerance). Differences are shown, not hidden; common causes
are journal entries, deposits and inventory items, which are not imported yet.

Behaviour: tokens refresh shortly before expiry and once more on HTTP 401; Intuit rotates refresh
tokens, so the newest is always saved; queries page with `STARTPOSITION`/`MAXRESULTS` (1000 per
page); HTTP 429 waits (Retry-After, else 60 s) and retries; 5xx backs off; error messages never
contain tokens or the client secret.

### Setup (about 15 minutes)

1. Create a free account at https://developer.intuit.com and create an app with the
   **Accounting** scope (`com.intuit.quickbooks.accounting`). Intuit creates a sandbox company with
   sample transactions.
2. Under *Keys & credentials → Development*, copy the Client ID and Client Secret, and add a
   redirect URI, for example `http://localhost:8501/qbo-callback` (it only needs to match; nothing
   has to listen there).
3. In `.env` (never committed):
   ```
   QBO_CLIENT_ID=...
   QBO_CLIENT_SECRET=...
   QBO_REDIRECT_URI=http://localhost:8501/qbo-callback
   QBO_ENVIRONMENT=sandbox
   ```
4. Connect once from a terminal on the server:
   ```bash
   python -m decisiondesk.ingest.quickbooks connect
   ```
   Open the printed URL, sign in, choose the sandbox company, and paste back the URL you are
   redirected to (the browser may show an error page; the URL is what matters). Tokens are stored
   in `data/private/quickbooks_tokens.json` with owner-only permissions (gitignored).
5. Check and pull:
   ```bash
   python -m decisiondesk.ingest.quickbooks status
   python -m decisiondesk.ingest.quickbooks pull --start 2024-01-01 --end 2026-11-15
   ```
   Or use **Settings → Data → QuickBooks sandbox → Import from QuickBooks** in the app.
6. Optional live test: `QBO_LIVE_TEST=1 pytest tests/test_ingestion.py -k live`.

Refresh tokens expire; Intuit reports the remaining lifetime (`x_refresh_token_expires_in`) and the Data page shows the days left. When it runs out, run `connect` again. Production use
requires Intuit's app review and the steps in `SECURITY.md`.

## Square (sales only)

**Status: implemented and tested with mocked API responses. Not yet run against a live sandbox.**

`ingest/square.py` downloads completed orders (`POST /v2/orders/search`) and completed refunds
(`GET /v2/refunds`) and the location's time zone. Net sale = total − sales tax − tips, dated on the
local business day. Refunds are negative revenue on the refund date, scaled to the same net basis
when their order is in the download.

Square has no costs, so **sales are never treated as profit**. The app shows a sales preview only;
combine with QuickBooks or a transactions file for profit and cash.

Setup: create an application at https://developer.squareup.com, open the **Sandbox** tab, and copy
the sandbox access token and a location id into `.env`:
```
SQUARE_ACCESS_TOKEN=...
SQUARE_LOCATION_ID=...
SQUARE_ENVIRONMENT=sandbox
```
Optional live test: `SQUARE_LIVE_TEST=1 pytest tests/test_ingestion.py -k live`.

## Files

**Daily totals** (one row per day): map columns to `date`, `revenue`, `variable_costs`,
`fixed_costs` (optional `units_sold`, `inventory_on_hand`, purchases, `net_cash_flow`). Gaps in the calendar are
rejected unless declared as closed days.

**Transactions** (bank, accounting or POS export, CSV or Excel, up to 200,000 rows):

1. Choose the date column and either one signed amount column (money in positive) or separate
   money-in / money-out columns. Optional: description, category/account, currency.
2. Vizor lists each category (or payee) with its direction, row count and total, and suggests a
   treatment. Transfers, loans, owner draws and tax payments are suggested as `excluded`. The owner
   confirms or changes every row; anything left `uncategorized` stays out of profit.
3. Errors name the column and the first bad rows (for example, `Column 'Amount' has values that are
   not numbers (file rows 3, 7)`).

Re-uploading an overlapping export does not double count: identical rows get the same id, and
genuinely repeated identical rows in one file are kept as separate entries.

Uploaded files are read into memory for the current browser session. Daily-totals files are
written to a temporary file for validation and deleted immediately after.
