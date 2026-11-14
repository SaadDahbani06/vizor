"""Grounding check: every number in an agent reply must come from a tool output or the user.

A number in the reply is GROUNDED if, at the precision it is written, it equals some number
found in the tool outputs of this conversation (or in the user's own messages). Accepted
transformations: rounding to the written precision, percent <-> fraction (3.5% ~ 0.035),
thousands/millions suffixes ($1.2k, 1.4M), sign of a difference, and absolute values. Dates and
identifiers inside tool outputs contribute their digits (so "2026-11-13" grounds "2026").
Small counting words (integers 0-10 written without $ or %) are exempt.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

NUM_RE = re.compile(
    r"(?<![\w.])(?P<sign>[-+−])?(?P<cur>\$)?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suf>\s?(?:k|K|M|million|thousand|bn|billion)\b)?(?P<pct>\s?%| percent| pp| percentage points)?")
DATE_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
ID_RE = re.compile(r"\b[A-Za-z_]+\d+[A-Za-z0-9_]*\b")  # identifiers start with a letter: p95, raise_prices_3pct, CUSR0000SAF11


@dataclass
class Mention:
    text: str
    value: float
    decimals: int
    is_pct: bool


def _mentions(text: str) -> list[Mention]:
    cleaned = DATE_RE.sub(" ", text)
    cleaned = ID_RE.sub(" ", cleaned)
    out = []
    for m in NUM_RE.finditer(cleaned):
        raw = m.group("num")
        value = float(raw.replace(",", ""))
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        suf = (m.group("suf") or "").strip().lower()
        mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}.get(suf, 1)
        if mult != 1:  # "1.03k" is precise to +/-5: decimals may become negative
            value, decimals = value * mult, decimals - {1e3: 3, 1e6: 6, 1e9: 9}[mult]
        if m.group("sign") in ("-", "−"):
            value = -value
        out.append(Mention(m.group(0).strip(), value, decimals, bool(m.group("pct"))))
    return out


def numbers_in(obj) -> set[float]:
    """All numeric values in a (nested) tool output, including digits inside strings."""
    found: set[float] = set()

    def walk(x):
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            found.add(float(x))
        elif isinstance(x, str):
            for tok in re.findall(r"-?\d[\d,]*\.?\d*", x):
                try:
                    found.add(float(tok.replace(",", "").rstrip(".")))
                except ValueError:
                    pass
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
    walk(obj if not isinstance(obj, str) else json.loads(obj) if obj.startswith(("{", "[")) else obj)
    return found


def _matches(m: Mention, allowed: set[float]) -> bool:
    """Grounded if some allowed value, rounded to the written precision, equals the mention:
    (1) in the same units, or (2) for a percent mention, as a fraction x 100."""
    tol = 0.5 * 10 ** (-m.decimals) + 1e-9
    target = abs(m.value)
    for a in allowed:
        a = abs(a)
        if abs(round(a, m.decimals) - target) <= tol:
            return True
        if m.is_pct and abs(round(a * 100, m.decimals) - target) <= tol:
            return True
    return False


def ungrounded_numbers(reply: str, tool_outputs: list, user_texts: list[str]) -> list[str]:
    allowed: set[float] = set()
    for o in tool_outputs:
        allowed |= numbers_in(o)
    for u in user_texts:
        allowed |= {abs(m.value) for m in _mentions(u)}
        allowed |= {abs(m.value) / 100 for m in _mentions(u) if m.is_pct}
    bad = []
    for m in _mentions(reply):
        if not m.is_pct and "$" not in m.text and float(m.value).is_integer() and 0 <= m.value <= 10:
            continue
        if not _matches(m, allowed):
            bad.append(m.text)
    return bad
