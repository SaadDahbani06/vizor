"""Messaging worker: answers iMessages and sends opted-in risk alerts.

  python -m decisiondesk.messaging.worker status            # read-only Photon status (masked)
  python -m decisiondesk.messaging.worker run --once        # one poll + status refresh
  python -m decisiondesk.messaging.worker run               # poll every 4 s until Ctrl-C
  python -m decisiondesk.messaging.worker alerts            # scan risks, send opted-in alerts
  python -m decisiondesk.messaging.worker scenario          # which scenario replies will use

Only ONE process may answer messages: ``run`` and ``alerts`` take the poller lock
(``messaging/shared.py``) and refuse to start while the dashboard's auto-check (or another
worker) holds it. Replies use the scenario published from the dashboard's Scenario Lab /
Messaging page, or the business's default scenario when nothing is published.

Event source: the documented list-messages polling path (no public URL needed). Webhooks are
supported by ``webhooks.py`` for deployments with a public HTTPS endpoint.
"""
from __future__ import annotations

import argparse
import json
import time

from dotenv import load_dotenv

from ..agent.tools import BusinessSession
from .gateway import PhotonGateway, PhotonUnavailable
from .risk_signals import scan
from .service import MessagingService, MessagingStore
from .shared import PollerLock, describe_holder

_SESSIONS: dict[str, BusinessSession] = {}


def session_for(key: str) -> BusinessSession:
    if key not in _SESSIONS:
        _SESSIONS[key] = BusinessSession.from_demo(key)
    return _SESSIONS[key]


def build_service() -> MessagingService:
    return MessagingService(PhotonGateway(), MessagingStore(), session_for)


def main() -> None:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(description="Main Street Risk Desk messaging worker")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    r = sub.add_parser("run")
    r.add_argument("--once", action="store_true")
    r.add_argument("--interval", type=float, default=4.0)
    sub.add_parser("alerts")
    sc = sub.add_parser("scenario")
    sc.add_argument("--business", default="restaurant")
    args = parser.parse_args()
    svc = build_service()
    if args.cmd == "scenario":
        print(json.dumps(svc.scenario_source(args.business), indent=1))
        return
    lock = PollerLock("worker", ttl_s=3600)
    if args.cmd in ("run", "alerts") and not lock.acquire():
        raise SystemExit("Another messaging poller is active: " + describe_holder(lock.holder())
                         + ". Stop it (or turn off the dashboard's auto-check) first, so two "
                         "pollers never answer the same message.")
    try:
        if args.cmd == "status":
            print(json.dumps(svc.gateway.status(), indent=1, default=str))
            return
        svc.sync_recipients()
        if args.cmd == "alerts":
            for owner in [o for o in svc.store.owners.values() if o.authorized]:
                for sig in scan(svc.agent_for(owner).session):  # owner's own scenario
                    rec = svc.send_alert(owner.handle, sig)
                    print(sig.kind, "->", "sent" if rec else "not sent (consent/dedupe/pref)")
            return
        while True:
            if not lock.heartbeat():
                raise SystemExit("Lost the poller lock to " + describe_holder(lock.holder()))
            sent = svc.poll_once()
            svc.refresh_statuses()
            for rec in sent:
                print(f"replied to {rec['to']} ({rec['kind']}): status {rec['status']}")
            if args.once:
                break
            time.sleep(args.interval)
    except PhotonUnavailable as exc:
        raise SystemExit(f"Photon unavailable: {exc}") from None
    except KeyboardInterrupt:
        pass
    finally:
        lock.release()


if __name__ == "__main__":
    main()
