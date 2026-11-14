"""Google Gemini provider for the decision agent (official ``google-genai`` SDK).

Manual function calling (automatic function calling is disabled) so every tool call is executed
by our own code against ``BusinessSession`` and recorded for the grounding check:

    generate_content(contents, tools)  ->  response.function_calls?
        yes: append the model's content (keeps thought signatures), run each tool, append one
             user turn with a function_response part per call, loop
        no:  return response.text

Verified 2026-11-14 against the API with the configured key: ``models/gemini-3.8-flash``
supports generateContent, thinking, 1,048,576 input / 65,536 output tokens. Pricing page: free
tier available (free-tier prompts may be used to improve Google products; paid tier is not), paid
tier $0.75 / $3.75 per 1M input/output tokens through 2026-12-31.
"""
from __future__ import annotations

import json
import os
import time

from .tools import TOOL_SCHEMAS, BusinessSession, run_tool

DEFAULT_MODEL = "gemini-3.8-flash"
MAX_TOOL_ROUNDS = 8


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
        timeout_s = timeout_s or float(os.getenv("GEMINI_TIMEOUT_S", "60"))
        # Numbers come from tools, so deep reasoning adds latency without adding accuracy.
        thinking = os.getenv("GEMINI_THINKING_LEVEL", "low").upper()
        self.client = client or genai.Client(
            api_key=os.environ.get("GEMINI_API_KEY"),
            http_options=types.HttpOptions(
                timeout=int(timeout_s * 1000),  # milliseconds
                retry_options=types.HttpRetryOptions(  # one retry on 408/429/5xx
                    attempts=2, initial_delay=2.0, http_status_codes=[408, 429, 500, 502, 503, 504])))
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
            "GEMINI_FALLBACK_MODELS", "gemini-3.7-flash,gemini-3.5-flash").split(",") if m.strip()]
        self.switched_from: str | None = None

    def _generate_with(self, model: str):
        from google.genai import errors
        BUDGET.take()
        try:
            return self.client.models.generate_content(model=model, contents=self.contents,
                                                       config=self.config)
        except errors.APIError as exc:
            raise GeminiUnavailable(f"Gemini API error {exc.code}: {exc.status or ''}".strip()) from None
        except _transport_errors() as exc:
            raise GeminiUnavailable(f"Gemini transport error: {type(exc).__name__}") from None

    def _generate(self):
        """Use the configured model. Only before the model has produced any turn in this
        conversation may we switch to a fallback model (thought signatures are model-bound)."""
        started = any(getattr(c, "role", None) == "model" for c in self.contents)
        chain = [self.model] if started else [self.model, *[m for m in self.fallbacks if m != self.model]]
        last = None
        for model in chain:
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

    def run(self, outputs_sink: list) -> tuple[str, list[dict]]:
        t = self.types
        calls: list[dict] = []
        for _ in range(MAX_TOOL_ROUNDS):
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
                    payload = {"result": json.loads(json.dumps(out, default=str))}
                except (ValueError, KeyError, TypeError, RuntimeError) as exc:
                    calls.append({"name": fc.name, "input": args, "error": str(exc)})
                    payload = {"error": f"{type(exc).__name__}: {exc}"}
                part = t.Part.from_function_response(name=fc.name, response=payload)
                if fc.id:
                    part.function_response.id = fc.id
                parts.append(part)
            self.contents.append(t.Content(role="user", parts=parts))
        return "I stopped after the maximum number of tool rounds without a final answer.", calls
