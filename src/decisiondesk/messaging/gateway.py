"""Photon 2.0 (Beta) gateway built on the official Python client ``photonhq-api`` (``photon_api``).

Verified against https://photon.codes/docs/beta/ and https://api.photon.codes/openapi.json
(2026-11-14):
  * API origin https://api.photon.codes/v1; Bearer auth with a project API key (``pho_sk_``);
    project IDs start with ``pho_prj_``.
  * Messages: ``GET /v1/projects/{id}/messages`` (filters: direction, platform, createdSince,
    spaceId, order, pageSize, pageToken) and ``POST /v1/projects/{id}/messages`` with a required
    ``Idempotency-Key`` and body {spaceId, senderId, content, parentMessageId?}.
  * A sent message's status is dispatched -> accepted -> delivered | failed. "accepted" is NOT
    delivery; only ``delivered`` (with timestamps.deliveredAt) confirms delivery.
  * Shared iMessage lines (Free plan: 25 assignments, no per-message iMessage fees) can only reply
    in conversations the human started; a shared line cannot send an unsolicited first message.
  * US SMS needs a purchased number AND an approved 10DLC campaign ($0.01/part + carrier fees).
Recipients are authorized ONLY from the project's iMessage assignments whose opt-in state is
"received" (the human texted the agent line). Phone numbers are masked in all outputs.
"""
from __future__ import annotations

import inspect
import os
from dataclasses import dataclass

MAX_TEXT = 1200  # conservative per-message cap; Photon returns 422 MESSAGE_TOO_LONG when exceeded


def mask(handle: str | None) -> str:
    if not handle:
        return "—"
    if "@" in handle:
        name, _, dom = handle.partition("@")
        return f"{name[:1]}***@{dom}"
    return f"•••{handle[-4:]}"


class PhotonUnavailable(RuntimeError):
    """Configuration, auth, API or transport problem; message never contains credentials."""


@dataclass
class Recipient:
    handle: str            # private; never logged unmasked
    assigned_line: str     # Photon agent number for this sender
    opt_in_state: str
    opt_in_at: str | None
    platform: str = "imessage"

    @property
    def masked(self) -> str:
        return mask(self.handle)


