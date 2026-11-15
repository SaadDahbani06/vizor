"""Photon messaging tests (MOCKED: no network, no real phone, no credentials).

``FakePhoton`` mirrors the official ``photonhq-api`` client surface. Its methods are annotated
with the client's real generated input models (``photon_api.rpc_generated``), so every request
our gateway builds (path aliases, query, Idempotency-Key header, send body) is validated by the
official schemas, and every fake inbound message validates against the official
``PlatformMessage`` model. Handles use the reserved fictional 555-01xx range.
"""
import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import photon_api.rpc_generated as rg
import pytest
from photon_api import ApiError
from photon_api.generated import models as pm
from standardwebhooks.webhooks import Webhook
from test_gemini import EVAL, call, fake_client, profit_text

from decisiondesk.agent.tools import BusinessSession
from decisiondesk.messaging import webhooks
from decisiondesk.messaging.gateway import (
    MAX_TEXT,
    PhotonGateway,
    PhotonUnavailable,
    fit_text,
    mask,
)
from decisiondesk.messaging.risk_signals import RiskSignal, scan
from decisiondesk.messaging.service import MessagingService, MessagingStore, SimulatorGateway

FAST = ("seasonal_naive", "weekday_mean_12w", "historical_mean_28d", "ridge")
KEY, PROJECT = "pho_sk_unit_test_secret_000000000000", "pho_prj_unit_test_0001"
OWNER, OTHER, STRANGER, LINE = "+15550100001", "+15550100002", "+15550100003", "+15550109999"
UIDS = {OWNER: "usr_owner", OTHER: "usr_other", STRANGER: "usr_stranger"}


