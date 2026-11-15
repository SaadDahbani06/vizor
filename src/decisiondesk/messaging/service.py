"""Two-way messaging service: Photon (or the labelled local simulator) <-> the decision agent.

Rules enforced here, before any send:
  * Only AUTHORIZED owners: handles from the project's iMessage assignments with opt-in "received"
    (or the simulator's demo owner). Unknown senders get no reply.
  * Replies go only into the conversation the owner started (shared lines cannot initiate).
  * Proactive alerts need explicit alert consent (dashboard toggle or texting START), a known
    conversation, a matching alert preference, no prior send of the same alert, and room under the
    daily send cap. STOP/UNSUBSCRIBE ends all messages except one confirmation; START resumes.
  * Every inbound message is processed once (message-ID dedupe that survives restarts); each
    reply uses a stable Idempotency-Key derived from the inbound message ID.
  * Each owner has an isolated DecisionAgent (conversation memory + scenario state) and session.
  * Alert texts must pass the numeric grounding check against the tool outputs that produced them.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..agent.agent import DecisionAgent
from ..agent.grounding import ungrounded_numbers
from .gateway import PhotonUnavailable, Recipient, fit_text, mask

STATE_PATH = Path("data/cache/messaging_state.json")
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "OPT-OUT"}
START_WORDS = {"START", "UNSTOP", "YES", "SUBSCRIBE"}
ALERT_KINDS = ("cash_shortfall", "low_runway", "cost_increase", "economic_release",
               "expense_increase", "revenue_decline", "inventory_constraint", "daily_briefing")
FINAL = {"delivered", "failed"}
# Business alerts on by default once the owner opts in. Not on by default: national economic
# releases (not part of the customer product) and the daily briefing (its own explicit opt-in).
DEFAULT_ALERT_KINDS = tuple(k for k in ALERT_KINDS if k not in ("economic_release", "daily_briefing"))


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class Owner:
    handle: str
    business_key: str = "restaurant"
    authorized: bool = False
    alerts_opt_in: bool = False
    stopped: bool = False
    consent_at: str | None = None
    consent_source: str | None = None
    alert_prefs: list[str] = field(default_factory=lambda: list(DEFAULT_ALERT_KINDS))
    space_id: str | None = None
    agent_user_id: str | None = None
    last_inbound_at: str | None = None
    line: str | None = None
    platform: str = "imessage"
    notify_level: str = "decision_needed"   # or "important" (also texts important alerts)
    last_alert: dict | None = None          # for DETAILS / OPTIONS replies; persisted

    @property
    def masked(self) -> str:
        return mask(self.handle)


class MessagingStore:
    """JSON state that survives restarts (gitignored). Writes are atomic and lock-protected."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("MSRD_MESSAGING_STATE") or STATE_PATH)
        self.lock = threading.RLock()
        self.reload()

    def reload(self) -> None:
        """Re-read the state file (used by read-only viewers while another process polls)."""
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        known = set(Owner.__dataclass_fields__)
        self.owners = {h: Owner(**{k: v for k, v in o.items() if k in known})
                       for h, o in data.get("owners", {}).items()}
        for o in self.owners.values():  # Milestone 5.4 removed national-indicator alerts
            o.alert_prefs = [k for k in o.alert_prefs if k != "economic_release"]
        self.processed: list[str] = data.get("processed", [])
        self.sent_alert_keys: list[str] = data.get("sent_alert_keys", [])
        self.outbound: list[dict] = data.get("outbound", [])
        self.conversations: dict[str, list[dict]] = data.get("conversations", {})
        self.usage: dict[str, int] = data.get("usage", {})
        self.cursor: str | None = data.get("cursor")
        self.events: list[dict] = data.get("events", [])

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "owners": {h: asdict(o) for h, o in self.owners.items()},
                "processed": self.processed[-2000:], "sent_alert_keys": self.sent_alert_keys[-2000:],
                "outbound": self.outbound[-500:],
                "conversations": {h: c[-200:] for h, c in self.conversations.items()},
                "usage": self.usage, "cursor": self.cursor, "events": self.events[-300:]},
                indent=1, default=str))
            tmp.replace(self.path)

    def owner(self, handle: str) -> Owner:
        if handle not in self.owners:
            self.owners[handle] = Owner(handle)
        return self.owners[handle]

    def log(self, handle: str, direction: str, text: str, **extra) -> dict:
        entry = {"at": _iso(), "direction": direction, "text": text, **extra}
        self.conversations.setdefault(handle, []).append(entry)
        return entry

    def event(self, kind: str, detail: str) -> None:
        self.events.append({"at": _iso(), "kind": kind, "detail": detail})

    def sends_today(self) -> int:
        return self.usage.get(_now().date().isoformat(), 0)

    def count_send(self) -> None:
        day = _now().date().isoformat()
        self.usage[day] = self.usage.get(day, 0) + 1


