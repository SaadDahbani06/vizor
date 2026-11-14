"""Lightweight proactive monitoring: saved rules -> checks -> deduplicated, evidence-backed alerts.

  python -m decisiondesk.monitor add --business restaurant --kind fred_release --series CUSR0000SAF11 --threshold 1.0
  python -m decisiondesk.monitor run --business restaurant [--live]
  python -m decisiondesk.monitor list

Rule kinds:
  fred_release   a new or REVISED FRED observation for a series (optional threshold on the
                 absolute 12-month change). For restaurant food-CPI series the business impact is
                 re-evaluated by running the decision engine with the observed change as a
                 HYPOTHETICAL pass-through to food costs.
  cash_shortfall the simulated 28-day cash shortfall frequency reaches a threshold.
State (rules, last-seen observations, alerts, sent keys) persists in a JSON file. Alerts are
deduplicated by a key built from the rule and the evidence. In-app alerts are always stored;
Telegram delivery is opt-in per rule AND requires TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID. No
business action is ever executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests

from .connectors.base import ConnectorError
from .connectors.fred import FREDConnector
from .macro import _latest_comparable

STATE_PATH = Path("data/cache/monitor_state.json")
KINDS = ("fred_release", "cash_shortfall")
FOOD_SERIES = {"CUSR0000SAF11"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class AlertRule:
    rule_id: str
    business: str
    kind: str
    series_id: str | None = None
    threshold: float | None = None
    telegram_opt_in: bool = False
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if self.kind == "fred_release" and not self.series_id:
            raise ValueError("fred_release rules need a series_id")
        if self.kind == "cash_shortfall" and self.threshold is None:
            raise ValueError("cash_shortfall rules need a threshold (0-1)")


@dataclass
class Alert:
    alert_id: str
    rule_id: str
    created_at: str
    title: str
    message: str
    evidence: dict
    category: str
    acknowledged: bool = False
    delivered: list[str] = field(default_factory=lambda: ["in_app"])


class MonitorStore:
    def __init__(self, path: str | Path = STATE_PATH):
        self.path = Path(path)
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.rules = [AlertRule(**r) for r in data.get("rules", [])]
        self.seen: dict = data.get("seen", {})
        self.alerts = [Alert(**a) for a in data.get("alerts", [])]
        self.sent_keys: set[str] = set(data.get("sent_keys", []))
        self.last_run: str | None = data.get("last_run")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "rules": [asdict(r) for r in self.rules], "seen": self.seen,
            "alerts": [asdict(a) for a in self.alerts], "sent_keys": sorted(self.sent_keys),
            "last_run": self.last_run}, indent=2, default=str))

    def add_rule(self, business: str, kind: str, series_id: str | None = None,
                 threshold: float | None = None, telegram_opt_in: bool = False) -> AlertRule:
        rid = hashlib.sha1(f"{business}|{kind}|{series_id}|{threshold}".encode()).hexdigest()[:10]
        existing = next((r for r in self.rules if r.rule_id == rid), None)
        if existing:
            return existing
        rule = AlertRule(rid, business, kind, series_id, threshold, telegram_opt_in)
        self.rules.append(rule)
        self.save()
        return rule

    def remove_rule(self, rule_id: str) -> None:
        self.rules = [r for r in self.rules if r.rule_id != rule_id]
        self.save()


def _emit(store: MonitorStore, rule: AlertRule, key_parts: tuple, title: str, message: str,
          evidence: dict, category: str, notifier=None) -> Alert | None:
    key = hashlib.sha1("|".join(map(str, (rule.rule_id, *key_parts))).encode()).hexdigest()[:16]
    if key in store.sent_keys:
        return None
    alert = Alert(key, rule.rule_id, _now(), title, message, evidence, category)
    if rule.telegram_opt_in and notifier is not None:
        try:
            notifier.send(f"{title}\n{message}")
            alert.delivered.append("telegram")
        except (ConnectorError, requests.RequestException) as exc:  # in-app alert is kept
            alert.evidence["telegram_error"] = type(exc).__name__
    store.alerts.append(alert)
    store.sent_keys.add(key)
    return alert


def check_fred_release(store, rule, session, fred: FREDConnector, notifier=None) -> Alert | None:
    vintages, prov = fred.fetch_vintages(rule.series_id, "2021-01-01")
    asof = pd.Timestamp(datetime.now(UTC).date())
    live = vintages[(vintages.realtime_start <= asof) & (vintages.realtime_end >= asof)]
    snap = live.set_index("observed_at")["value"].sort_index()
    if snap.empty:
        return None
    latest = snap.index.max()
    value = float(snap[latest])
    seen = store.seen.get(rule.series_id)
    current = {"observed_at": str(latest.date()), "value": value}
    store.seen[rule.series_id] = current
    if seen == current:
        return None
    comp = _latest_comparable(snap, pd.DateOffset(years=1))
    yoy = None if comp is None else 100 * (float(snap[comp]) / float(snap[comp - pd.DateOffset(years=1)]) - 1)
    kind = "new release" if seen is None or seen["observed_at"] != current["observed_at"] else "revision"
    if rule.threshold is not None and (yoy is None or abs(yoy) < rule.threshold):
        return None
    evidence = {"series_id": rule.series_id, "title": prov.notes.split(";")[0],
                "observed_at": current["observed_at"], "value": value, "units": prov.units,
                "previously_seen": seen, "change_12m_pct": None if yoy is None else round(yoy, 2),
                "change_12m_period": None if comp is None else str(comp.date())[:7],
                "published_at": str(vintages.loc[vintages.observed_at == latest, "published_at"].min().date()),
                "retrieved_at_utc": str(vintages["retrieved_at_utc"].iloc[0]),
                "source_url": prov.source_url, "category": "observed_economic_data"}
    message = (f"FRED {rule.series_id} {kind}: {value:g} {prov.units} for {current['observed_at'][:7]}"
               + ("" if yoy is None else f"; 12-month change {yoy:+.2f}% ({evidence['change_12m_period']})")
               + f". Published {evidence['published_at']}.")
    if session is not None and rule.series_id in FOOD_SERIES and session.industry == "restaurant" \
            and yoy is not None:
        import copy

        from .agent.tools import evaluate_restaurant_actions
        saved = copy.deepcopy(session.state)  # never alter the user's own scenario
        try:
            out = evaluate_restaurant_actions(session, food_cost_change_pct=round(yoy, 2))
        finally:
            session.state = saved
        evidence["re_evaluation"] = {
            "assumption": f"HYPOTHETICAL: food costs move by the observed {yoy:+.2f}% (no "
                          "pass-through relationship has been estimated)",
            "run_id": out["run_id"], "recommended_action": out["recommended_action"],
            "strength": out["strength"], "baseline_expected_profit": out["baseline_expected_profit"]}
        message += (f" Re-evaluated with that change as a hypothetical food-cost shift: expected "
                    f"28-day profit with no action ${out['baseline_expected_profit']:,.0f}; "
                    f"recommended: {out['recommended_action'] or 'none'} ({out['strength']}). "
                    "Review in Decisions; nothing was executed.")
    return _emit(store, rule, (rule.series_id, current["observed_at"], value),
                 f"{rule.series_id}: {kind}", message, evidence, "observed_economic_data", notifier)


def check_cash(store, rule, session, notifier=None) -> Alert | None:
    from .agent.tools import assess_cash_risk
    out = assess_cash_risk(session)
    if not out.get("available"):
        return None
    freq = out["simulated_shortfall_frequency"]
    if freq < rule.threshold:
        return None
    message = (f"Simulated 28-day cash shortfall frequency {freq:.1%} reached the threshold "
               f"{rule.threshold:.1%} (starting cash ${out['starting_cash']:,.0f}, "
               f"{out['starting_cash_source']}). {out['calibration']}.")
    return _emit(store, rule, (session.dataset, str(session.origin.date()), round(freq, 2)),
                 "Cash shortfall risk", message, out, "model_estimate", notifier)


def run_checks(store: MonitorStore, sessions: dict, *, fred: FREDConnector | None = None,
               notifier=None) -> list[Alert]:
    """sessions: {business key -> BusinessSession}. Returns alerts created by this run."""
    fred = fred or FREDConnector(offline=not os.getenv("FRED_API_KEY"))
    new = []
    for rule in store.rules:
        session = sessions.get(rule.business)
        try:
            if rule.kind == "fred_release":
                a = check_fred_release(store, rule, session, fred, notifier)
            else:
                a = None if session is None else check_cash(store, rule, session, notifier)
        except ConnectorError as exc:
            store.seen.setdefault("_errors", []).append(f"{_now()} {rule.rule_id}: {exc}")
            continue
        if a:
            new.append(a)
    store.last_run = _now()
    store.save()
    return new


class TelegramNotifier:
    """Sends a message only when both credentials exist; used only for opted-in rules."""

    def __init__(self, token: str | None = None, chat_id: str | None = None, session=None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> None:
        if not self.configured:
            raise ConnectorError("Telegram not configured")
        r = self.session.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              json={"chat_id": self.chat_id, "text": text[:4000]}, timeout=15)
        if getattr(r, "status_code", 500) != 200:
            raise ConnectorError(f"Telegram HTTP {getattr(r, 'status_code', '?')}")


def main() -> None:
    from dotenv import load_dotenv

    from .agent.tools import BusinessSession
    load_dotenv(".env")
    parser = argparse.ArgumentParser(description="Main Street Risk Desk monitor")
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--business", choices=["restaurant", "retail"], required=True)
    a.add_argument("--kind", choices=KINDS, required=True)
    a.add_argument("--series")
    a.add_argument("--threshold", type=float)
    a.add_argument("--telegram", action="store_true", help="opt in to Telegram delivery")
    r = sub.add_parser("run")
    r.add_argument("--live", action="store_true", help="query FRED live (needs FRED_API_KEY)")
    sub.add_parser("list")
    args = parser.parse_args()
    store = MonitorStore()
    if args.cmd == "add":
        rule = store.add_rule(args.business, args.kind, args.series, args.threshold, args.telegram)
        print("rule", asdict(rule))
    elif args.cmd == "list":
        for rule in store.rules:
            print("RULE", asdict(rule))
        for al in store.alerts:
            print("ALERT", al.created_at, al.title, "|", al.message)
    else:
        businesses = {r.business for r in store.rules}
        sessions = {b: BusinessSession.from_demo(b) for b in businesses}
        notifier = TelegramNotifier()
        new = run_checks(store, sessions, fred=FREDConnector(offline=not args.live),
                         notifier=notifier if notifier.configured else None)
        print(f"{len(new)} new alert(s); {len(store.alerts)} stored")
        for al in new:
            print("ALERT", al.title, "|", al.message)


if __name__ == "__main__":
    main()