def iso(dt=None):
    return (dt or datetime.now(UTC)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def user(kind, handle, uid):
    return {"kind": kind, "handle": handle, "id": uid, "platform": "imessage",
            "projectId": PROJECT, "details": {}, "metadata": {}}


def space(handle):
    return {"id": f"spc_{UIDS[handle]}", "kind": "direct", "platform": "imessage",
            "projectId": PROJECT, "details": {}, "metadata": {},
            "participantIds": [UIDS[handle], "usr_agent"]}


def inbound(mid, text, handle=OWNER, at=None):
    at = at or iso()
    msg = {"id": mid, "projectId": PROJECT, "createdAt": at, "updatedAt": at, "status": "received",
           "metadata": {}, "timestamps": {"receivedAt": at}, "platform": "imessage",
           "content": {"type": "text", "format": "plain", "text": text},
           "sentBy": user("human", handle, UIDS[handle]),
           "receivedBy": user("agent", LINE, "usr_agent"), "space": space(handle)}
    pm.PlatformMessage.model_validate(msg)  # the fake speaks the official schema
    return msg


class FakePhoton:
    def __init__(self, assignments=None):
        self.assignments = assignments if assignments is not None else [
            {"handle": OWNER, "assignedPhoneNumber": LINE, "optIn": {"state": "received",
                                                                     "receivedAt": iso()}},
            {"handle": OTHER, "assignedPhoneNumber": LINE, "optIn": {"state": "pending"}}]
        self.inbox, self.sent, self.requests = [], {}, []
        self.by_key: dict[str, str] = {}
        self.error: Exception | None = None
        api = self

        class Assignments:
            def list(self, input: rg.ListSharedLineAssignmentsInput):
                return api._rec("assignments.list", input, {"assignments": api.assignments})

        class Platforms:
            imessage = SimpleNamespace(assignments=Assignments())

            def get_imessage(self, input: rg.GetProjectImessagePlatformInput):
                return api._rec("get_imessage", input, {"mode": "shared", "maxUserAssignments": 25})

            def list_resources(self, input: rg.ListResourcesInput):
                return api._rec("list_resources", input, {"resources": []})

        class Projects:
            platforms = Platforms()

            def get(self, input: rg.GetProjectInput):
                return api._rec("get", input, {"projectId": PROJECT, "name": "unit_test_project"})

            def list_messages(self, input: rg.ListMessagesInput):
                q = (api._rec("list_messages", input, None).get("query") or {})
                since = q.get("createdSince")
                items = [m for m in api.inbox if not since or m["createdAt"] >= since]
                return {"items": items, "nextPageToken": None}

            def get_message(self, input: rg.GetMessageInput):
                mid = api._rec("get_message", input, None)["path"]["messageId"]
                if mid in api.sent:
                    return api.sent[mid]
                return next(m for m in api.inbox if m["id"] == mid)

            def send_message(self, input: rg.SendMessageInput):
                req = api._rec("send_message", input, None)
                key = req["headers"]["idempotency-key"]
                if key in api.by_key:  # Photon replays the original response for a reused key
                    return api.sent[api.by_key[key]]
                mid = f"msg_out_{len(api.sent) + 1}"
                api.by_key[key] = mid
                api.sent[mid] = {"id": mid, "status": "dispatched", "body": req["body"],
                                 "timestamps": {"dispatchedAt": iso()}}
                return api.sent[mid]

        self.projects = Projects()

    def _rec(self, op, input, result):
        if self.error is not None:
            raise self.error
        dumped = input.model_dump(mode="json", by_alias=True, exclude_unset=True)
        assert PROJECT in dumped["path"].values()
        self.requests.append((op, dumped))
        return dumped if result is None else result

    def sends(self):
        return [r for op, r in self.requests if op == "send_message"]


@pytest.fixture(scope="module")
def base_session():
    return BusinessSession.from_demo("restaurant", models=FAST, n_sims=300)


def make(tmp_path, base_session, fake=None, **kw):
    fake = fake or FakePhoton()
    gw = PhotonGateway(KEY, PROJECT, client=fake)
    svc = MessagingService(gw, MessagingStore(tmp_path / "state.json"), lambda k: base_session,
                           agent_mode=kw.pop("agent_mode", "offline"), **kw)
    return svc, fake


def started(tmp_path, base_session, **kw):
    """Service where the owner has already texted the agent line (conversation exists)."""
    fake = kw.pop("fake", None) or FakePhoton()
    fake.inbox.append(inbound("msg_hist_1", "hi", at=iso(datetime.now(UTC) - timedelta(days=1))))
    svc, fake = make(tmp_path, base_session, fake, **kw)
    svc.sync_recipients()
    return svc, fake


# ------------------------------------------------------------------ configuration and status
def test_config_problems_and_no_client_without_config():
    assert PhotonGateway("", "").config_problems() == ["PHOTON_API_KEY is not set",
                                                       "PHOTON_PROJECT_ID is not set"]
    bad = PhotonGateway("sk-old-spectrum-key", "proj_123").config_problems()
    assert len(bad) == 2 and all("Photon 2.0" in b for b in bad)
    assert PhotonGateway(KEY, PROJECT).config_problems() == []
    with pytest.raises(PhotonUnavailable, match="PHOTON_API_KEY is not set"):
        PhotonGateway("", "").client()
    assert PhotonGateway("", "").status()["connected"] is False


def test_status_is_read_only_and_masks_phone_numbers(tmp_path, base_session):
    svc, fake = make(tmp_path, base_session)
    st = svc.gateway.status()
    assert st["connected"] and st["imessage"]["mode"] == "shared" and st["sms_lines"] == []
    assert {r["masked"] for r in st["recipients"]} == {mask(OWNER), mask(OTHER)}
    text = json.dumps(st)
    assert OWNER not in text and LINE not in text and KEY not in text
    assert not fake.sends()


def test_api_errors_are_scrubbed_of_credentials(tmp_path, base_session):
    svc, fake = make(tmp_path, base_session)
    fake.error = ApiError(f"unauthorized key {KEY} for {PROJECT}", status=401, headers={},
                          body=None, raw_body="", operation_id="getProject", request_id=None)
    with pytest.raises(PhotonUnavailable) as exc:
        svc.gateway.status()
    assert "HTTP 401" in str(exc.value) and KEY not in str(exc.value) and PROJECT not in str(exc.value)


# ------------------------------------------------------------------ recipients and routing
def test_only_opted_in_assignments_are_authorized_and_history_is_not_answered(tmp_path, base_session):
    svc, fake = started(tmp_path, base_session)
    owners = svc.store.owners
    assert owners[OWNER].authorized and OTHER not in owners or not owners[OTHER].authorized
    o = owners[OWNER]
    assert o.space_id == f"spc_{UIDS[OWNER]}" and o.agent_user_id == "usr_agent"
    assert "msg_hist_1" in svc.store.processed
    assert svc.poll_once() == [] and not fake.sends()  # the old "hi" is never answered


def test_question_gets_agent_reply_in_owner_conversation(tmp_path, base_session):
    svc, fake = started(tmp_path, base_session)
    fake.inbox.append(inbound("msg_q1", "What should I do if food costs rise 12%?"))
    [rec] = svc.poll_once()
    [req] = fake.sends()
    body = req["body"]
    assert body["spaceId"] == f"spc_{UIDS[OWNER]}" and body["senderId"] == "usr_agent"
    assert body["parentMessageId"] == "msg_q1" and body["content"]["type"] == "text"
    assert req["headers"]["idempotency-key"] == "msrd-reply:msg_q1"
    assert len(body["content"]["text"]) <= MAX_TEXT
    assert rec["kind"] == "reply:offline" and rec["status"] == "dispatched"
    assert rec["delivered_at"] is None and rec["evidence"]["run_ids"] and rec["evidence"]["grounded"]
    assert rec["to"] == mask(OWNER) and OWNER not in json.dumps(svc.store.outbound)


def test_duplicate_and_redelivered_messages_are_processed_once(tmp_path, base_session):
    svc, fake = started(tmp_path, base_session)
    msg = inbound("msg_dup", "How is cash looking?")
    fake.inbox.append(msg)
    assert len(svc.poll_once()) == 1
    assert svc.handle_inbound(msg) is None  # webhook + poll racing on the same message
    restarted = MessagingService(svc.gateway, MessagingStore(tmp_path / "state.json"),
                                 lambda k: base_session, agent_mode="offline")
    assert restarted.handle_inbound(msg) is None
    assert len(fake.sends()) == 1


def test_unauthorized_senders_and_echoes_are_ignored(tmp_path, base_session):
    svc, fake = started(tmp_path, base_session)
    fake.inbox.append(inbound("msg_x1", "What should I do?", handle=STRANGER))
    fake.inbox.append(inbound("msg_x2", "What should I do?", handle=OTHER))  # opt-in pending
    echo = inbound("msg_x3", "agent echo")
    echo["sentBy"], echo["receivedBy"] = echo["receivedBy"], echo["sentBy"]
    fake.inbox.append(echo)
    assert svc.poll_once() == [] and not fake.sends()
    assert any(e["kind"] == "ignored" and mask(STRANGER) in e["detail"] for e in svc.store.events)
    assert STRANGER not in json.dumps(svc.store.events)


def test_owners_have_isolated_memory_and_scenarios(tmp_path, base_session):
    fake = FakePhoton(assignments=[
        {"handle": h, "assignedPhoneNumber": LINE, "optIn": {"state": "received"}}
        for h in (OWNER, OTHER)])
    old = iso(datetime.now(UTC) - timedelta(days=1))
    fake.inbox += [inbound("h1", "hi", OWNER, old), inbound("h2", "hi", OTHER, old)]
    svc, fake = make(tmp_path, base_session, fake)
    svc.sync_recipients()
    fake.inbox.append(inbound("q_owner", "What should I do if food costs rise 15%?", OWNER))
    fake.inbox.append(inbound("q_other", "How is cash looking?", OTHER))
    assert len(svc.poll_once()) == 2
    a, b = (svc.agent_for(svc.store.owners[h]) for h in (OWNER, OTHER))
    assert a is not b and a.session is not b.session
    assert a.session.state.food_cost_change_pct == 15 and b.session.state.food_cost_change_pct == 0
    assert base_session.state.food_cost_change_pct == 0  # dashboard session untouched
    assert a.user_texts == ["What should I do if food costs rise 15%?"]
    assert b.user_texts == ["How is cash looking?"]
    assert {r["body"]["spaceId"] for r in fake.sends()} == {f"spc_{UIDS[OWNER]}", f"spc_{UIDS[OTHER]}"}


# ------------------------------------------------------------------ consent and commands
def test_stop_blocks_everything_until_start(tmp_path, base_session):
    svc, fake = started(tmp_path, base_session)
    svc.set_alert_consent(OWNER, True)
    fake.inbox.append(inbound("s1", "STOP"))
    [rec] = svc.poll_once()
    assert rec["kind"] == "stop" and "unsubscribed" in rec["text"]
    o = MessagingStore(tmp_path / "state.json").owners[OWNER]
    assert o.stopped and not o.alerts_opt_in  # persisted
    fake.inbox.append(inbound("s2", "What should I do?"))
    assert svc.poll_once() == []
    sig = RiskSignal("cash_shortfall", "critical", "t", "Cash risk. Reply OPTIONS.", "k1", [{}])
    assert svc.send_alert(OWNER, sig) is None
    fake.inbox.append(inbound("s3", "START"))
    [rec] = svc.poll_once()
    assert rec["kind"] == "start" and svc.store.owners[OWNER].alerts_opt_in
    assert svc.store.owners[OWNER].consent_source == "message:START"
    assert len(fake.sends()) == 2


def test_help_and_options_commands(tmp_path, base_session):
    svc, fake = started(tmp_path, base_session)
    fake.inbox.append(inbound("c1", "help"))
    fake.inbox.append(inbound("c2", "OPTIONS"))
    help_rec, opt_rec = svc.poll_once()
    assert help_rec["kind"] == "help" and "STOP" in help_rec["text"]
    assert opt_rec["kind"] == "reply:offline" and opt_rec["evidence"]["run_ids"]
    agent = svc.agent_for(svc.store.owners[OWNER])
    assert agent.user_texts[0].startswith("What should I do?")
    assert any(c["name"] == EVAL for c in agent.planner.answer("What should I do?").tool_calls)


# ------------------------------------------------------------------ alerts
def grounded_signal(key="sig-1", kind="cash_shortfall"):
    out = {"run_id": "run_1", "cash_shortage_probability": 0.42, "starting_cash": 5000}
    return RiskSignal(kind, "critical", "Cash", "42% of scenarios run out of cash with $5,000.",
                      key, [out])


def test_alerts_need_consent_preference_and_are_sent_once(tmp_path, base_session):
    svc, fake = started(tmp_path, base_session)
    assert svc.send_alert(OWNER, grounded_signal()) is None  # no consent yet
    svc.set_alert_consent(OWNER, True)
    rec = svc.send_alert(OWNER, grounded_signal())
    assert rec["kind"] == "alert:cash_shortfall" and rec["evidence"]["run_ids"] == ["run_1"]
    assert fake.sends()[0]["headers"]["idempotency-key"] == "msrd-alert:sig-1"
    assert svc.send_alert(OWNER, grounded_signal()) is None  # dedupe
    svc.store.owners[OWNER].alert_prefs = ["low_runway"]
    assert svc.send_alert(OWNER, grounded_signal("sig-2")) is None  # preference off
    assert len(fake.sends()) == 1


def test_ungrounded_alert_is_blocked(tmp_path, base_session):
    svc, fake = started(tmp_path, base_session)
    svc.set_alert_consent(OWNER, True)
    bad = RiskSignal("cash_shortfall", "critical", "t", "77% of scenarios fail.", "bad", [{"x": 1}])
    assert svc.send_alert(OWNER, bad) is None and not fake.sends()
    assert any(e["kind"] == "alert_blocked" for e in svc.store.events)


def test_cannot_message_before_owner_starts_conversation_or_past_daily_cap(tmp_path, base_session):
    svc, fake = make(tmp_path, base_session)
    svc.sync_recipients()  # authorized but has never texted the line
    with pytest.raises(PermissionError, match="has not messaged"):
        svc.send_test(OWNER)
    svc2, fake2 = started(tmp_path / "b", base_session, daily_limit=2)
    svc2.send_test(OWNER)
    svc2.send_test(OWNER)
    with pytest.raises(PermissionError, match="daily send cap"):
        svc2.send_test(OWNER)
    assert len(fake2.sends()) == 2 and not fake.sends()


def test_risk_scan_signals_are_grounded_and_stable(tmp_path):
    s = BusinessSession.from_demo("restaurant", models=FAST, n_sims=300,
                                  fred_cache_dir=str(tmp_path / "nofred"))
    s.state.food_cost_change_pct, s.state.starting_cash = 15.0, 2000.0
    sigs = scan(s)
    kinds = {x.kind for x in sigs}
    assert "cost_increase" in kinds and ("cash_shortfall" in kinds or "low_runway" in kinds)
    from decisiondesk.agent.grounding import ungrounded_numbers
    for sig in sigs:
        assert ungrounded_numbers(sig.text, sig.tool_outputs, []) == [], sig.text
        assert all(sig.run_ids)
    assert [x.dedup_key for x in scan(s)] == [x.dedup_key for x in sigs]


# ------------------------------------------------------------------ delivery status
def test_delivery_reported_only_when_confirmed(tmp_path, base_session):
    svc, fake = started(tmp_path, base_session)
    r1, r2, r3 = (svc.send_test(OWNER) for _ in range(3))
    assert all(r["status"] == "dispatched" and r["delivered_at"] is None for r in (r1, r2, r3))
    fake.sent[r1["message_id"]] = {"id": r1["message_id"], "status": "delivered",
                                   "timestamps": {"deliveredAt": "2026-11-14T18:00:00.000Z"}}
    fake.sent[r2["message_id"]] = {"id": r2["message_id"], "status": "failed",
                                   "failure": {"code": "recipient_unreachable"}}
    fake.sent[r3["message_id"]] = {"id": r3["message_id"], "status": "accepted",
                                   "acceptance": {"code": "delivery_confirmation_unavailable"}}
    assert svc.refresh_statuses() == 3
    by = {r["message_id"]: r for r in svc.store.outbound}
    assert by[r1["message_id"]]["delivered_at"] == "2026-11-14T18:00:00.000Z"
    assert by[r2["message_id"]]["failure"] == "recipient_unreachable"
    assert by[r3["message_id"]]["status"] == "accepted" and not by[r3["message_id"]]["delivered_at"]
    assert by[r3["message_id"]]["acceptance"] == "delivery_confirmation_unavailable"
    assert svc.refresh_statuses() == 0  # all final; no more polling


def test_long_replies_are_trimmed():
    text = ("The forecast is stable. " * 120).strip()
    out = fit_text(text)
    assert len(out) <= MAX_TEXT and out.endswith("dashboard)")
    assert fit_text("short") == "short"


# ------------------------------------------------------------------ Gemini through iMessage
def test_gemini_analyst_answers_imessage_with_tools(tmp_path, base_session, monkeypatch):
    monkeypatch.setenv("MSRD_ROUTING", "llm")  # tests the model's own tool loop
    from decisiondesk.agent import gemini as gem
    monkeypatch.setattr(gem, "BUDGET", gem.CallBudget(100))
    client = fake_client(call(EVAL, {"food_cost_change_pct": 10}), profit_text)
    svc, fake = started(tmp_path, base_session, agent_mode=None, agent_client=client)
    fake.inbox.append(inbound("g1", "What should I do if food costs rise 10%?"))
    [rec] = svc.poll_once()
    assert rec["kind"] == "reply:gemini" and rec["evidence"]["grounded"]
    assert rec["evidence"]["run_ids"] and "10%" in fake.sends()[0]["body"]["content"]["text"]
    assert "600 characters" in client.models.requests[0]["config"].system_instruction


def test_gemini_outage_still_answers_from_offline_analyst(tmp_path, base_session, monkeypatch):
    monkeypatch.setenv("MSRD_ROUTING", "llm")  # tests the model's own tool loop
    from test_gemini import api_error

    from decisiondesk.agent import gemini as gem
    monkeypatch.setattr(gem, "BUDGET", gem.CallBudget(100))
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "")
    client = fake_client(api_error(503, "UNAVAILABLE"))
    svc, fake = started(tmp_path, base_session, agent_mode=None, agent_client=client)
    fake.inbox.append(inbound("g2", "What should I do?"))
    [rec] = svc.poll_once()
    assert rec["kind"] == "reply:offline" and rec["text"].startswith("(Gemini unavailable")


