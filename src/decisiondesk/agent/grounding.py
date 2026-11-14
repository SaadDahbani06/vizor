"""Grounding check: every number in an agent reply must come from a tool output or the user.

A number in the reply is GROUNDED if, at the precision it is written, it equals some number
found in the tool outputs of this conversation (or in the user's own messages). Accepted
transformations: rounding to the written precision, percent <-> fraction (3.5% ~ 0.035),
thousands/millions suffixes ($1.2k, 1.4M), sign of a difference, and absolute values. Dates and
identifiers inside tool outputs contribute their digits (so "2026-11-13" grounds "2026").
Small counting words (integers 0-10 written without $ or %) are exempt.

Context check (Milestone 5). A value match is not enough: when the words next to a number name a
concept (elasticity, probability, range, supplier, price, demand, cash, ...), at least one tool
value that matches the number must carry that concept in its own label: its key path, the short
text fields beside it, or the tool's top-level labels. Example caught by this check: "price
elasticity is low (-0.6)" when 0.6 appears only as a supplier success probability. Numbers the
user wrote are always accepted. Without a nearby concept word, the value-only rule applies.
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


CONCEPTS: dict[str, str] = {
    "elasticity": r"elastic\w*",
    "probability": r"probabilit\w*|chances?|likelihood|odds|frequenc\w*",
    "interval": r"intervals?|ranges?|band|p\d{1,2}|lower|upper",
    "supplier": r"suppliers?|vendors?|negotiat\w*",
    "marketing": r"discretionary|marketing|advertis\w*|subscriptions?",
    "profit": r"profits?|profitab\w*|earnings",
    "margin": r"margins?",
    "revenue": r"revenues?|sales",
    "cash": r"cash|runway|liquidity",
    "stockout": r"stock-?outs?|stockouts?|sell-?outs?|shortages?|unmet",
    "inventory": r"inventor\w*|stock|on-hand|supply|supplies|ingredients?|deliver\w*",
    "demand": r"demand|orders?|guests?|covers?|customers?|units?|plates?",
    "accuracy": r"wape|mae|rmse|mape|accuracy|errors?",
    "price": r"prices?|pricing|menu",
    "cost": r"costs?|expenses?|spend\w*|cogs|payroll|labor|rent|bills?",
    "food": r"food",
    "unemployment": r"unemployment|jobless",
    "inflation": r"cpi|inflation",
}
_CONCEPT_RE = {c: re.compile(rf"(?:{p})") for c, p in CONCEPTS.items()}
# Extra words that count only on the TOOL side (labels such as {"low": -1.2, "high": -0.4}).
LABEL_ALIASES = {"interval": re.compile(r"low|high|min|max|p05|p95|p10|p90"),
                 "duration": re.compile(r"days?|weeks?|months?|years?|horizon|period|window")}
DURATION_RE = re.compile(r"\s?-?\s?(days?|weeks?|months?|years?)\b", re.IGNORECASE)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]*")
CLAUSE_RE = re.compile(r"[!?;\n]|\.(?!\d)|(?<!\d)\.")  # a period between digits is a decimal
WINDOW = 6  # words


def concepts_of(words, *, labels: bool = False) -> set[str]:
    out = set()
    for w in words:
        w = w.lower()
        for c, rx in _CONCEPT_RE.items():
            if rx.fullmatch(w):
                out.add(c)
        if labels:
            for c, rx in LABEL_ALIASES.items():
                if rx.fullmatch(w):
                    out.add(c)
    return out


def _words(text: str) -> list[str]:
    toks = []
    for part in re.split(r"[_\s/():,]+", str(text)):
        toks += WORD_RE.findall(part)
    return toks


def labelled_numbers(obj) -> list[tuple[float, frozenset]]:
    """(value, concepts) for every number in a tool output. A number's concepts come from its key
    path, the short text fields beside it, and the output's top-level short text fields; numbers
    inside longer text get the words around them."""
    if isinstance(obj, str):
        obj = json.loads(obj) if obj.startswith(("{", "[")) else {"text": obj}
    root_words = []
    if isinstance(obj, dict):
        root_words = [w for v in obj.values() if isinstance(v, str) and len(v) <= 60
                      for w in _words(v)]
    found: list[tuple[float, frozenset]] = []

    def walk(x, path, sibling_words):
        if isinstance(x, bool) or x is None:
            return
        base = path + sibling_words + root_words
        if isinstance(x, (int, float)):
            found.append((float(x), frozenset(concepts_of(base, labels=True))))
        elif isinstance(x, str):
            words = _words(x)
            for m in re.finditer(r"-?\d[\d,]*\.?\d*", x):
                try:
                    v = float(m.group().replace(",", "").rstrip("."))
                except ValueError:
                    continue
                before = _words(x[:m.start()])[-WINDOW:]
                after = _words(x[m.end():])[:WINDOW]
                local = before + after if len(x) > 60 else words
                found.append((v, frozenset(concepts_of(path + local + root_words, labels=True))))
        elif isinstance(x, dict):
            sib = [w for v in x.values() if isinstance(v, str) and len(v) <= 60 for w in _words(v)]
            for k, v in x.items():
                walk(v, path + _words(k), sib)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v, path, sibling_words)
    walk(obj, [], [])
    return found


SUBCLAUSE_RE = re.compile(r"[(),:;]|\s[-\u2013\u2014]\s")


# A dollar amount cannot be a count, a probability, an elasticity or a duration.
NOT_MONEY = {"demand", "probability", "elasticity", "duration", "accuracy", "unemployment"}


def _concept(word: str, money: bool) -> str | None:
    c = concepts_of([word])
    if money:
        c -= NOT_MONEY
    return _priority(c) if c else None


def _first_concept(words, money: bool = False) -> str | None:
    for w in words:
        c = _concept(w, money)
        if c:
            return c
    return None


def nearest_concept(reply: str, start: int, end: int, money: bool = False) -> str | None:
    """Concept a number refers to: the closest concept word in the number's own phrase (between
    commas, parentheses or colons); otherwise the closest one before it in the sentence; otherwise
    the closest one after it. At most WINDOW words away."""
    if not money and DURATION_RE.match(reply, end):  # "28 days", "28-day": a length of time
        return "duration"
    cs = [m.end() for m in CLAUSE_RE.finditer(reply[:start])]
    c_start = cs[-1] if cs else 0
    nxt = CLAUSE_RE.search(reply, end)
    c_end = nxt.start() if nxt else len(reply)
    ss = [m.end() for m in SUBCLAUSE_RE.finditer(reply, c_start, start)]
    s_start = ss[-1] if ss else c_start
    snx = SUBCLAUSE_RE.search(reply, end, c_end)
    s_end = snx.start() if snx else c_end
    before = _words(reply[s_start:start])[::-1][:WINDOW]
    after = _words(reply[end:s_end])[:WINDOW]
    for i in range(WINDOW):  # own phrase, nearest first; ties go to the word before
        for side in (before, after):
            if i < len(side) and _concept(side[i], money):
                return _concept(side[i], money)
    c = _first_concept(_words(reply[c_start:s_start])[::-1][:WINDOW], money)
    if c:
        return c
    return _first_concept(_words(reply[s_end:c_end])[:WINDOW], money)


def _priority(cs: set[str]) -> str:
    order = list(CONCEPTS)
    return min(cs, key=order.index)


def check_numbers(reply: str, tool_outputs: list, user_texts: list[str]) -> list[dict]:
    """Every number in ``reply`` with its verdict: grounded / no_match / context_mismatch."""
    labelled: list[tuple[float, frozenset]] = []
    for o in tool_outputs:
        labelled += labelled_numbers(o)
    user_vals: set[float] = set()
    for u in user_texts:
        user_vals |= {abs(m.value) for m in _mentions(u)}
        user_vals |= {abs(m.value) / 100 for m in _mentions(u) if m.is_pct}
    allowed = {v for v, _ in labelled}
    results = []
    cleaned = ID_RE.sub(lambda m: " " * len(m.group()), DATE_RE.sub(lambda m: " " * len(m.group()), reply))
    for m in NUM_RE.finditer(cleaned):
        men = _mentions(m.group(0))[0]
        if not men.is_pct and "$" not in men.text and float(men.value).is_integer() and 0 <= men.value <= 10:
            continue
        rec = {"text": men.text, "concept": None, "verdict": "grounded"}
        if _matches(men, user_vals):
            results.append(rec)
            continue
        if not _matches(men, allowed):
            rec["verdict"] = "no_match"
            results.append(rec)
            continue
        concept = nearest_concept(reply, m.start(), m.end(), money="$" in men.text)
        rec["concept"] = concept
        if concept is not None:
            ok = any(concept in cs for v, cs in labelled if _matches(men, {v}))
            if not ok:
                rec["verdict"] = "context_mismatch"
        results.append(rec)
    return results


def ungrounded_numbers(reply: str, tool_outputs: list, user_texts: list[str]) -> list[str]:
    """Numbers that fail the check, as display strings (context failures name the concept)."""
    out = []
    for r in check_numbers(reply, tool_outputs, user_texts):
        if r["verdict"] == "no_match":
            out.append(r["text"])
        elif r["verdict"] == "context_mismatch":
            out.append(f"{r['text']} (no tool value for '{r['concept']}')")
    return out
