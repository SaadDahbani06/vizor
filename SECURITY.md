# Security

Vizor handles business financial records and API credentials. It is a hackathon project and has
**not** had a security review. Read this page before connecting real business data.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting
(*Security → Report a vulnerability*) on this repository. We aim to reply within a week.

## Secrets

- All credentials are read from environment variables or a local `.env` file (template:
  `.env.example`). `.env`, `.streamlit/secrets.toml`, `data/private/` and `data/cache/` are
  gitignored.
- QuickBooks OAuth tokens are stored server-side in `data/private/quickbooks_tokens.json` with
  owner-only file permissions (0600). They are never sent to the browser, the language model or
  logs; the UI shows only the environment, the last four digits of the company id and the days left
  on the refresh token.
- Connector errors never include tokens or client secrets (tested in `tests/test_ingestion.py`).
- Phone numbers are masked in the UI and logs.

### Repository scan (Milestone 7, 2026-11-15)

Every tracked file and every commit in the history was scanned for API keys, tokens, private keys,
passwords, connection strings, phone numbers, email addresses and real financial records.

- **No credentials found** in the working tree or the history. `.env` has never been committed.
  `data/private/` and `data/cache/` have never been committed.
- **Tracked data** is synthetic demo data (`data/examples/*_SYNTHETIC.csv`) plus dated public menu
  prices (`data/market/austin_menu_prices.csv`). No real business records.
- **Finding: personal email address in commit metadata.** Every commit's author field contains a
  personal email address. It is not in any file, but it becomes public when the history is pushed.
  Before publishing, either:
  1. set a GitHub no-reply address (`git config user.email <id>+<user>@users.noreply.github.com`)
     and rewrite the existing authors, e.g. `git filter-repo --mailmap mailmap.txt` on a fresh
     clone; or
  2. publish a fresh repository with a single squashed initial commit made with the no-reply
     address.

### If a credential is ever exposed

Revoke first, then replace, then check for misuse. Removing it from git history is not enough,
because clones and caches keep it.

| Credential | Rotate at |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio → API keys → delete, create a new key |
| `PHOTON_API_KEY`, `PHOTON_WEBHOOK_SECRET` | Photon dashboard → project → API keys / webhooks |
| `QBO_CLIENT_SECRET` | Intuit developer portal → app → Keys & credentials → regenerate; delete `data/private/quickbooks_tokens.json` and reconnect |
| QuickBooks tokens | revoke at `https://developer.api.intuit.com/v2/oauth2/tokens/revoke` or disconnect the app in QuickBooks, then delete the token file |
| `SQUARE_ACCESS_TOKEN` | Square developer dashboard → application → credentials → replace token |
| `NESSIE_API_KEY`, `FRED_API_KEY`, `ANTHROPIC_API_KEY` | the provider's account page |

## How data is handled

| Data | Where it lives | Shared between browser sessions? |
|---|---|---|
| Uploaded files | read into memory for that browser session; the daily-totals file is written to a temporary file for validation and deleted immediately | no |
| Imported ledger and daily totals | memory of that browser session | no |
| Fitted forecast models for uploaded or connected data | memory of that browser session (only the synthetic demo is cached on disk) | no |
| QuickBooks connection (tokens, company) | `data/private/quickbooks_tokens.json`, one per deployment | **yes**: anyone who can open the app can import from the connected company |
| Messaging state (phone handles, opt-ins, preferences) | `data/cache/messaging_state.json`, one per deployment | **yes** |
| Questions to Vizor AI | sent to Google Gemini with aggregated tool results (totals, forecasts, ranges), not raw transaction rows | n/a |

Streamlit keeps `st.session_state` per browser session, so one visitor's uploads are not visible to
another. The app does not use `st.cache_data`/`st.cache_resource` for business data. But a
deployment is **single-tenant**: there are no user accounts, and the QuickBooks connection and the
messaging store belong to the whole deployment.

## Before handling real customer data

Vizor is not ready for this yet. At minimum:

1. **Authentication and tenancy.** Put the app behind sign-in (for example an identity-aware
   proxy), and either run one deployment per business or add per-tenant storage for tokens,
   messaging state and imports.
2. **Encrypt secrets at rest.** Move QuickBooks tokens and messaging state to a secrets manager or
   an encrypted database; restrict file-system access on the host.
3. **HTTPS only**, with the Streamlit server not exposed directly.
4. **Language-model terms.** Use a paid Gemini tier whose terms exclude training on your data, or
   the offline analyst. Keep sending only aggregated tool results, never raw records.
5. **Intuit and Square production approval.** Both require app review, privacy policy and terms of
   service URLs, and a disconnect flow before production keys are issued.
6. **Logging and retention.** Confirm logs contain no records or tokens; define how long imported
   data and messages are kept and how a customer deletes them (and revoke tokens on disconnect).
7. **Live integration tests** against sandbox companies, then a supervised pilot where the owner
   checks imported totals against their own books (the reconciliation table) before any decision.
8. **Dependency and secret scanning** in CI (for example Dependabot and a secret scanner), and a
   security review of the OAuth flow and file upload handling.
9. **Legal review** of disclaimers: Vizor's outputs are estimates, not financial, tax or legal
   advice.
