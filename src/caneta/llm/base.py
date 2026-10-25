"""Provider interface: CANETA orchestrates (searches, grounding, review anchoring);
the language model only writes and reviews text it is given."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


class NoLLM(Exception):
    """Raised when the user chose to work without a language model."""


class LLMOutputError(Exception):
    pass


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    data: dict | None = None
    error: str | None = None
    provider: str = ""
    model: str = ""
    duration_s: float = 0.0
    meta: dict = field(default_factory=dict)


def extract_json(text: str) -> dict:
    """The JSON object in a model reply, tolerating a fenced block or surrounding prose
    (from lccv-paper-maker agent.extract_json)."""
    text = (text or "").strip()
    fence = FENCED_JSON.search(text)
    if fence:
        text = fence[1]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise LLMOutputError("the reply contains no JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMOutputError("the reply contains no JSON object")
    return data


JSON_INSTRUCTION = (
    "\n\nResponda SOMENTE com um objeto JSON válido (sem texto antes ou depois), seguindo exatamente este formato:\n"
)


class Provider:
    name = "base"
    model = ""

    def _complete(self, prompt: str, system: str, json_mode: bool, timeout: int, on_log) -> str:
        raise NotImplementedError

    def complete(self, prompt: str, *, system: str = "", schema: dict | None = None, timeout: int = 600,
                 on_log=None) -> LLMResult:
        """Run one prompt. With `schema`, the reply must be a JSON object (parsed into `data`)."""
        import time

        full = prompt + (JSON_INSTRUCTION + json.dumps(schema, ensure_ascii=False, indent=1) if schema else "")
        started = time.monotonic()
        try:
            text = self._complete(full, system, schema is not None, timeout, on_log)
        except NoLLM:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a readable error
            return LLMResult(False, error=f"{type(exc).__name__}: {exc}", provider=self.name, model=self.model,
                             duration_s=round(time.monotonic() - started, 2))
        result = LLMResult(True, text=text, provider=self.name, model=self.model,
                           duration_s=round(time.monotonic() - started, 2))
        if schema is not None:
            try:
                result.data = extract_json(text)
            except LLMOutputError as exc:
                result.ok, result.error = False, str(exc)
        return result


class NullProvider(Provider):
    name = "none"

    def _complete(self, prompt, system, json_mode, timeout, on_log):
        raise NoLLM("modo sem IA")