class SimulatorGateway:
    """LOCAL SIMULATOR — no network, no real phone. Same interface as PhotonGateway.

    Inbound messages are injected with ``simulate_inbound``; outbound messages are stored and
    marked ``delivered (simulated)``. Every record is labelled SIMULATED."""

    simulated = True
    DEMO_HANDLE = "+15550100000"  # reserved fictional 555-01xx range

    def __init__(self):
        self.inbox: list[dict] = []
        self.sent: list[dict] = []
        self.space_id = "sim_space_demo"
        self.agent_id = "sim_agent_line"
        self.human_id = "sim_owner"

    configured = True

    def config_problems(self) -> list[str]:
        return []

    def status(self) -> dict:
        return {"configured": True, "connected": True, "simulated": True, "problems": [],
                "imessage": {"mode": "LOCAL SIMULATOR"}, "sms_lines": [],
                "recipients": [{"masked": mask(self.DEMO_HANDLE), "line": "simulator",
                                "opt_in": "received", "opt_in_at": None}]}

    def recipients(self) -> list[Recipient]:
        return [Recipient(self.DEMO_HANDLE, "simulator", "received", None)]

    def simulate_inbound(self, text: str, handle: str | None = None) -> dict:
        msg = {"id": f"sim_msg_{uuid.uuid4().hex[:12]}", "createdAt": _iso(), "status": "received",
               "platform": "imessage", "content": {"type": "text", "format": "plain", "text": text},
               "sentBy": {"kind": "human", "handle": handle or self.DEMO_HANDLE, "id": self.human_id},
               "receivedBy": {"kind": "agent", "handle": "simulator", "id": self.agent_id},
               "space": {"id": self.space_id, "kind": "direct"}}
        self.inbox.append(msg)
        return msg

    def list_received(self, created_since, platform=None, page_size=50) -> list[dict]:
        return [m for m in self.inbox if not created_since or m["createdAt"] >= created_since]

    def send_text(self, *, space_id, sender_id, text, idempotency_key, parent_message_id=None):
        for s in self.sent:
            if s["idempotency_key"] == idempotency_key:
                return s["response"]
        resp = {"id": f"sim_out_{uuid.uuid4().hex[:12]}", "status": "delivered",
                "timestamps": {"deliveredAt": _iso()}, "simulated": True}
        self.sent.append({"idempotency_key": idempotency_key, "text": fit_text(text),
                          "space_id": space_id, "response": resp})
        return resp

    def get_message(self, message_id: str) -> dict:
        return next((s["response"] for s in self.sent if s["response"]["id"] == message_id),
                    {"id": message_id, "status": "failed"})


