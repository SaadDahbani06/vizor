# Contributing to Vizor

Thanks for helping. Vizor gives small-business owners numbers they may act on, so the bar is
correctness and honesty before features.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,ml,ui,agent,messaging]'
pytest -q && ruff check .
./start.sh offline        # the app, no API keys needed
```

## Ground rules

1. **No invented numbers.** Calculations live in Python and are tested. The language model only
   explains tool results. Do not add a feature where the model computes or guesses a figure.
2. **No fabricated data.** Missing values are skipped and reported, never filled in. Synthetic data
   is labelled synthetic everywhere it appears.
3. **No accuracy claims from synthetic data.** Model changes must be evaluated in date order
   (rolling origin plus untouched holdout, `leaderboard.py`) and reported with the real numbers,
   including when they get worse.
4. **Forecasts are not causal effects.** Scenario effects are assumptions with ranges; say so.
5. **No automatic actions.** Payments, orders, staffing changes, supplier contact and outbound
   messages always need explicit approval from the owner.
6. **No secrets or private data in the repository.** Use `.env` (see `.env.example`). Never commit
   real business files; `data/private/` and `data/cache/` are gitignored. Test fixtures must be
   synthetic. Don't log raw financial records or tokens.
7. **External APIs go through a connector** (`connectors/base.py`, `ingest/`) with mocked HTTP
   tests. Don't claim an integration works until it has run against the real service, and say
   which it was.

## Pull requests

- One topic per pull request, with tests. `pytest -q` and `ruff check .` must pass (CI runs both on
  Python 3.11 and 3.13).
- Describe what changed, how you tested it, and what you did *not* test.
- UI changes: include a screenshot, and keep explanations in hover tooltips rather than on the page.
- Keep the plain, calm writing style of the existing copy.

## Reporting bugs and security issues

Open an issue for bugs. For security problems, follow [SECURITY.md](SECURITY.md) instead of opening
a public issue.
