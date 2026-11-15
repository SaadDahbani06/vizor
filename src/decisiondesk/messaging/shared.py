"""Scenario consistency and single-poller safety for messaging (Milestone 5).

``ScenarioStore``: the scenario iMessage answers use is published EXPLICITLY from the dashboard
("Use this scenario for iMessage") into one JSON file that the dashboard and the standalone
worker both read. Each publish gets a new version; an owner's agent switches to a new version on
their next message. With nothing published, every interface uses the business's DEFAULT
scenario (declared constraints only), never a silent copy of whatever the dashboard shows.

``PollerLock``: at most one process answers messages. The worker and the dashboard's auto-check
both take this lock (a file holding owner, PID, heartbeat and a time-to-live); a second poller is
refused while the holder's process is alive and its heartbeat is younger than its TTL. The worker
uses a 1-hour TTL (a slow Gemini reply must not release the lock mid-answer; a dead process frees it
at once); the dashboard, which shares the Streamlit server's PID across browser sessions, uses
30 s and heartbeats every 5 s. Other processes only read the messaging state. The lock is
advisory and local to this machine.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCENARIO_PATH = Path("data/cache/published_scenario.json")
LOCK_PATH = Path("data/cache/messaging_poller.lock")
LOCK_TTL_S = 30.0


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def scenario_summary(state: dict | None) -> str:
    if not state:
        return "default scenario (no changes)"
    parts = []
    if state.get("food_cost_change_pct"):
        parts.append(f"food costs {state['food_cost_change_pct']:+g}%")
    if state.get("starting_cash") is not None:
        parts.append(f"starting cash ${state['starting_cash']:,.0f}")
    if state.get("elasticity_source") == "user_declared":
        parts.append(f"price elasticity {state.get('elasticity_mode')} "
                     f"({state.get('elasticity_low')} to {state.get('elasticity_high')})")
    ps = state.get("product_scenario") or {}
    if ps:
        from ..decision.product_scenario import ProductScenario
        try:
            text = ProductScenario(**ps).describe()
            if text != "no change (current plan)":
                parts.append(f"Decision Lab: {text} over {ps.get('horizon_days', 28)} days")
        except (TypeError, ValueError):
            pass
    return "; ".join(parts) or "default scenario (no changes)"


class ScenarioStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("MSRD_SCENARIO_STATE") or SCENARIO_PATH)

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def get(self, business_key: str) -> dict | None:
        return self._read().get(business_key)

    def publish(self, business_key: str, state: dict, source: str = "dashboard") -> dict:
        data = self._read()
        prev = data.get(business_key) or {}
        rec = {"version": int(prev.get("version", 0)) + 1, "published_at": _now(),
               "source": source, "state": state, "summary": scenario_summary(state)}
        data[business_key] = rec
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, default=str))
        tmp.replace(self.path)
        return rec

    def clear(self, business_key: str) -> None:
        data = self._read()
        if data.pop(business_key, None) is not None:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, default=str))
            tmp.replace(self.path)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class PollerLock:
    def __init__(self, owner: str, path: str | Path | None = None, ttl_s: float = LOCK_TTL_S):
        self.owner = owner
        self.path = Path(path or os.getenv("MSRD_POLLER_LOCK") or LOCK_PATH)
        self.ttl_s = ttl_s
        self.token = uuid.uuid4().hex

    @contextmanager
    def _guard(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".guard"), "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _read(self) -> dict | None:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def holder(self) -> dict | None:
        """The current holder if its heartbeat is fresh and its process is alive, else None."""
        rec = self._read()
        if not rec:
            return None
        ttl = float(rec.get("ttl_s", self.ttl_s))
        if time.time() - float(rec.get("heartbeat", 0)) > ttl or not _alive(int(rec.get("pid", -1))):
            return None
        return rec

    @property
    def held(self) -> bool:
        rec = self.holder()
        return bool(rec and rec.get("token") == self.token)

    def acquire(self) -> bool:
        with self._guard():
            rec = self.holder()
            if rec and rec.get("token") != self.token:
                return False
            self._write()
            return True

    def heartbeat(self) -> bool:
        with self._guard():
            rec = self._read()
            if not rec or rec.get("token") != self.token:
                rec2 = self.holder()
                if rec2 and rec2.get("token") != self.token:
                    return False  # someone else took over after our heartbeat expired
            self._write()
            return True

    def release(self) -> None:
        with self._guard():
            rec = self._read()
            if rec and rec.get("token") == self.token:
                self.path.unlink(missing_ok=True)

    def _write(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"owner": self.owner, "pid": os.getpid(), "token": self.token,
                                   "heartbeat": time.time(), "since": _now(), "ttl_s": self.ttl_s}))
        tmp.replace(self.path)


def describe_holder(rec: dict | None) -> str:
    if not rec:
        return "nobody is answering messages"
    return f"{rec['owner']} (PID {rec['pid']}, since {rec['since']})"
