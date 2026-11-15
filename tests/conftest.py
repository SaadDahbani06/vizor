"""Never let offline/mocked tests reach live paid or quota-limited APIs.

The Streamlit app calls load_dotenv(), which would copy real keys from .env into this test
process. Unless MSRD_LIVE=1 (opt-in live tests), every live credential is blanked; tests that
need a credential set a fake one explicitly. Messaging state files are redirected to a temporary
directory so tests never read or write the real (gitignored) state, which holds phone handles,
and so is the published-scenario file and the poller lock.
"""
import os

import pytest

LIVE_KEYS = ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "PHOTON_API_KEY", "PHOTON_PROJECT_ID",
             "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "PHOTON_WEBHOOK_SECRET")


@pytest.fixture(autouse=True)
def _no_live_credentials(monkeypatch, tmp_path):
    if os.getenv("MSRD_LIVE") == "1":
        yield
        return
    for k in LIVE_KEYS:
        monkeypatch.setenv(k, "")  # set (not delete) so load_dotenv(override=False) keeps it blank
    monkeypatch.setenv("MSRD_LLM_PROVIDER", "")
    monkeypatch.setenv("MSRD_MESSAGING_STATE", str(tmp_path / "messaging_state.json"))
    monkeypatch.setenv("MSRD_MESSAGING_SIM_STATE", str(tmp_path / "messaging_sim.json"))
    monkeypatch.setenv("MSRD_SCENARIO_STATE", str(tmp_path / "published_scenario.json"))
    monkeypatch.setenv("MSRD_POLLER_LOCK", str(tmp_path / "messaging_poller.lock"))
    monkeypatch.setenv("MSRD_DECISION_LOG", str(tmp_path / "decision_log.json"))
    yield
