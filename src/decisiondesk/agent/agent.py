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
from dataclasses import dataclass, field
from typing import ClassVar

from .grounding import ungrounded_numbers
from .tools import TOOL_SCHEMAS, BusinessSession, run_tool

MODEL = os.getenv("MSRD_AGENT_MODEL", "claude-opus-5-5")
MAX_TOOL_ROUNDS = 8

SYSTEM_PROMPT = """You are the analyst inside Main Street Risk Desk, a decision tool for U.S. small \
businesses. You explain results; you never produce them yourself.

Rules you must follow:
1. Every number you state (money, percentages, probabilities, forecasts, economic statistics) \
must be copied from a tool result in this conversation or from the user's message. If you have \
not run the tool that produces a number, run it or say you do not have it. Never estimate, \
round loosely, or invent figures.
2. To answer what-if questions, call the scenario tool with the changed assumption; changed \
assumptions persist, so pass null for anything the user did not change.
3. Always say which objective was optimized, name the recommended action (or say none is \
defensible), give the change versus doing nothing with its range, the key assumptions and their \
sources, and the main limitation.
4. Label data honestly: the business data is synthetic unless the tool result says otherwise; \
economic values are observed FRED data as of the stated date; food-cost or demand changes the \
user proposes are hypothetical; action effects are modeled assumptions, not proven causal \
effects. A simulated share of 100% is not a real-world guarantee.
5. If required information is missing (for example a starting cash balance or a budget), ask \
for it instead of assuming.
6. Recommendations need human approval. Never claim to have executed any business action.
Keep answers short: a direct answer first, then the supporting numbers."""


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

    @property
    def label(self) -> str:
        return ANALYST_LABELS[self.mode]

    # ------------------------------------------------------------------ public
    def ask(self, question: str) -> AgentReply:
        self.user_texts.append(question)
        if self.mode == "offline":
            reply = self._offline(question)
        elif self.mode == "gemini":
            try:
                reply = self._ask_gemini(question)
            except Exception as exc:  # noqa: BLE001 - any provider failure must degrade, not crash
                from .gemini import GeminiUnavailable
                reason = str(exc) if isinstance(exc, GeminiUnavailable) else type(exc).__name__
                self.last_fallback = reason
                reply = self._offline(question)
                reply.fallback_reason = reason
                reply.notice = f"(Gemini unavailable: {reason}. Answered by the offline analyst.)"
        else:
            reply = self._ask_claude(question)
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
        return reply

    def _offline(self, question: str) -> AgentReply:
        reply = self.planner.answer(question)
        self.tool_outputs += [c["output"] for c in reply.tool_calls if "output" in c]
        return reply

    # ------------------------------------------------------------------ Gemini mode
    def _ask_gemini(self, question: str) -> AgentReply:
        self.gemini.add_user_text(question)
        text, calls = self.gemini.run(self.tool_outputs)
        return AgentReply(text, "gemini", calls)

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


# ====================================================================== offline analyst
def _money(x) -> str:
    return "n/a" if x is None else f"${x:,.0f}"


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

    def _call(self, calls, name, args):
        out = run_tool(self.s, name, args)
        calls.append({"name": name, "input": args, "output": out})
        return out

    def answer(self, q: str) -> AgentReply:
        ql = q.lower()
        calls: list[dict] = []
        pct = re.search(r"(-?\d+(?:\.\d+)?)\s*%", q)
        money = re.search(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k)?", q, re.IGNORECASE)
        amount = None if not money else float(money.group(1).replace(",", "")) * (1000 if money.group(2) else 1)
        try:
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
            if re.search(r"inflation|cpi|econom|fred|unemployment|interest|indicator|"
                         r"price index|food[- ]price", ql):
                live = bool(re.search(r"live|latest|current|today", ql))
                out = self._call(calls, "get_economic_indicators", {"refresh": live})
                return AgentReply(self._econ_text(out), "offline", calls)
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
            "four-week forecasts, economic indicators from FRED, cash-shortfall risk, restaurant "
            "what-ifs (food costs rising by a stated percent, demand becoming more "
            "price-sensitive), inventory budgets (a stated dollar budget) and 'what should I "
            "do?'. Set GEMINI_API_KEY for free-form questions.", "offline", calls)

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
        return (f"Offline analyst. {out['target']} forecast {out['forecast_start']} to "
                f"{out['forecast_end']}: {out['point_total']:,.0f} (80% interval {lo:,.0f} to "
                f"{hi:,.0f}) from {out['selected_model']}. Measured accuracy: validation WAPE "
                f"{out['validation']['wape']}, final-test WAPE {out['final_test']['wape']}, "
                f"final-test interval coverage {out['final_test']['interval_80_coverage']}. "
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
                f"{out['calibration']}.")

    @staticmethod
    def _overview_text(out):
        c = out["current"]
        return (f"Offline analyst. Last {out['period_days']} days ({out['period'][0]} to "
                f"{out['period'][1]}): revenue {_money(c['revenue'])}, operating profit "
                f"{_money(c['operating_profit'])} ({c['operating_margin_pct']}% margin). Cash "
                f"{_money(out['cash']['balance'])} ({out['cash']['source']}). Data: {out['data_label']}.")
