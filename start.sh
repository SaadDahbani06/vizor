#!/usr/bin/env bash
# One command to open Main Street Risk Desk in your browser (http://localhost:8501).
# Usage: ./start.sh            (Gemini analyst if GEMINI_API_KEY is set in .env)
#        ./start.sh offline    (instant rule-based analyst, good for demos)
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
if lsof -iTCP:8501 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Something is already running on port 8501 (probably an older dashboard)."
  echo "Stop it with Ctrl-C in its terminal, or: kill \$(lsof -t -iTCP:8501 -sTCP:LISTEN)"
  exit 1
fi
if [ "${1:-}" = "offline" ]; then export MSRD_LLM_PROVIDER=offline; fi
exec streamlit run app/streamlit_app.py --server.port 8501
