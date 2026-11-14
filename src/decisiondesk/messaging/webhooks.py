"""Photon webhook verification (Standard Webhooks) and event intake.

Photon signs each delivery with headers webhook-id, webhook-timestamp and webhook-signature
(HMAC-SHA256 over "id.timestamp.rawBody" with the base64-decoded ``whsec_`` secret; timestamps
older or newer than 5 minutes are rejected). We verify with the official ``standardwebhooks``
library, dedupe by webhook-id, and only then hand the message ID to the service, which re-reads
the message from the API (so we never trust unverified payload fields).

Webhooks need a PUBLIC HTTPS URL (Photon blocks localhost). For local demos use the polling or
WebSocket worker instead.
"""
from __future__ import annotations

import json


class WebhookRejected(ValueError):
    pass


def verify(raw_body: bytes, headers: dict, secret: str) -> dict:
    """Return the parsed event if the signature is valid, else raise WebhookRejected."""
    from standardwebhooks.webhooks import Webhook, WebhookVerificationError
    if not secret:
        raise WebhookRejected("PHOTON_WEBHOOK_SECRET not configured")
    lower = {k.lower(): v for k, v in headers.items()}
    try:
        return Webhook(secret).verify(raw_body, lower)
    except (WebhookVerificationError, ValueError) as exc:
        raise WebhookRejected(f"signature verification failed: {type(exc).__name__}") from None


def message_id_from_event(event: dict) -> str | None:
    """Extract the message ID from a verified message.* event without trusting other fields."""
    if not str(event.get("type", "")).startswith("message."):
        return None
    data = event.get("data") or {}
    msg = data.get("message") if isinstance(data.get("message"), dict) else data
    return msg.get("id") if isinstance(msg, dict) else None


def handle_delivery(raw_body: bytes, headers: dict, secret: str, service, seen: set[str]) -> int:
    """HTTP status to return: 204 accepted/duplicate, 400 bad signature, 503 retry later."""
    try:
        event = verify(raw_body, headers, secret)
    except WebhookRejected:
        return 400
    wid = {k.lower(): v for k, v in headers.items()}.get("webhook-id")
    if wid in seen:
        return 204
    mid = message_id_from_event(event if isinstance(event, dict) else json.loads(event))
    try:
        if mid and str(event.get("type")) == "message.received":
            service.handle_inbound(service.gateway.get_message(mid))
        elif mid:
            service.refresh_statuses()
    except Exception:  # noqa: BLE001 - tell Photon to retry instead of losing the event
        return 503
    seen.add(wid)
    return 204
