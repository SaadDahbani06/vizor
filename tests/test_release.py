"""Milestone 4.5 release tests: leaderboard disk cache identity and the Messaging tab (offline)."""
from pathlib import Path

import numpy as np

from decisiondesk.data import validate_daily_frame
from decisiondesk.leaderboard import build_leaderboard, build_leaderboard_cached
from decisiondesk.synthetic_v2 import load_business

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")
FAST = ("seasonal_naive", "weekday_mean_12w", "ridge")


def test_cached_leaderboard_is_identical_to_a_fresh_fit(tmp_path):
    hist, _ = validate_daily_frame(load_business("restaurant").daily, source="t")
    kw = {"dataset": "t", "data_label": "SYNTHETIC", "models": FAST}
    fresh = build_leaderboard(hist, "revenue", **kw)
    first = build_leaderboard_cached(hist, "revenue", cache_dir=tmp_path, **kw)
    second = build_leaderboard_cached(hist, "revenue", cache_dir=tmp_path, **kw)
    assert first.meta["cache"]["hit"] is False and second.meta["cache"]["hit"] is True
    assert second.selected == fresh.selected
    timing = ["fit_seconds"]  # wall-clock timing is the only field allowed to differ
    assert second.table().drop(columns=timing, errors="ignore").equals(
        fresh.table().drop(columns=timing, errors="ignore"))
    np.testing.assert_array_equal(second.val_residuals, fresh.val_residuals)
    changed = hist.copy()
    changed.loc[changed.index[-1], "revenue"] += 1.0  # any data change -> new key, refit
    third = build_leaderboard_cached(changed, "revenue", cache_dir=tmp_path, **kw)
    assert third.meta["cache"]["hit"] is False and third.meta["cache"]["key"] != first.meta["cache"]["key"]


def test_messaging_tab_simulator_round_trip(monkeypatch):
    monkeypatch.setenv("MSRD_FAST", "1")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception
    at.radio(key="nav").set_value("Messaging").run()
    assert not at.exception
    assert any("PHOTON_API_KEY is not set" in e.value for e in at.error)  # live mode, no key
    at.radio(key="msg_mode").set_value("Local simulator (no real messages)").run()
    assert not at.exception
    md = " ".join(m.value for m in at.markdown)
    assert "LOCAL SIMULATOR (no real messages)" in md
    assert "Offline analyst" in md  # active analyst shown (no Gemini key in tests)
    at.text_input(key="sim_msg_svc_sim").set_value("What should I do?")
    at.button(key="simgo_msg_svc_sim").click().run()
    assert not at.exception
    chat = [m.markdown[0].value for m in at.chat_message if m.markdown]
    assert any(c.startswith("What should I do?") for c in chat)
    assert any("Recommendation" in c and "reply:offline" in c for c in chat)
    table = at.dataframe[-1].value
    assert bool(table["simulated"].iloc[0]) and table["kind"].iloc[0] == "reply:offline"