# ------------------------------------------------------------------ webhooks
SECRET = "whsec_" + base64.b64encode(b"unit-test-webhook-secret-32bytes!").decode()


def signed(event, wid="wh_1"):
    body = json.dumps(event)
    ts = datetime.now(UTC)
    sig = Webhook(SECRET).sign(wid, ts, body)
    return body.encode(), {"Webhook-Id": wid, "Webhook-Timestamp": str(int(ts.timestamp())),
                           "Webhook-Signature": sig}


def test_webhook_signature_dedupe_and_retry(tmp_path, base_session):
    svc, fake = started(tmp_path, base_session)
    msg = inbound("w1", "How is cash looking?")
    fake.inbox.append(msg)
    seen: set[str] = set()
    body, headers = signed({"type": "message.received", "data": {"message": {"id": "w1"}}})
    assert webhooks.handle_delivery(body, headers, SECRET, svc, seen) == 204
    assert len(fake.sends()) == 1 and "wh_1" in seen
    assert webhooks.handle_delivery(body, headers, SECRET, svc, seen) == 204  # duplicate
    assert len(fake.sends()) == 1
    tampered = body.replace(b"w1", b"w9")
    assert webhooks.handle_delivery(tampered, headers, SECRET, svc, seen) == 400
    assert webhooks.handle_delivery(body, headers, "", svc, seen) == 400  # no secret configured
    b2, h2 = signed({"type": "message.received", "data": {"message": {"id": "w2"}}}, "wh_2")
    fake.error = TimeoutError("photon down")
    assert webhooks.handle_delivery(b2, h2, SECRET, svc, seen) == 503 and "wh_2" not in seen


# ------------------------------------------------------------------ local simulator
def test_simulator_round_trip_is_labelled(tmp_path, base_session):
    gw = SimulatorGateway()
    svc = MessagingService(gw, MessagingStore(tmp_path / "sim.json"), lambda k: base_session,
                           agent_mode="offline")
    assert svc.simulated and "SIMULATOR" in svc.channel_label
    svc.sync_recipients()
    gw.simulate_inbound("What should I do if food costs rise 10%?")
    [rec] = svc.poll_once()
    assert rec["simulated"] and rec["status"] == "delivered" and gw.sent[0]["text"] == rec["text"]
    assert svc.refresh_statuses() == 0
