"""Decision agent: answers business questions ONLY through executed tools.

Interchangeable modes behind one interface (``DecisionAgent.ask``):
  * "gemini"  - Google Gemini (GEMINI_MODEL, default gemini-3.8-flash) via the official google-genai
                SDK with manual function calling (see ``gemini.py``). Default when GEMINI_API_KEY is
                set. API, quota or timeout failures fall back to the offline analyst, visibly.
  * "claude"  - optional: Claude (claude-opus-5-5) with tool use, used only when chosen via
                MSRD_LLM_PROVIDER=claude or when no Gemini key exists but an Anthropic credential does
                (ANTHROPIC_API_KEY or another credential the SDK resolves). Manual agentic loop:
                every tool_use block is executed against ``BusinessSession``; results are returned
                in one user message; the loop ends at end_turn.
  * "offline" - a deterministic rule-based analyst (NO language model) for demos and tests without
                credentials. It maps common questions to the same tools and fills template text
                with tool outputs.
Both modes run the grounding check: any number in the reply that is not in a tool output (or in
the user's own words) is flagged. In Claude mode the reply is regenerated once with the flagged
numbers listed; if numbers remain ungrounded the reply is returned with a visible warning.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import ClassVar

from .grounding import ungrounded_numbers
from .tools import TOOL_SCHEMAS, BusinessSession, run_tool

MODEL = os.getenv("MSRD_AGENT_MODEL", "claude-opus-5-5")
ANSWER_DEADLINE_S = float(os.getenv("MSRD_ANSWER_DEADLINE_S", "40"))
# Routing: "fast" = verified fast answers and deterministic engines first, a language model only for
# open questions (default); "llm" = always the language model's tool loop (used by provider tests).
EXPLAIN_TOOLS = {"simulate_product_scenario", "evaluate_restaurant_actions", "evaluate_inventory_policies",
                 "explain_profit_change", "get_daily_briefing", "get_local_market_prices",
                 "get_product_performance", "assess_cash_risk", "forecast_business"}
MAX_TOOL_ROUNDS = 8

SYSTEM_PROMPT = """You are Vizor AI, the financial analyst inside Vizor, a decision \
tool for U.S. small businesses (restaurants and shops). You talk with the owner in plain, calm \
language, like a trusted bookkeeper who also knows the numbers. You explain results; you never \
produce them yourself.