class MessagingService:
    def __init__(self, gateway, store: MessagingStore,
                 session_provider: Callable[[str], object], *, agent_mode: str | None = None,
                 daily_limit: int | None = None, agent_client=None, scenarios=None):
        from .shared import ScenarioStore
        self.scenarios = scenarios or ScenarioStore()
        self.gateway = gateway
        self.store = store
        self.session_provider = session_provider
        self.agent_mode = agent_mode
        self.agent_client = agent_client
        self.daily_limit = daily_limit or int(os.getenv("MSRD_MAX_MESSAGES_PER_DAY", "40"))
        self.agents: dict[str, DecisionAgent] = {}
        self.simulated = bool(getattr(gateway, "simulated", False))

    @property
    def channel_label(self) -> str:
        return "LOCAL SIMULATOR (no real messages)" if self.simulated else "Photon 2.0 iMessage (live)"

    # ---------------------------------------------------------------- recipients
    def sync_recipients(self) -> list[Owner]:
        """Authorize exactly the handles whose assignment opt-in is 'received'."""
        allowed = {r.handle: r for r in self.gateway.recipients() if r.opt_in_state == "received"}
        with self.store.lock:
            for h, o in self.store.owners.items():
                o.authorized = h in allowed
            for h, r in allowed.items():
                o = self.store.owner(h)
                o.authorized, o.line = True, r.assigned_line
            self.store.save()
        self._discover_conversations(set(allowed))
        return [self.store.owners[h] for h in allowed]

    def _discover_conversations(self, handles: set[str]) -> None:
        """Learn each owner's existing conversation (space + agent user) from history, read-only.
        Historical messages are marked processed so they are never answered retroactively."""
        if not handles or self.store.cursor is not None:
            return
        try:
            history = self.gateway.list_received(None)
        except PhotonUnavailable:
            return
        with self.store.lock:
            for m in sorted(history, key=lambda x: x.get("createdAt", "")):
                h = (m.get("sentBy") or {}).get("handle")
                if h in handles and (m.get("receivedBy") or {}).get("kind") == "agent":
                    o = self.store.owner(h)
                    o.space_id = (m.get("space") or {}).get("id") or o.space_id
                    o.agent_user_id = (m.get("receivedBy") or {}).get("id") or o.agent_user_id
                    o.last_inbound_at = m.get("createdAt") or o.last_inbound_at
                if m.get("id") and m["id"] not in self.store.processed:
                    self.store.processed.append(m["id"])
            self.store.cursor = _iso(_now() - timedelta(seconds=5))
            self.store.save()

    def scenario_source(self, business_key: str) -> dict:
        """What scenario iMessage answers use for this business (shown in the dashboard)."""
        from .shared import scenario_summary
        rec = self.scenarios.get(business_key)
        if rec:
            return {"version": rec["version"], "published_at": rec["published_at"],
                    "summary": rec["summary"], "source": rec.get("source", "dashboard")}
        return {"version": 0, "published_at": None, "summary": scenario_summary(None),
                "source": "default"}

    def agent_for(self, owner: Owner) -> DecisionAgent:
        """One isolated agent per owner and business: its own conversation memory, scenario
        state and tool log. Fitted leaderboards/forecasts (pure functions of the data) are shared
        with the base session so a second owner does not refit the models.

        Scenario source (identical in the dashboard and the worker): the scenario PUBLISHED from
        the dashboard for this business, or the business's default scenario. A newly published
        version replaces the owner's scenario at their next message; changes the owner makes by
        text persist until then."""
        key = f"{owner.handle}|{owner.business_key}"
        rec = self.scenarios.get(owner.business_key)
        version = rec["version"] if rec else 0
        if key not in self.agents:
            base = self.session_provider(owner.business_key)
            session = replace(base, state=base.default_state(), log=[])
            self.agents[key] = DecisionAgent(session, mode=self.agent_mode, channel="imessage",
                                             client=self.agent_client)
            self.agents[key].scenario_version = 0
        agent = self.agents[key]
        if version != getattr(agent, "scenario_version", 0):
            state = agent.session.default_state()
            for k, v in ((rec or {}).get("state") or {}).items():
                if hasattr(state, k):
                    setattr(state, k, deepcopy(v))
            agent.session.state = state
            agent.scenario_version = version
        return agent

    def set_alert_consent(self, handle: str, opted_in: bool, source: str = "dashboard") -> Owner:
        with self.store.lock:
            o = self.store.owner(handle)
            o.alerts_opt_in = opted_in
            if opted_in:
                o.stopped, o.consent_at, o.consent_source = False, _iso(), source
            self.store.event("consent", f"{o.masked} alerts {'on' if opted_in else 'off'} via {source}")
            self.store.save()
        return o

    # ---------------------------------------------------------------- sending
    def _send(self, owner: Owner, text: str, *, key: str, kind: str, parent: str | None = None,
              evidence: dict | None = None) -> dict:
        if not owner.authorized:
            raise PermissionError(f"{owner.masked} is not an authorized recipient")
        if not owner.space_id or not owner.agent_user_id:
            raise PermissionError(f"{owner.masked} has not messaged the agent line yet; a shared "
                                  "iMessage line can only reply in a conversation the owner started")
        if self.store.sends_today() >= self.daily_limit:
            raise PermissionError(f"daily send cap reached ({self.daily_limit})")
        text = fit_text(text)
        resp = self.gateway.send_text(space_id=owner.space_id, sender_id=owner.agent_user_id,
                                      text=text, idempotency_key=key, parent_message_id=parent)
        record = {"message_id": resp.get("id"), "kind": kind, "to": owner.masked,
                  "status": resp.get("status", "submitted"), "created_at": _iso(),
                  "delivered_at": (resp.get("timestamps") or {}).get("deliveredAt"),
                  "acceptance": (resp.get("acceptance") or {}).get("code"),
                  "simulated": self.simulated, "idempotency_key": key, "text": text,
                  "evidence": evidence or {}}
        with self.store.lock:
            self.store.count_send()
            self.store.outbound.append(record)
            self.store.log(owner.handle, "out", text, message_id=record["message_id"], kind=kind,
                           status=record["status"])
            self.store.save()
        return record

    def send_test(self, handle: str) -> dict:
        o = self.store.owner(handle)
        text = ("Vizor test message. Reply OPTIONS for recommendations, HELP for "
                "commands, STOP to unsubscribe.")
        return self._send(o, text, key=f"msrd-test:{uuid.uuid4().hex[:16]}", kind="test")

    def send_briefing(self, handle: str, briefing: dict, *, scheduled: bool = False,
                      confirmed: bool = False) -> dict | None:
        """Send the short daily briefing to one owner.

        On demand (the owner clicks "Send to my phone"): requires ``confirmed=True``.
        Scheduled (worker ``briefing --send``, run by cron): requires the alert opt-in AND the
        "daily_briefing" preference, and sends at most once per owner per briefing date.
        STOP always wins. Numbers must pass the grounding check against the briefing data."""
        from ..briefing import briefing_sms
        o = self.store.owner(handle)
        if o.stopped:
            self.store.event("briefing_skipped", f"{o.masked} sent STOP")
            return None
        if scheduled:
            if not o.alerts_opt_in or "daily_briefing" not in o.alert_prefs:
                self.store.event("briefing_skipped", f"{o.masked} has not opted in to daily briefings")
                return None
        elif not confirmed:
            raise PermissionError("sending the briefing needs the owner's confirmation")
        dedup = f"briefing:{o.handle}:{briefing['date']}"
        if scheduled and dedup in self.store.sent_alert_keys:
            return None
        text = briefing_sms(briefing)
        bad = ungrounded_numbers(text, [{k: v for k, v in briefing.items() if k != "text"}], [])
        if bad:
            self.store.event("briefing_blocked", f"ungrounded numbers {bad}")
            self.store.save()
            return None
        key = (f"msrd-brief:{uuid.uuid5(uuid.NAMESPACE_URL, dedup).hex[:16]}" if scheduled
               else f"msrd-brief:{uuid.uuid4().hex[:16]}")
        rec = self._send(o, text, key=key, kind="briefing",
                         evidence={"briefing_date": briefing["date"], "scheduled": scheduled})
        if scheduled:
            with self.store.lock:
                self.store.sent_alert_keys.append(dedup)
                self.store.save()
        return rec

    def send_attention(self, handle: str, briefing: dict, *, max_messages: int = 2) -> list[dict]:
        """Text the owner about briefing alerts that need attention (never routine ones).

        decision_needed alerts go to owners who opted in to alerts; important alerts only if the
        owner chose the "important" level. At most ``max_messages`` per run, once per alert per
        day, never after STOP, numbers checked against the briefing. Replies: DETAILS, OPTIONS,
        STOP. Nothing is ordered or changed automatically."""
        o = self.store.owner(handle)
        if o.stopped or not o.alerts_opt_in:
            return []
        allowed = {"decision_needed"} | ({"important"} if o.notify_level == "important" else set())
        data = [{k: v for k, v in briefing.items() if k != "text"}]
        sent = []
        for a in briefing.get("alerts", []):
            if len(sent) >= max_messages:
                break
            if a.get("level") not in allowed:
                continue
            dedup = f"attention:{o.handle}:{briefing['date']}:{a['kind']}:{a.get('product') or ''}"
            if dedup in self.store.sent_alert_keys:
                continue
            head = "Decision needed" if a["level"] == "decision_needed" else "Heads up"
            text = (f"{head}: {a['title']}. {a['detail']} Reply DETAILS for the numbers, OPTIONS for what you "
                    "could do, or STOP. Nothing is ordered or changed automatically.")
            bad = ungrounded_numbers(text, data, [])
            if bad:
                self.store.event("alert_blocked", f"{a['kind']}: ungrounded numbers {bad}")
                continue
            rec = self._send(o, text, key=f"msrd-attn:{uuid.uuid5(uuid.NAMESPACE_URL, dedup).hex[:16]}",
                             kind=f"attention:{a['level']}", evidence={"briefing_date": briefing["date"],
                                                                        "alert": a["kind"]})
            with self.store.lock:
                self.store.sent_alert_keys.append(dedup)
                o.last_alert = {k: a.get(k) for k in ("kind", "level", "title", "detail", "next_step", "product")} | {
                    "date": briefing["date"]}
                self.store.save()
            sent.append(rec)
        return sent

    def send_alert(self, handle: str, signal) -> dict | None:
        """Send one risk signal if consent, preference, dedupe and grounding all allow it."""
        o = self.store.owner(handle)
        if not o.alerts_opt_in or o.stopped:
            self.store.event("alert_skipped", f"{signal.kind}: no alert consent for {o.masked}")
            return None
        if signal.kind not in o.alert_prefs:
            return None
        if signal.dedup_key in self.store.sent_alert_keys:
            return None
        bad = ungrounded_numbers(signal.text, signal.tool_outputs, [])
        if bad:
            self.store.event("alert_blocked", f"{signal.kind}: ungrounded numbers {bad}")
            self.store.save()
            return None
        rec = self._send(o, signal.text, key=f"msrd-alert:{signal.dedup_key}", kind=f"alert:{signal.kind}",
                         evidence={"run_ids": signal.run_ids, "kind": signal.kind})
        with self.store.lock:
            self.store.sent_alert_keys.append(signal.dedup_key)
            self.store.save()
        return rec

    # ---------------------------------------------------------------- inbound
    def handle_inbound(self, msg: dict) -> dict | None:
        mid = msg.get("id")
        with self.store.lock:
            if not mid or mid in self.store.processed:
                return None
            self.store.processed.append(mid)
            self.store.save()
        sender, agent_user = msg.get("sentBy") or {}, msg.get("receivedBy") or {}
        if msg.get("status") != "received" or sender.get("kind") != "human" \
                or agent_user.get("kind") != "agent":
            return None
        handle = sender.get("handle", "")
        o = self.store.owners.get(handle)
        if o is None or not o.authorized:
            self.store.event("ignored", f"message from unauthorized sender {mask(handle)}")
            self.store.save()
            return None
        content = msg.get("content") or {}
        text = (content.get("text") or "").strip() if content.get("type") == "text" else ""
        with self.store.lock:
            o.space_id = (msg.get("space") or {}).get("id") or o.space_id
            o.agent_user_id = agent_user.get("id") or o.agent_user_id
            o.last_inbound_at = msg.get("createdAt")
            o.platform = msg.get("platform", o.platform)
            self.store.log(handle, "in", text or f"[{content.get('type', 'non-text')} message]",
                           message_id=mid)
            self.store.save()
        if not text:
            return None
        word = text.upper().strip(" .!")
        key = f"msrd-reply:{mid}"
        if word in STOP_WORDS:
            with self.store.lock:  # persist the opt-out before (and regardless of) the confirmation
                o.stopped, o.alerts_opt_in = True, False
                self.store.event("consent", f"{o.masked} sent STOP; all messages stopped")
                self.store.save()
            return self._send(o, "You're unsubscribed from Vizor. No more messages "
                                 "will be sent. Reply START to resubscribe.", key=key, kind="stop", parent=mid)
        if o.stopped and word not in START_WORDS:
            return None  # respect STOP: no replies until START
        if word in START_WORDS:
            self.set_alert_consent(handle, True, "message:START")
            return self._send(o, "Subscribed to Vizor risk alerts for your "
                                 "business. Reply STOP to unsubscribe, HELP for commands.",
                              key=key, kind="start", parent=mid)
        if word == "HELP":
            return self._send(o, "Vizor: ask about sales, cash, costs, prices or a decision "
                                 "(for example 'What if taco prices rise 5%?'). DETAILS = numbers behind the last "
                                 "alert. OPTIONS = what you could do. STOP = unsubscribe. Figures are estimates, "
                                 "not guarantees.", key=key, kind="help", parent=mid)
        if word in ("DETAILS", "DETAIL"):
            last = o.last_alert
            if last:
                body = (f"{last['title']} ({last['date']}). {last['detail']} Next step: {last['next_step']} "
                        "Reply OPTIONS to compare what you could do.")
            else:
                from ..briefing import briefing_sms, daily_briefing
                body = briefing_sms(daily_briefing(self.agent_for(o).session))
            return self._send(o, body, key=key, kind="details", parent=mid)
        question = text
        if word in ("OPTIONS", "OPTION"):
            last = o.last_alert or {}
            question = ("Should I buy more " + last["product"].lower() + " ingredients?"
                        if last.get("kind") in ("low_stock", "stockout_risk") and last.get("product") else
                        "Will I run out of cash?" if last.get("kind") == "cash" else
                        "What should I do? Compare the available actions to reduce the risk.")
        agent = self.agent_for(o)
        reply = agent.ask(question)
        footer = "" if reply.grounded else " (some figures could not be verified)"
        clean = re.sub(r"^Offline analyst(?: \(rule-based, no language model\))?\.\s*", "", reply.text)
        body = f"{clean.replace('**', '')}{footer}"
        src = self.scenario_source(o.business_key)
        return self._send(o, body, key=key, kind=f"reply:{reply.mode}", parent=mid,
                          evidence={"run_ids": [c["output"]["run_id"] for c in reply.tool_calls
                                                if "output" in c], "analyst": reply.mode,
                                    "grounded": reply.grounded,
                                    "tools": [c["name"] for c in reply.tool_calls],
                                    "model": getattr(getattr(agent, "gemini", None), "model", None)
                                    if reply.mode == "gemini" else None,
                                    "scenario_version": src["version"],
                                    "scenario": src["summary"]})

    def poll_once(self) -> list[dict]:
        """Fetch new inbound messages (bounded) and answer them; returns sent records."""
        since = self.store.cursor
        if since is None:  # first run: only messages from now on (never answer old history)
            self.store.cursor = _iso(_now() - timedelta(seconds=5))
            self.store.save()
            since = self.store.cursor
        msgs = self.gateway.list_received(since)
        sent = []
        newest = since
        for m in sorted(msgs, key=lambda x: x.get("createdAt", "")):
            try:
                rec = self.handle_inbound(m)
            except (PermissionError, PhotonUnavailable) as exc:
                self.store.event("send_error", str(exc))
                rec = None
            if rec:
                sent.append(rec)
            newest = max(newest, m.get("createdAt", newest))
        # Keep a small overlap for eventual consistency; dedupe makes re-reads harmless.
        with self.store.lock:
            self.store.cursor = newest
            self.store.save()
        return sent

    def refresh_statuses(self, max_checks: int = 10) -> int:
        """Re-read recent non-final outbound messages; delivery is reported only when confirmed."""
        n = 0
        for rec in reversed(self.store.outbound):
            if n >= max_checks:
                break
            if rec.get("status") in FINAL or rec.get("acceptance") or not rec.get("message_id") \
                    or rec.get("simulated"):
                continue
            msg = self.gateway.get_message(rec["message_id"])
            ts = msg.get("timestamps") or {}
            rec["status"] = msg.get("status", rec["status"])
            rec["delivered_at"] = ts.get("deliveredAt") or rec.get("delivered_at")
            if msg.get("status") == "failed":
                rec["failure"] = (msg.get("failure") or {}).get("code") or "failed"
            if msg.get("status") == "accepted" and msg.get("acceptance"):
                # Final "accepted": the platform took the message but delivery will not be
                # confirmed (e.g. delivery_confirmation_unavailable / _timed_out). Never "delivered".
                rec["acceptance"] = msg["acceptance"].get("code") or "accepted"
            for convo in self.store.conversations.values():  # keep the chat view in step
                for entry in convo:
                    if entry.get("message_id") == rec["message_id"]:
                        entry["status"] = rec["status"]
            n += 1
        self.store.save()
        return n