class PhotonGateway:
    def __init__(self, api_key: str | None = None, project_id: str | None = None, *, client=None,
                 timeout: float = 15.0):
        self.api_key = (api_key if api_key is not None else os.getenv("PHOTON_API_KEY", "")).strip()
        self.project_id = (project_id if project_id is not None else os.getenv("PHOTON_PROJECT_ID", "")).strip()
        self._client = client
        self.timeout = timeout

    # -------------------------------------------------------------- plumbing
    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.project_id)

    def config_problems(self) -> list[str]:
        problems = []
        if not self.api_key:
            problems.append("PHOTON_API_KEY is not set")
        elif not self.api_key.startswith(("pho_sk_", "pho_ask_")):
            problems.append("PHOTON_API_KEY is not a Photon 2.0 key (expected pho_sk_ or pho_ask_)")
        if not self.project_id:
            problems.append("PHOTON_PROJECT_ID is not set")
        elif not self.project_id.startswith("pho_prj_"):
            problems.append("PHOTON_PROJECT_ID is not a Photon 2.0 project ID (expected pho_prj_)")
        return problems

    def client(self):
        if self._client is None:
            if self.config_problems():
                raise PhotonUnavailable("; ".join(self.config_problems()))
            from photon_api import Photon
            self._client = Photon(headers={"Authorization": f"Bearer {self.api_key}"},
                                  timeout=self.timeout, max_attempts=2, max_retry_after=10.0)
        return self._client

    def _scrub(self, text: str) -> str:
        for secret in (self.api_key, self.project_id):
            if secret:
                text = text.replace(secret, "<redacted>")
        return text

    def _call(self, fn, path: list, *, query: dict | None = None, headers: dict | None = None,
              body: dict | None = None):
        """Build the generated input model from field aliases, then call; errors are scrubbed."""
        try:
            from photon_api import PhotonError
        except ImportError:  # pragma: no cover - client not installed
            PhotonError = Exception
        ann = next(iter(inspect.signature(fn).parameters.values())).annotation
        if isinstance(ann, str):
            import photon_api.rpc_generated as rg
            ann = getattr(rg, ann)
        fields = ann.model_fields
        payload = {}
        path_model = fields["path"].annotation
        names = [(f.alias or n) for n, f in path_model.model_fields.items()]
        payload["path"] = dict(zip(names, path, strict=True))
        if query is not None:
            payload["query"] = query
        if headers is not None:
            payload["headers"] = headers
        if body is not None:
            payload["body"] = body
        try:
            return fn(ann.model_validate(payload))
        except PhotonError as exc:
            status = getattr(exc, "status", None)
            code = getattr(exc, "code", None)
            raise PhotonUnavailable(self._scrub(
                f"Photon {type(exc).__name__}" + (f" HTTP {status}" if status else "")
                + (f" {code}" if code else "") + f": {str(exc)[:200]}")) from None

    @staticmethod
    def _dump(obj):
        return obj.model_dump(mode="json", by_alias=True) if hasattr(obj, "model_dump") else obj

    # -------------------------------------------------------------- read
    def status(self) -> dict:
        """Connection + line status (read-only)."""
        out = {"configured": self.configured, "problems": self.config_problems(),
               "connected": False, "imessage": None, "sms_lines": [], "recipients": []}
        if out["problems"]:
            return out
        p = self.client().projects
        project = self._dump(self._call(p.get, [self.project_id]))
        out.update(connected=True, project_name=project.get("name"))
        out["imessage"] = self._dump(self._call(p.platforms.get_imessage, [self.project_id]))
        res = self._dump(self._call(p.platforms.list_resources, [self.project_id],
                                    query={"state": "active"}))
        out["sms_lines"] = [{"resource": r.get("type") or r.get("kind"),
                             "activation": (r.get("detail") or {}).get("activationState"),
                             "state": r.get("state")} for r in res.get("resources", [])]
        out["recipients"] = [{"masked": r.masked, "line": mask(r.assigned_line),
                              "opt_in": r.opt_in_state, "opt_in_at": r.opt_in_at}
                             for r in self.recipients()]
        return out

    def recipients(self) -> list[Recipient]:
        p = self.client().projects
        data = self._dump(self._call(p.platforms.imessage.assignments.list, [self.project_id],
                                     query={}))
        out = []
        for a in data.get("assignments", []):
            opt = a.get("optIn") or {}
            out.append(Recipient(a.get("handle", ""), a.get("assignedPhoneNumber", ""),
                                 opt.get("state", "unknown"), opt.get("receivedAt")))
        return out

    def list_received(self, created_since: str | None, platform: str | None = None,
                      page_size: int = 50) -> list[dict]:
        p = self.client().projects
        query = {"direction": "received", "order": "asc", "pageSize": page_size}
        if created_since:
            query["createdSince"] = created_since
        if platform:
            query["platform"] = platform
        items: list[dict] = []
        for _ in range(10):  # bounded pagination
            page = self._dump(self._call(p.list_messages, [self.project_id], query=dict(query)))
            items += page.get("items", [])
            token = page.get("nextPageToken")
            if not isinstance(token, str):
                break
            query["pageToken"] = token
        return items

    def get_message(self, message_id: str) -> dict:
        p = self.client().projects
        return self._dump(self._call(p.get_message, [self.project_id, message_id]))

    # -------------------------------------------------------------- write
    def send_text(self, *, space_id: str, sender_id: str, text: str, idempotency_key: str,
                  parent_message_id: str | None = None) -> dict:
        """Submit a plain-text message; returns the API's acceptance (NOT delivery)."""
        text = fit_text(text)
        body = {"spaceId": space_id, "senderId": sender_id,
                "content": {"type": "text", "format": "plain", "text": text}}
        if parent_message_id:
            body["parentMessageId"] = parent_message_id
        p = self.client().projects
        res = self._call(p.send_message, [self.project_id],
                         headers={"idempotency-key": idempotency_key[:255]}, body=body)
        return self._dump(res)


def fit_text(text: str, limit: int = MAX_TEXT) -> str:
    """Trim to the per-message cap at a sentence/line boundary, with a pointer to the dashboard."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 60]
    for sep in ("\n", ". "):
        i = cut.rfind(sep)
        if i > limit // 2:
            cut = cut[: i + 1]
            break
    return cut.rstrip() + " … (full details in the Main Street Risk Desk dashboard)"
