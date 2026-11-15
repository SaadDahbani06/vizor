"""Google Gemini provider for the decision agent (official ``google-genai`` SDK).

Manual function calling (automatic function calling is disabled) so every tool call is executed
by our own code against ``BusinessSession`` and recorded for the grounding check:

    generate_content(contents, tools)  ->  response.function_calls?
        yes: append the model's content (keeps thought signatures), run each tool, append one
             user turn with a function_response part per call, loop
        no:  return response.text

Verified 2026-11-14 against the API with the configured key (``models.list``): gemini-3.8/3.7/3.6/
3.5-flash, gemini-3.5-flash-lite and gemini-3.1-flash-lite support generateContent with thinking and
1,048,576 input tokens. Routing (``agent.py``) answers most questions without the model; the model
explains verified engine results in one request, and runs its own tool loop only for open questions. Pricing page: free
tier available (free-tier prompts may be used to improve Google products; paid tier is not), paid
tier $0.75 / $3.75 per 1M input/output tokens through 2026-12-31.
"""
from __future__ import annotations

import json
import os
import time

from .tools import TOOL_SCHEMAS, BusinessSession, run_tool

# Measured 2026-11-14 with this project's key (synthetic data): gemini-3.5-flash-lite answered in
# about 1.0-1.1 s per request; gemini-3.8-flash / 3.7-flash returned 429 (quota exhausted) and
# gemini-3.5-flash took 3-8 s when available. Override with GEMINI_MODEL.
DEFAULT_MODEL = "gemini-3.5-flash-lite"
MAX_TOOL_ROUNDS = int(os.getenv("MSRD_GEMINI_MAX_ROUNDS", "4"))
BREAKER_S = 90  # skip a model for this long after a 429/5xx/timeout (circuit breaker)
_BROKEN: dict[str, float] = {}

# Fields that help people read a tool result but add tokens without helping the model explain it.
_DROP = {"interval_meaning", "range_check", "effect_type", "run_id", "tool", "data_origin", "note", "text",
         "capabilities", "not_used_in_forecasts", "what_is_needed", "leaderboard", "comparables", "excluded",
         "scheduled_bills", "items", "method", "source_url", "warnings", "assumptions_detail"}


def compact(x, depth: int = 0):
    """Smaller copy of a tool result for the model (full results still feed the number check)."""
    if isinstance(x, dict):
        return {k: compact(v, depth + 1) for k, v in x.items() if k not in _DROP}
    if isinstance(x, list):
        return [compact(v, depth + 1) for v in x[:8]]
    if isinstance(x, float):
        return round(x, 2)
    if isinstance(x, str) and len(x) > 400:
        return x[:400] + "…"
    return x


class GeminiUnavailable(RuntimeError):
    """Raised for any API, quota, timeout or transport failure; callers fall back offline."""


def _transport_errors() -> tuple[type[BaseException], ...]:
    errs: list[type[BaseException]] = [TimeoutError, ConnectionError]
    for mod in ("httpx", "httpx2"):
        try:
            errs.append(__import__(mod).TransportError)
        except (ImportError, AttributeError):
            pass
    return tuple(errs)


class CallBudget:
    """Process-wide cap on Gemini requests per rolling hour (protects free-tier quota)."""

    def __init__(self, per_hour: int):
        self.per_hour, self.stamps = per_hour, []

    def take(self) -> None:
        now = time.monotonic()
        self.stamps = [t for t in self.stamps if now - t < 3600]
        if len(self.stamps) >= self.per_hour:
            raise GeminiUnavailable(f"local Gemini budget reached ({self.per_hour} requests/hour)")
        self.stamps.append(now)


BUDGET = CallBudget(int(os.getenv("MSRD_GEMINI_MAX_CALLS_PER_HOUR", "120")))