Rules you must follow:
1. Every number you state (money, percentages, probabilities, forecasts) must be copied from a \
tool result in this conversation or from the owner's message. If you have not run the tool that \
produces a number, run it or say you do not have it. Never estimate, round loosely, or invent \
figures.
2. For what-if questions, call the scenario tool. Follow-ups refer to the conversation: "what \
about 3% instead?" means the same product and the same kind of change with the new value; \
"which is safer?" compares the scenarios already run (higher chance of beating the current plan \
and a smaller possible loss is safer). Pass null for anything the owner did not change.
3. When you suggest an action, say: what to do, why it might help, the expected change versus \
keeping the current plan with its likely range, the key assumptions, what could go wrong, and \
what to check first. If no action beats the current plan, say so.
4. Be honest about where numbers come from: the business data is SYNTHETIC demo data unless the \
tool result says otherwise; past figures are observed records; forecasts are model estimates \
with measured accuracy; scenario results are simulations under stated assumptions (price \
sensitivity is assumed, not measured) and are not proven causal effects. Competitor menu prices \
show position only and are never by themselves a reason to change a price. Comparisons with a \
typical day describe what changed, not why.
5. If required information is missing (for example a starting cash balance or a budget), ask \
for it instead of assuming.
6. Every action needs the owner's approval. Never claim to have changed prices, placed orders, \
moved money, contacted anyone, or sent messages.
7. National economic indicators are not part of this product; if asked, say so and offer to \
test a supplier cost change for the owner's own business instead.
Avoid jargon: say "price sensitivity" rather than "elasticity" unless the owner uses the word, \
"likely range" rather than "90% interval" (you may add "9 in 10 simulated months"). Keep answers \
short: a direct answer first, then two to four supporting facts."""


@dataclass
class AgentReply:
    text: str
    mode: str
    tool_calls: list[dict] = field(default_factory=list)
    ungrounded: list[str] = field(default_factory=list)
    grounded: bool = True
    retried: bool = False
    fallback_reason: str | None = None
    notice: str = ""  # system notice (e.g. provider fallback); not analysis, not grounding-checked
    path: str = ""    # fast | engine | engine+gemini | gemini | claude | help (how it was answered)
    elapsed_s: float = 0.0


CHANNEL_NOTES = {
    "chat": "",
    "imessage": ("\nYou are replying by iMessage/SMS to the business owner's phone. Answer in at "
                 "most 600 characters of plain text (no markdown tables), lead with the "
                 "recommendation and its change versus doing nothing, then one line on "
                 "uncertainty. Do not include account numbers or full financial statements."),
}

ANALYST_LABELS = {"gemini": "Gemini analyst", "claude": "Claude analyst",
                  "offline": "Offline analyst (rule-based, no language model)"}


def _has_anthropic_credentials() -> bool:
    return any(os.getenv(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"))


def _resolve_mode(client) -> str:
    """Explicit client > MSRD_LLM_PROVIDER > available key (Gemini first) > offline."""
    if client is not None:
        models = getattr(client, "models", None)
        return "gemini" if hasattr(models, "generate_content") else "claude"
    forced = os.getenv("MSRD_LLM_PROVIDER", "").lower()
    if forced in ("gemini", "claude", "offline"):
        return forced
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if _has_anthropic_credentials():
        return "claude"
    return "offline"


class DecisionAgent:
    def __init__(self, session: BusinessSession, *, client=None, mode: str | None = None,
                 channel: str = "chat"):
        self.session = session
        self.mode = mode or _resolve_mode(client)
        self.channel = channel
        self.client = client
        self.system_prompt = SYSTEM_PROMPT + CHANNEL_NOTES.get(channel, "")
        self.gemini = None
        if self.mode == "gemini":
            from .gemini import GeminiProvider
            self.gemini = GeminiProvider(session, self.system_prompt, client=client)
        if self.mode == "claude" and self.client is None:
            import anthropic
            self.client = anthropic.Anthropic()
        self.messages: list[dict] = []        # Claude conversation (full content blocks)
        self.tool_outputs: list[dict] = []    # every tool result this conversation
        self.user_texts: list[str] = []
        self.planner = OfflinePlanner(session)
        self.last_fallback: str | None = None
        self.routing = os.getenv("MSRD_ROUTING", "fast")
        self.explain = os.getenv("MSRD_GEMINI_EXPLAIN", "1") == "1"

    @property
    def label(self) -> str:
        return ANALYST_LABELS[self.mode]

    # ------------------------------------------------------------------ public
    def ask(self, question: str, context: dict | None = None) -> AgentReply:
        """``context``: optional dashboard context, e.g. {"page": "Decision Lab", "product": "tacos"}.
        It steers which product a follow-up refers to; it never supplies numbers."""
        t0 = time.monotonic()
        deadline = t0 + ANSWER_DEADLINE_S
        self.user_texts.append(question)
        self.planner.context = context or {}
        reply = self._route(question, context, deadline) if self.routing == "fast" else None
        if reply is not None:
            pass
        elif self.mode == "offline":
            reply = self._offline(question)
        elif self.mode == "gemini":
            try:
                reply = self._ask_gemini(_with_context(question, context), deadline)
            except Exception as exc:  # noqa: BLE001 - any provider failure must degrade, not crash
                from .gemini import GeminiUnavailable
                reason = str(exc) if isinstance(exc, GeminiUnavailable) else type(exc).__name__
                self.last_fallback = reason
                reply = self._offline(question)
                if reply.path == "help":  # never leave the owner with a menu of commands
                    calls: list[dict] = []
                    k = self.planner._call(calls, "get_key_numbers", {})
                    self.tool_outputs.append(k)
                    reply = AgentReply("I couldn't reach the AI model for a free-form answer just now, so here "
                                       "are your key numbers.\n" + self.planner._fast_text("month", "", k) + "\n"
                                       + self.planner._fast_text("yesterday", "", k), "offline", calls, path="fast")
                reply.fallback_reason = reason
                reply.notice = f"(Gemini unavailable: {reason}. Answered by the offline analyst.)"
        else:
            reply = self._ask_claude(_with_context(question, context))
        bad = ungrounded_numbers(reply.text, self.tool_outputs, self.user_texts)
        if bad and reply.mode in ("claude", "gemini"):
            try:
                reply = self._regenerate(reply, bad)
            except Exception as exc:  # noqa: BLE001 - keep the first reply, flagged below
                reply.fallback_reason = f"grounding retry failed: {type(exc).__name__}"
            bad = ungrounded_numbers(reply.text, self.tool_outputs, self.user_texts)
        reply.ungrounded, reply.grounded = bad, not bad
        if bad:
            reply.text += ("\n\n⚠️ Unverified numbers (not found in any tool result): "
                           + ", ".join(bad) + ". Treat them as unreliable.")
        if reply.notice:
            reply.text = reply.notice + "\n" + reply.text
        reply.elapsed_s = round(time.monotonic() - t0, 3)
        return reply

    def _route(self, question: str, context: dict | None, deadline: float) -> AgentReply | None:
        """Verified fast answers and deterministic engines first. Returns None when the question
        needs the language model (open questions the rule-based analyst does not recognize)."""
        reply = self.planner.fast(question)
        if reply is None:
            planned = self.planner.answer(question)
            if planned.path == "help" and self.mode in ("gemini", "claude"):
                return None
            reply = planned
            reply.path = reply.path or "engine"
        else:
            reply.path = "fast"
        self.tool_outputs += [c["output"] for c in reply.tool_calls if "output" in c]
        if (self.mode == "gemini" and self.explain and reply.path == "engine"
                and {c["name"] for c in reply.tool_calls} & EXPLAIN_TOOLS):
            try:
                outs = [c["output"] for c in reply.tool_calls if "output" in c]
                text = self.gemini.explain(_with_context(question, context), outs, reply.text, deadline)
                if not ungrounded_numbers(text, self.tool_outputs, self.user_texts):
                    return AgentReply(text, "gemini", reply.tool_calls, path="engine+gemini")
                reply.fallback_reason = "explanation had unverified numbers; showing the calculation"
            except Exception as exc:  # noqa: BLE001 - the verified engine answer is still complete
                self.last_fallback = str(exc)
                reply.fallback_reason = str(exc)
        elif self.gemini is not None:
            self.gemini.remember(question, reply.text)
        return reply

    def _offline(self, question: str) -> AgentReply:
        reply = self.planner.answer(question)
        self.tool_outputs += [c["output"] for c in reply.tool_calls if "output" in c]
        return reply

    # ------------------------------------------------------------------ Gemini mode
    def _ask_gemini(self, question: str, deadline: float | None = None) -> AgentReply:
        self.gemini.add_user_text(question)
        text, calls = self.gemini.run(self.tool_outputs, deadline)
        return AgentReply(text, "gemini", calls, path="gemini")

    # ------------------------------------------------------------------ Claude mode
    def _create(self):
        return self.client.beta.messages.create(
            model=MODEL, max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "medium"},
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=[{**t, "strict": True} for t in TOOL_SCHEMAS],
            messages=self.messages)

    def _loop(self) -> tuple[str, list[dict]]:
        calls = []
        for _ in range(MAX_TOOL_ROUNDS):
            response = self._create()
            self.messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "refusal":
                return ("The model declined this request. No analysis was produced.", calls)
            if response.stop_reason == "pause_turn":
                continue
            uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if response.stop_reason != "tool_use" or not uses:
                text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
                if response.stop_reason == "max_tokens":
                    text += "\n[Reply truncated at the token limit.]"
                return text.strip(), calls
            results = []
            for b in uses:
                try:
                    out = run_tool(self.session, b.name, dict(b.input))
                    self.tool_outputs.append(out)
                    calls.append({"name": b.name, "input": dict(b.input), "output": out})
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": json.dumps(out, default=str)})
                except (ValueError, KeyError, TypeError, RuntimeError) as exc:
                    # tool/connector errors go back to the model as is_error results
                    results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                    "content": f"{type(exc).__name__}: {exc}"})
                    calls.append({"name": b.name, "input": dict(b.input), "error": str(exc)})
            self.messages.append({"role": "user", "content": results})
        return "I stopped after the maximum number of tool rounds without a final answer.", calls

    def _ask_claude(self, question: str) -> AgentReply:
        self.messages.append({"role": "user", "content": question})
        text, calls = self._loop()
        return AgentReply(text, "claude", calls)

    def _regenerate(self, reply: AgentReply, bad: list[str]) -> AgentReply:
        note = ("[Automatic grounding check] Your last reply contains numbers that do not appear "
                f"in any tool result: {', '.join(bad)}. Rewrite the reply using only numbers "
                "copied from tool results (call a tool if you need a number). Do not mention "
                "this check.")
        if reply.mode == "gemini":
            self.gemini.add_user_text(note)
            text, calls = self.gemini.run(self.tool_outputs)
            return AgentReply(text, "gemini", reply.tool_calls + calls, retried=True)
        self.messages.append({"role": "user", "content": (
            "[Automatic grounding check] Your last reply contains numbers that do not appear in "
            f"any tool result: {', '.join(bad)}. Rewrite the reply using only numbers copied from "
            "tool results (call a tool if you need a number). Do not mention this check.")})
        text, calls = self._loop()
        return AgentReply(text, "claude", reply.tool_calls + calls, retried=True)


def _with_context(question: str, context: dict | None) -> str:
    if not context:
        return question
    bits = [f"{k}: {v}" for k, v in context.items() if v and k in ("page", "product", "section")]
    return (f"[Dashboard context (not a number source): {'; '.join(bits)}]\n" if bits else "") + question


# ====================================================================== offline analyst
def _money(x) -> str:
    """$1,234 / -$1,234 (the sign before the dollar sign, so the grounding check reads it)."""
    if x is None:
        return "n/a"
    return f"-${abs(x):,.0f}" if round(x) < 0 else f"${x:,.0f}"


class OfflinePlanner:
    """Deterministic, rule-based analyst used when no LLM credential is configured.

    It is NOT a language model: it recognizes a fixed set of question patterns, calls the same
    tools, and composes replies from templates filled with tool outputs."""

    NULL_RESTAURANT: ClassVar[dict] = {"food_cost_change_pct": None, "elasticity_mode": None, "elasticity_low": None,
                       "elasticity_high": None, "max_price_increase": None, "starting_cash": None,
                       "objective": "max_expected_profit"}
    NULL_RETAIL: ClassVar[dict] = {"purchase_budget_usd": None, "storage_units": None,
                   "max_stockout_probability": None, "objective": "max_expected_profit"}

    def __init__(self, session: BusinessSession):
        self.s = session
        self.context: dict = {}
        self.scenarios: list[tuple[dict, dict]] = []   # (inputs, output) of product scenarios run
        self.last_kind: str | None = None              # last what-if tool, for follow-ups
        self.last_food_pct: float | None = None

    def _call(self, calls, name, args):
        out = run_tool(self.s, name, args)
        calls.append({"name": name, "input": args, "output": out})
        if name == "simulate_product_scenario" and out.get("available"):
            self.scenarios.append((args, out))
            self.last_kind = name
        elif name == "evaluate_restaurant_actions":
            self.last_kind = name
        return out

    def _context_product(self) -> str | None:
        p = str(self.context.get("product") or "").strip().lower()
        p = {"taco": "tacos", "burrito": "burritos", "quesadilla": "quesadillas", "nacho": "nachos",
             "drink": "drinks"}.get(p, p)
        return p or None

    # ---------------------------------------------------------------- fast facts
    def fast(self, q: str) -> AgentReply | None:
        """Instant answers to the most common factual questions from verified records (one cached
        tool call, no simulation, no language model). None when the question is not one of them."""
        ql = q.lower().strip()
        if re.search(r"\d\s*%|percent|what if|instead|safer|should i|why|forecast|next (week|month)|"
                     r"outlook|run out|predict|compare|competitor|nearby", ql):
            return None
        intents = [
            ("yesterday", r"yesterday|last night"),
            ("cash", r"how much cash|cash (do|have|on hand|balance|in the bank)|our cash|cash position"),
            ("bills", r"bills?\b.*\b(due|coming|this week)|what do (we|i) owe|upcoming (bills|payments)"),
            ("best", (r"sold the most|best[- ]sell|most popular|top (seller|product|item)|sell(s|ing)? the most|"
                     r"most (orders|units)")),
            ("price", (r"(price of|price for|how much (do|does) (we|i) charge|how much (is|are)|current|what'?s (our|my|the)?)"
                      r".*\b(taco|burrito|quesadilla|nacho|drink)s?\b|\b(taco|burrito|quesadilla|nacho|drink)s?\b.*\bprices?\b")),
            ("month", (r"this month|so far|month to date|\bmtd\b|how (are|is) (we|my business|business) doing|"
                      r"how'?s business")),
        ]
        intent = next((name for name, rx in intents if re.search(rx, ql)), None)
        if intent is None:
            return None
        if intent == "price" and not getattr(self.s, "has_products", False):
            return None
        if intent == "best" and not getattr(self.s, "has_products", False):
            return None
        calls: list[dict] = []
        k = self._call(calls, "get_key_numbers", {})
        return AgentReply(self._fast_text(intent, ql, k), "offline", calls)

    def _fast_text(self, intent: str, ql: str, k: dict) -> str:
        y, m, c = k["yesterday"], k["month_to_date"], k["cash"]
        if intent == "yesterday":
            chg = y["revenue_change_vs_typical_pct"]
            rel = ("about the same as" if chg is None or abs(chg) < 3 else
                   f"{abs(chg):.0f}% {'below' if chg < 0 else 'above'}")
            text = (f"Yesterday ({y['weekday']} {y['date']}) sales were **{_money(y['revenue'])}**, {rel} a typical "
                    f"{y['weekday']} (typical revenue {_money(y['typical_same_weekday_revenue'])}). Operating profit "
                    f"was {_money(y['operating_profit'])}.")
            if y.get("customers") is not None:
                text += f" Customers: {y['customers']}."
            return text + " These are recorded figures from your records."
        if intent == "cash":
            if c.get("balance") is None:
                return ("No cash balance has been entered for this business, so cash on hand is unknown. Add your "
                        "current balance on the Data page; Vizor does not estimate it.")
            text = f"Cash on hand: **{_money(c['balance'])}** at the end of {y['date']}."
            b = k.get("bills_due_7_days")
            if b:
                text += (f" Bills due in the next 7 days: {_money(b['total'])} ({b['count']} bills), so cash "
                         f"{'covers' if (c['balance'] or 0) >= b['total'] else 'does NOT cover'} them.")
            return text + " For the 4-week outlook, ask “What's our cash outlook?”."
        if intent == "bills":
            b = k.get("bills_due_7_days")
            if not b:
                return "There is no bill schedule in this data."
            return (f"{b['count']} bills are due in the next 7 days, {_money(b['total'])} in total. Unpaid "
                    f"supplier invoices: {_money(b['unpaid_supplier_invoices'])}. Cash on hand: "
                    f"{_money(c['balance'])}.")
        if intent == "best":
            ps = sorted(k["products_last_28_days"], key=lambda r: -r["revenue"])
            top = ps[0]
            units = max(ps, key=lambda r: r["units_sold"])
            text = (f"Over the last 28 days, **{top['product']}** brought in the most sales: revenue "
                    f"{_money(top['revenue'])} from {top['units_sold']:,} orders sold.")
            if units["product"] != top["product"]:
                text += (f" {units['product']} sold the most units ({units['units_sold']:,} orders sold), at a "
                         f"lower price.")
            return text
        if intent == "price":
            prod = re.search(r"(taco|burrito|quesadilla|nacho|drink)s?", ql).group(1) + "s"
            r = next(x for x in k["products_last_28_days"] if x["product"].lower() == prod)
            return (f"Your current {r['product'].lower().rstrip('s')} price is **${r['price']:.2f}**. Over the last 28 days you "
                    f"sold {r['units_sold']:,} orders, revenue {_money(r['revenue'])}, with a "
                    f"{r['gross_margin_pct']}% margin after ingredients.")
        chg = m["revenue_change_pct"]
        return (f"This month so far: revenue **{_money(m['revenue'])}** "
                f"({'+' if (chg or 0) >= 0 else ''}{chg}% vs the same days last month), operating profit "
                f"{_money(m['operating_profit'])} ({m['operating_margin_pct']}% margin), expenses "
                f"{_money(m['expenses'])}." + (f" Cash on hand {_money(c['balance'])}." if c.get("balance") is not None
                                               else " No cash balance has been entered."))

    # ---------------------------------------------------------------- follow-ups
    LEVERS: ClassVar[tuple] = ("price_change_pct", "supply_change_pct", "ingredient_cost_change_pct",
                               "demand_shock_pct", "marketing_change_pct")

    def _followup(self, q: str, ql: str, pct, calls) -> AgentReply | None:
        named = re.search(self.PRODUCT_RE, ql)
        if re.search(r"which (one |option |scenario )?(is |was )?(safer|less risky|riskier|better)|"
                     r"compare (them|those|the two|both)|which should i (pick|choose)", ql):
            if len(self.scenarios) >= 2:
                return AgentReply(self._safer_text(self.scenarios[-2][1], self.scenarios[-1][1]),
                                  "offline", calls)
            return AgentReply("Offline analyst. I need two scenarios to compare. Try one change "
                              "(for example a price change), then another, then ask again.",
                              "offline", calls)
        if pct and not named and re.search(r"instead|what about|how about|and if|try|^and\b|"
                                           r"what if it", ql):
            v = float(pct.group(1))
            if re.search(r"decreas|lower|cut|reduc|drop|fall|less", ql):
                v = -abs(v)
            if self.last_kind == "simulate_product_scenario" and self.scenarios:
                args = dict(self.scenarios[-1][0])
                lever = next((k for k in self.LEVERS if args.get(k)), "price_change_pct")
                if v > 0 and (args.get(lever) or 0) < 0 and "-" not in pct.group(0):
                    v = -v  # "what about 3% instead" after a price cut keeps the direction
                args[lever] = v
                out = self._call(calls, "simulate_product_scenario", args)
                return AgentReply(self._product_scn_text(out), "offline", calls)
            if self.last_kind == "evaluate_restaurant_actions":
                out = self._call(calls, "evaluate_restaurant_actions",
                                 {**self.NULL_RESTAURANT, "food_cost_change_pct": v})
                return AgentReply(self._restaurant_text(out, q), "offline", calls)
        if re.search(r"why", ql) and re.search(r"demand|orders|fewer|sell less|sales", ql) and \
                re.search(r"price|rais|increase|go(es)? up", ql) and getattr(self.s, "has_products", False):
            prev = next((o for a, o in reversed(self.scenarios) if a.get("price_change_pct")), None)
            if prev is None:
                pid = (named.group(1) + "s") if named else (self._context_product() or "tacos")
                prev = self._call(calls, "simulate_product_scenario", self._scn(pid, price_change_pct=5.0))
            return AgentReply(self._why_price_text(prev), "offline", calls)
        return None

    @staticmethod
    def _safer_text(a: dict, b: dict) -> str:
        def key(o):
            return (o["probability_profit_improves_pct"], o["operating_profit"]["change_90pct_range"][0])
        safer, other = (a, b) if key(a) >= key(b) else (b, a)
        more = a if a["operating_profit"]["change"] >= b["operating_profit"]["change"] else b
        lines = [("Offline analyst (rule-based, no language model). Comparing your last two scenarios "
                 "(same simulated futures for both):")]
        for o in (a, b):
            p = o["operating_profit"]
            lo, hi = p["change_90pct_range"]
            lines.append(f"- **{o['scenario_text']}**: expected profit {_money(p['change'])} over "
                         f"{o['horizon_days']} days (likely range {_money(lo)} to {_money(hi)}); "
                         f"probability it beats changing nothing: {o['probability_profit_improves_pct']}%.")
        pa, pb = safer["probability_profit_improves_pct"], other["probability_profit_improves_pct"]
        la, lb = safer["operating_profit"]["change_90pct_range"][0], other["operating_profit"]["change_90pct_range"][0]
        if (pa, la) == (pb, lb):
            why = "they are equally risky by these measures"
        elif pa > pb:
            why = "it beats the current plan more often" + (" and its worst likely outcome is better" if la > lb else "")
        else:
            why = "same chance of beating the current plan, and its worst likely outcome is better"
        lines.append(f"Safer: {safer['scenario_text']} ({why}).")
        if more is not safer:
            lines.append(f"Higher expected profit: {more['scenario_text']}, with more risk.")
        lines.append("Both rest on assumed price sensitivity; neither is a guarantee. Your approval "
                     "is needed before any change.")
        return "\n".join(lines)

    @staticmethod
    def _why_price_text(o: dict) -> str:
        d = o["selected_product_demand_units"]
        a = o["assumptions"][0]
        return "\n".join([
            "Offline analyst (rule-based, no language model).",
            (f"In the scenario \"{o['scenario_text']}\", forecast demand for the product goes from "
            f"{d['baseline']:,} to {d['scenario']:,} units over {o['horizon_days']} days."),
            (f"That comes from an assumed price sensitivity ({a['name'].replace('_', ' ')} {a['value']}, "
            f"likely between {a['low']} and {a['high']}): when a price goes up, some customers order "
            "less or pick a different item; the more negative the number, the more orders drop."),
            ("It is an assumption, not something measured from your sales: your history has no "
            "item-level price changes to learn it from. A short price test would show how your "
            "customers really react."),
            (f"Profit can still rise if the higher price per order outweighs the lost orders: here "
            f"the profit change is {_money(o['operating_profit']['change'])}.")])

    def answer(self, q: str) -> AgentReply:
        ql = q.lower()
        calls: list[dict] = []
        pct = re.search(r"(-?\d+(?:\.\d+)?)\s*%", q)
        money = re.search(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k)?", q, re.IGNORECASE)
        amount = None if not money else float(money.group(1).replace(",", "")) * (1000 if money.group(2) else 1)
        try:
            if getattr(self.s, "has_products", False) and re.search(
                    r"competitor|nearby|local market|other restaurants|compare.*(price|menu)|"
                    r"market price|what do others charge|prices?\b.*\blocal(ly)?\b", ql):
                prod = re.search(self.PRODUCT_RE, ql)
                out = self._call(calls, "get_local_market_prices",
                                 {"product": (prod.group(1) + "s") if prod else None})
                return AgentReply(self._market_text(out), "offline", calls)
            follow = self._followup(q, ql, pct, calls)
            if follow is not None:
                return follow
            if re.search(r"inflation|\bcpi\b|econom|\bfred\b|unemployment|interest rate|indicator|"
                         r"price index", ql):
                return AgentReply(
                    "Offline analyst. National economic indicators are not part of Main Street Risk "
                    "Desk; it focuses on your own sales, costs, stock and cash. If supplier prices "
                    "are going up, I can test that for your business: ask for example \"what if "
                    "supplier costs rise eight percent?\"", "offline", calls)
            if re.search(r"brief|yesterday|what needs (my )?attention|today|focus on|tomorrow", ql):
                out = self._call(calls, "get_daily_briefing", {})
                return AgentReply(self._briefing_answer(out, ql), "offline", calls)
            routed = self._product_route(q, ql, pct, calls)
            if routed is not None:
                return routed
            if self.s.industry == "restaurant" and re.search(r"food|ingredient|supplier|cost", ql) and pct:
                sign = -1 if re.search(r"decrease|fall|drop|lower", ql) else 1
                out = self._call(calls, "evaluate_restaurant_actions",
                                 {**self.NULL_RESTAURANT, "food_cost_change_pct": sign * abs(float(pct.group(1)))})
                return AgentReply(self._restaurant_text(out, q), "offline", calls)
            if self.s.industry == "restaurant" and re.search(r"(more|less) (price[- ])?sensitive|elastic", ql):
                st = self.s.state
                shift = -0.4 if "less" not in ql else 0.3
                out = self._call(calls, "evaluate_restaurant_actions", {
                    **self.NULL_RESTAURANT, "elasticity_mode": round(st.elasticity_mode + shift, 2),
                    "elasticity_low": round(st.elasticity_low + shift, 2),
                    "elasticity_high": round(st.elasticity_high + shift, 2)})
                return AgentReply(self._restaurant_text(out, q), "offline", calls)
            if re.search(r"budget|inventory|reorder|stock", ql) and self.s.industry == "retail":
                out = self._call(calls, "evaluate_inventory_policies",
                                 {**self.NULL_RETAIL, "purchase_budget_usd": amount})
                return AgentReply(self._inventory_text(out), "offline", calls)
            if re.search(r"forecast|predict|next month|next 28|demand", ql):
                target = "units_sold" if re.search(r"demand|units|covers|traffic", ql) else "revenue"
                out = self._call(calls, "forecast_business", {"target": target})
                return AgentReply(self._forecast_text(out), "offline", calls)
            if re.search(r"cash|run out|shortfall|liquidity", ql):
                out = self._call(calls, "assess_cash_risk", {"starting_cash": amount})
                return AgentReply(self._cash_text(out), "offline", calls)
            if re.search(r"assumption", ql):
                out = self._call(calls, "get_assumptions", {})
                return AgentReply("Current assumptions:\n" + json.dumps(out["scenario_state"], indent=1),
                                  "offline", calls)
            if re.search(r"overview|how .*doing|summary|performance", ql):
                out = self._call(calls, "get_business_overview", {"period_days": 28})
                return AgentReply(self._overview_text(out), "offline", calls)
            if re.search(r"what should i do|options?\b|recommend|compare|which action|decision|"
                         r"reduce (the )?risk", ql):
                # Rank actions under the scenario already in memory (no new assumptions).
                if self.s.industry == "restaurant":
                    out = self._call(calls, "evaluate_restaurant_actions", dict(self.NULL_RESTAURANT))
                    return AgentReply(self._restaurant_text(out, q), "offline", calls)
                out = self._call(calls, "evaluate_inventory_policies", dict(self.NULL_RETAIL))
                return AgentReply(self._inventory_text(out), "offline", calls)
        except ValueError as exc:
            return AgentReply(f"I can't run that analysis: {exc}", "offline", calls)
        return AgentReply(
            "Offline analyst (rule-based, no language model). I can answer: business overview, "
            "today's briefing, four-week forecasts, cash-shortfall risk, restaurant "
            "what-ifs (food costs rising by a stated percent, demand becoming more "
            "price-sensitive), inventory budgets (a stated dollar budget), 'what should I "
            "do?', and for the demo restaurant: which product is most profitable, why profit "
            "changed, what a product price change would do (e.g. 'what if burrito prices rise five "
            "percent?' with a number) and "
            "whether to buy more of a product's ingredients, supplier cost increases, and "
            "follow-ups like 'what about three percent instead?' or 'which is safer?'. Set GEMINI_API_KEY for "
            "free-form questions.", "offline", calls, path="help")

    @staticmethod
    def _briefing_answer(out: dict, ql: str) -> str:
        head = "Offline analyst (rule-based, no language model)."
        lines = out["text"].split("\n")
        if re.search(r"why", ql) and re.search(r"yesterday|fell|drop|down|low", ql):
            y = out["yesterday"]
            body = [lines[1]]
            drivers = [a for a in out["alerts"] if a["kind"] in ("sales_decline", "product_mix")]
            body += [f"- {a['title']}: {a['detail']}" for a in drivers] or [
                "Nothing stood out beyond normal day-to-day variation."]
            if y.get("guests_change_pct") is not None and drivers:
                body.append("These are comparisons with a typical day: they show what moved, not "
                            "the cause (weather, events or staffing are not in the data).")
            return "\n".join([head, *body])
        if re.search(r"focus|tomorrow|attention|today", ql):
            items = [f"- {a['title']}. Next step: {a['next_step']}" for a in out["alerts"]]
            body = ["What to focus on:", *(items or ["- Nothing urgent is flagged."])]
            if out.get("action"):
                from ..briefing import checklist_text
                body += checklist_text(out["action"])
            return "\n".join([head, *body])
        return head + "\n" + out["text"]

    # ---------------------------------------------------------------- product questions
    PRODUCT_RE: ClassVar[str] = r"(taco|burrito|quesadilla|nacho|drink)s?"

    def _product_route(self, q: str, ql: str, pct, calls) -> AgentReply | None:
        if not getattr(self.s, "has_products", False):
            if re.search(self.PRODUCT_RE + r"|which product|most profitable|best[- ]sell", ql):
                out = self._call(calls, "get_product_performance", {"period_days": 28})
                return AgentReply("Offline analyst. " + out.get("reason", "") + " Needed: "
                                  + out.get("what_is_needed", ""), "offline", calls)
            return None
        prod = re.search(self.PRODUCT_RE, ql)
        pid = (prod.group(1) + "s") if prod else self._context_product()
        down = bool(re.search(r"decreas|lower|cut|reduc|drop|fall|less", ql))
        val = None if not pct else (-1 if down else 1) * abs(float(pct.group(1)))
        if val is not None and re.search(r"supplier|ingredient (cost|price)s?|wholesale|vendor", ql) and \
                re.search(r"cost|price|charg|increase|rise|up", ql):
            out = self._call(calls, "simulate_product_scenario", self._scn(
                pid or self._context_product() or "tacos", ingredient_cost_change_pct=val))
            return AgentReply(self._product_scn_text(out), "offline", calls)
        if re.search(r"why .*profit|profit .*(drop|fall|fell|declin|down|change|up|rise|rose|grew)", ql):
            out = self._call(calls, "explain_profit_change", {"period_days": 28})
            return AgentReply(self._profit_change_text(out), "offline", calls)
        if pid and val is not None and re.search(r"price|charge|cost more|menu", ql):
            out = self._call(calls, "simulate_product_scenario",
                             self._scn(pid, price_change_pct=val))
            return AgentReply(self._product_scn_text(out), "offline", calls)
        if pid and re.search(r"purchas|buy|order|ingredient|restock|supply|more stock|inventory", ql):
            if val is not None:
                out = self._call(calls, "simulate_product_scenario",
                                 self._scn(pid, supply_change_pct=val))
            else:
                n = re.search(r"(\d[\d,]*)\s*(extra|more|additional)?\s*" + self.PRODUCT_RE, ql)
                if n:  # a one-time purchase of a stated quantity
                    out = self._call(calls, "simulate_product_scenario", self._scn(
                        pid, extra_units=float(n.group(1).replace(",", "")), horizon_days=7))
                else:  # raise this product's share of the planned orders by 25%
                    out = self._call(calls, "simulate_product_scenario",
                                     self._scn(pid, supply_change_pct=25.0))
            return AgentReply(self._product_scn_text(out), "offline", calls)
        if val is not None and re.search(r"demand|customers|traffic|sales (rise|fall|drop|increase|decrease)", ql):
            out = self._call(calls, "simulate_product_scenario", self._scn(
                pid or "tacos", demand_shock_pct=val, demand_shock_scope="product" if pid else "all"))
            return AgentReply(self._product_scn_text(out), "offline", calls)
        if val is not None and re.search(r"marketing|advertis", ql):
            out = self._call(calls, "simulate_product_scenario",
                             self._scn(pid or "tacos", marketing_change_pct=val))
            return AgentReply(self._product_scn_text(out), "offline", calls)
        if re.search(r"which product|most profitable|best[- ]sell|top (product|seller)|lowest margin|"
                     r"products?\b.*(perform|margin|stock)|" + self.PRODUCT_RE, ql):
            out = self._call(calls, "get_product_performance", {"period_days": 28})
            return AgentReply(self._product_perf_text(out), "offline", calls)
        return None

    @staticmethod
    def _market_text(out) -> str:
        if not out.get("available"):
            return "Offline analyst. " + out.get("reason", "")
        head = (f"Offline analyst (rule-based, no language model). Local menu prices, {out['market']} "
                f"(public official menus, verified and dated). Your {out['our_serving']}: "
                f"${out['our_price']:.2f}.")
        if not out["sufficient"]:
            body = (f"Only {out['peer_count']} verified comparable(s), so there is no reliable local "
                    "benchmark for this item yet.")
        else:
            body = (f"{out['peer_count']} verified comparables range from ${out['peer_min']:.2f} to "
                    f"${out['peer_max']:.2f} (median ${out['peer_median']:.2f}); your price is "
                    f"{out['position']}.")
        lines = [head, body]
        for c in out["comparables"]:
            lines.append(f"  {c['restaurant']} ({c['type']}): {c['item']} ${c['listed_price']:.2f} "
                         f"= ${c['price_for_our_serving']:.2f} for your serving; verified {c['verified_on']}.")
        lines += out["warnings"] + [out["not_a_recommendation"]]
        return "\n".join(lines)

    @staticmethod
    def _scn(pid, **kw) -> dict:
        base = {"product": pid, "price_change_pct": 0.0, "supply_change_pct": 0.0,
                "extra_units": 0.0, "demand_shock_pct": 0.0, "demand_shock_scope": "product",
                "marketing_change_pct": 0.0, "horizon_days": 28, "ingredient_cost_change_pct": 0.0}
        return base | kw

    @staticmethod
    def _product_perf_text(out) -> str:
        if not out.get("available"):
            return "Offline analyst. " + out["reason"]
        lines = ["Offline analyst (rule-based, no language model).",
                 f"Your five main items, {out['period']}:"]
        for r in out["products"]:
            lines.append(f"- **{r['product']}**: {r['units_sold']:,} orders sold, ${r['revenue']:,} in sales, "
                         f"${r['gross_profit']:,} profit after ingredients ({r['gross_margin_pct']}% margin). "
                         f"About {r['forecast_demand_next_7_days']:,} orders expected next week; stock lasts "
                         f"{r['stock_days_of_supply']} days; sell-out risk {r['stockout_risk_next_7_days_pct']}%.")
        lines.append(f"Most profitable: **{out['most_profitable_by_gross_profit']}**. Best seller by sales: "
                     f"{out['best_seller_by_revenue']}; most orders: {out['most_units_sold']}. Lowest margin: "
                     f"{out['lowest_gross_margin']}.")
        lines.append("'Profit after ingredients' is before staff, rent and other costs. Orders expected next "
                     "week are a forecast from your past sales.")
        return "\n".join(lines)

    @staticmethod
    def _product_scn_text(out) -> str:
        if not out.get("available"):
            return "Offline analyst. " + out["reason"]
        r, p, d, u = (out["revenue"], out["operating_profit"], out["selected_product_demand_units"],
                      out["selected_product_units_sold"])
        so = out["selected_product_stockout_risk_pct"]
        lo, hi = p["change_90pct_range"]
        a = out["assumptions"][0]
        policy = ("Within the owner's approved limits." if out["within_owner_policy"] else
                  "OUTSIDE the owner's approved limits: " + "; ".join(out["policy_violations"]) + ".")
        return "\n".join([
            "Offline analyst (rule-based, no language model).",
            (f"**{out['scenario_text']}**, next {out['horizon_days']} days ({out['forecast_window'][0]} to "
            f"{out['forecast_window'][1]}), compared with changing nothing:"),
            (f"- Expected profit: **{_money(p['change'])}** (most likely between {_money(lo)} and {_money(hi)}; "
            f"better than changing nothing in {out['probability_profit_improves_pct']}% of our test runs)"),
            f"- Sales: {_money(r['baseline'])} → {_money(r['scenario'])} ({_money(r['change'])})",
            (f"- Orders for this item: {d['baseline']:,} → {d['scenario']:,}; units sold {u['baseline']:,} → "
            f"{u['scenario']:,}"),
            f"- Sell-out risk: {so['baseline']}% → {so['scenario']}%",
            f"- Cash over the period: {_money(out['operating_cash_flow']['change'])}",
            policy, "Recommendation: " + out["recommendation"],
            (f"How strongly customers react to price is our estimate ({a['value']}, between {a['low']} and "
            f"{a['high']}), not measured from your sales. Not a guarantee; your approval is needed before any "
            "change.")])

    @staticmethod
    def _profit_change_text(out) -> str:
        pr = out["profit"]
        lines = ["Offline analyst (rule-based, no language model).",
                 (f"Operating profit {out['period']}: {_money(pr['current'])} versus "
                 f"{_money(pr['previous'])} in the {out['comparison']} (change {_money(pr['change'])})."),
                 "Largest effects on profit:"]
        for d in out["drivers"][:5]:
            lines.append(f"  {d['driver']}: {_money(d['previous'])} -> {_money(d['current'])} "
                         f"(profit effect {_money(d['profit_effect'])})")
        if out["products"]:
            lines.append("Product revenue changes: " + "; ".join(
                f"{x['product']} {_money(x['revenue_change'])} ({x['units_change']:+,} units)"
                for x in sorted(out["products"], key=lambda x: -abs(x["revenue_change"]))))
        lines.append(out["note"])
        return "\n".join(lines)

    # ---------------------------------------------------------------- templates
    @staticmethod
    def _actions_lines(out, metric="profit"):
        lines = []
        for a in out["actions"]:
            tag = f"#{a['rank']}" if a["rank"] else "excluded"
            why = f" (excluded: {'; '.join(a['violations'])})" if a["violations"] else ""
            r = a["change_90pct_range"]
            lines.append(f"  {tag} {a['label']}: expected {metric} {_money(a.get('expected_' + metric))}, "
                         f"change vs no action {_money(a['change_vs_no_action'])} "
                         f"(90% range {_money(r[0])} to {_money(r[1])}){why}")
        return "\n".join(lines)

    def _restaurant_text(self, out, q):
        sc = out["scenario"]
        e = sc["price_elasticity"]
        rec = out["recommended_action"]
        head = (f"Recommendation (objective: {out['objective']}, next {out['horizon_days']} days): "
                + (f"**{rec}** — strength: {out['strength']}." if rec else
                   f"no defensible recommendation ({out['strength']})."))
        return "\n".join([
            "Offline analyst (rule-based, no language model).", head,
            (f"Scenario: food/variable costs {sc['food_cost_change_pct']:+g}% "
            f"({sc['food_cost_change_type']}); price elasticity {e['mode']} (range {e['low']} to "
            f"{e['high']}, {e['source']}). Baseline expected profit: {_money(out['baseline_expected_profit'])}."),
            self._actions_lines(out), out["rationale"],
            "Limitations: " + " ".join(out["limitations"][:2]),
            out["note_on_probabilities"] + " Business data: " + out["data_label"] + ". "
            "Action effects are modeled assumptions, not proven causal effects. Human approval required."])

    def _inventory_text(self, out):
        if not out.get("supported", True):
            return f"Offline analyst: inventory analysis unsupported — {out.get('reason')}"
        sc = out["scenario"]
        rec = out["recommended_action"]
        budget = _money(sc["purchase_budget_usd"]) if sc["purchase_budget_usd"] else "none declared"
        head = (f"Recommendation (objective: {out['objective']}, next {out['horizon_days']} days, "
                f"purchase budget {budget}): "
                + (f"**{rec}** — strength: {out['strength']}." if rec else
                   f"no feasible policy under the declared constraints ({out['strength']})."))
        return "\n".join(["Offline analyst (rule-based, no language model).", head,
                          self._actions_lines(out), out["rationale"],
                          out["note_on_probabilities"] + " Business data: " + out["data_label"] + "."])

    @staticmethod
    def _econ_text(out):
        lines = [("Offline analyst. Observed U.S. economic data (FRED, as published by "
                 f"{out['as_of']}):")]
        for o in out["observations"]:
            lines.append(f"  {o['series_id']} ({o['title']}): {o['value']} {o['units']} for "
                         f"{o['observed_at']}, 12-month change {o['change_12m']} "
                         f"{o['change_12m_unit']} ({o['change_12m_period']}); published "
                         f"{o['published_at']}; retrieval: {o['retrieval']}.")
        lines += out["notes"] + [out["not_used_in_forecasts"]]
        return "\n".join(lines)

    @staticmethod
    def _forecast_text(out):
        lo, hi = out["interval_80_total"]
        v = out.get("interval_80_total_validation") or {}
        return (f"Offline analyst. {out['target']} forecast {out['forecast_start']} to "
                f"{out['forecast_end']}: {out['point_total']:,.0f} (80% range for the total "
                f"{lo:,.0f} to {hi:,.0f}) from {out['selected_model']}. Measured accuracy: "
                f"validation WAPE {out['validation']['wape']}, final-test WAPE "
                f"{out['final_test']['wape']}. Range check: {v.get('summary', 'not validated')}. "
                + out["caveat"])

    @staticmethod
    def _cash_text(out):
        if not out["available"]:
            return f"Offline analyst. Cash risk needs a starting balance: {out['reason']}."
        lo, hi = out["ending_cash_80pct_interval"]
        return (f"Offline analyst. Starting cash {_money(out['starting_cash'])} "
                f"({out['starting_cash_source']}). Simulated {out['horizon_days']}-day shortfall "
                f"frequency {out['simulated_shortfall_frequency']}; median ending cash "
                f"{_money(out['median_ending_cash'])} (80% range {_money(lo)} to {_money(hi)}). "
                f"{out['calibration']}. {out['warning']}")

    @staticmethod
    def _overview_text(out):
        c = out["current"]
        return (f"Offline analyst. Last {out['period_days']} days ({out['period'][0]} to "
                f"{out['period'][1]}): revenue {_money(c['revenue'])}, operating profit "
                f"{_money(c['operating_profit'])} ({c['operating_margin_pct']}% margin). Cash "
                f"{_money(out['cash']['balance'])} ({out['cash']['source']}). Data: {out['data_label']}.")