class GeminiProvider:
    def __init__(self, session: BusinessSession, system_prompt: str, *, client=None,
                 model: str | None = None, timeout_s: float | None = None):
        from google import genai
        from google.genai import types

        self.types = types
        self.session = session
        self.model = model or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
        timeout_s = timeout_s or float(os.getenv("GEMINI_TIMEOUT_S", "25"))
        self.timeout_s = timeout_s
        # Numbers come from tools, so deep reasoning adds latency without adding accuracy.
        thinking = os.getenv("GEMINI_THINKING_LEVEL", "low").upper()
        self.client = client or genai.Client(
            api_key=os.environ.get("GEMINI_API_KEY"),
            http_options=types.HttpOptions(
                timeout=int(timeout_s * 1000),  # milliseconds
                # No silent retries: a failed request falls back (other model or offline) at once.
                retry_options=types.HttpRetryOptions(attempts=1)))
        self.config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t["name"], description=t["description"],
                                          parameters_json_schema=t["input_schema"])
                for t in TOOL_SCHEMAS])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=(types.ThinkingConfig(thinking_level=thinking)
                             if thinking not in ("", "DEFAULT") else None),
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode="AUTO")))
        self.contents: list = []  # full multi-turn history, model turns kept verbatim
        self.fallbacks = [m.strip() for m in os.getenv(
            "GEMINI_FALLBACK_MODELS", "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.5-flash").split(",")
            if m.strip()]
        self.explain_config = types.GenerateContentConfig(
            system_instruction=system_prompt, max_output_tokens=700,
            thinking_config=(types.ThinkingConfig(thinking_level=thinking)
                             if thinking not in ("", "DEFAULT") else None))
        self.switched_from: str | None = None

    def _generate_with(self, model: str, contents=None, config=None):
        from google.genai import errors
        BUDGET.take()
        try:
            return self.client.models.generate_content(model=model, contents=self.contents if contents is None
                                                       else contents, config=config or self.config)
        except errors.APIError as exc:
            if exc.code in (408, 429, 500, 502, 503, 504):
                _BROKEN[model] = time.monotonic() + BREAKER_S
            raise GeminiUnavailable(f"Gemini API error {exc.code}: {exc.status or ''}".strip()) from None
        except _transport_errors() as exc:
            _BROKEN[model] = time.monotonic() + BREAKER_S
            raise GeminiUnavailable(f"Gemini transport error: {type(exc).__name__}") from None

    def _generate(self):
        """Use the configured model. Only before the model has produced any turn in this
        conversation may we switch to a fallback model (thought signatures are model-bound)."""
        started = any(getattr(c, "role", None) == "model" for c in self.contents)
        chain = [self.model] if started else [self.model, *[m for m in self.fallbacks if m != self.model]]
        live = [m for m in chain if _BROKEN.get(m, 0) <= time.monotonic()]
        if not live:
            raise GeminiUnavailable("Gemini recently failed; using offline mode for a few minutes")
        last = None
        for model in live:
            try:
                response = self._generate_with(model)
                if model != self.model:
                    self.switched_from, self.model = self.model, model
                return response
            except GeminiUnavailable as exc:
                last = exc
                if not any(code in str(exc) for code in ("429", "500", "503", "504", "transport")):
                    break  # auth / bad request: a different model will not help
        raise last

    def add_user_text(self, text: str) -> None:
        t = self.types
        self.contents.append(t.Content(role="user", parts=[t.Part.from_text(text=text)]))

    def explain(self, question: str, results: list[dict], draft: str, deadline: float | None = None) -> str:
        """One request, no tools: rewrite verified results as a short answer. The question and the
        answer are kept in the conversation so later questions have context."""
        if deadline is not None and time.monotonic() > deadline:
            raise GeminiUnavailable("time limit reached")
        live = [m for m in [self.model, *self.fallbacks] if _BROKEN.get(m, 0) <= time.monotonic()]
        if not live:
            raise GeminiUnavailable("Gemini recently failed; using offline mode for a few minutes")
        t = self.types
        prompt = (f"Owner's question: {question}\n\nVerified results (JSON, the only allowed source of "
                  f"numbers):\n{json.dumps([compact(r) for r in results], default=str)[:6000]}\n\n"
                  f"Draft answer:\n{draft[:2500]}\n\nWrite the reply to the owner: at most 120 words, plain "
                  "language, the direct answer first. Copy numbers exactly from the results (no new "
                  "arithmetic). Keep any downside risk and say which figures are estimates. If an action is "
                  "suggested, say it needs the owner's approval.")
        history = [*self.contents, t.Content(role="user", parts=[t.Part.from_text(text=prompt)])]
        last = None
        for model in live[:2]:
            try:
                response = self._generate_with(model, history, self.explain_config)
                text = (response.text or "").strip()
                if not text:
                    raise GeminiUnavailable("empty answer")
                self.remember(question, text)
                return text
            except GeminiUnavailable as exc:
                last = exc
        raise last

    def remember(self, question: str, answer: str) -> None:
        """Keep a turn answered outside the model (fast path) in the model's conversation."""
        t = self.types
        self.contents.append(t.Content(role="user", parts=[t.Part.from_text(text=question)]))
        self.contents.append(t.Content(role="model", parts=[t.Part.from_text(text=answer[:1500])]))

    def run(self, outputs_sink: list, deadline: float | None = None) -> tuple[str, list[dict]]:
        t = self.types
        calls: list[dict] = []
        for _ in range(MAX_TOOL_ROUNDS):
            if deadline is not None and time.monotonic() > deadline:
                raise GeminiUnavailable("time limit reached")
            response = self._generate()
            cand = (response.candidates or [None])[0]
            if cand is None or cand.content is None:
                reason = getattr(cand, "finish_reason", None) if cand else "no candidate"
                return f"The model returned no answer ({reason}). No analysis was produced.", calls
            self.contents.append(cand.content)
            fcs = response.function_calls or []
            if not fcs:
                return (response.text or "").strip(), calls
            parts = []
            for fc in fcs:
                args = dict(fc.args or {})
                try:
                    out = run_tool(self.session, fc.name, args)
                    outputs_sink.append(out)
                    calls.append({"name": fc.name, "input": args, "output": out})
                    payload = {"result": json.loads(json.dumps(compact(out), default=str))}
                except (ValueError, KeyError, TypeError, RuntimeError) as exc:
                    calls.append({"name": fc.name, "input": args, "error": str(exc)})
                    payload = {"error": f"{type(exc).__name__}: {exc}"}
                part = t.Part.from_function_response(name=fc.name, response=payload)
                if fc.id:
                    part.function_response.id = fc.id
                parts.append(part)
            self.contents.append(t.Content(role="user", parts=parts))
        return "I stopped after the maximum number of tool rounds without a final answer.", calls
