"""HTTP API providers: Anthropic, and one OpenAI-compatible adapter for OpenAI, NVIDIA NIM and Ollama."""

from __future__ import annotations

from .base import Provider

MAX_TOKENS = 4096


class AnthropicAPI(Provider):
    name = "anthropic-api"

    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    def _complete(self, prompt, system, json_mode, timeout, on_log):
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key, timeout=timeout)
        kwargs = {"system": system} if system else {}
        msg = client.messages.create(model=self.model, max_tokens=MAX_TOKENS,
                                     messages=[{"role": "user", "content": prompt}], **kwargs)
        return "".join(block.text for block in msg.content if getattr(block, "type", "") == "text").strip()


class OpenAICompat(Provider):
    """OpenAI, NVIDIA NIM (https://integrate.api.nvidia.com/v1) and Ollama (http://localhost:11434/v1)."""

    def __init__(self, name: str, base_url: str | None, api_key: str | None, model: str):
        self.name, self.base_url, self.api_key, self.model = name, base_url, api_key, model

    def client(self, timeout: float = 600):
        import openai

        return openai.OpenAI(base_url=self.base_url or None, api_key=self.api_key or "ollama", timeout=timeout)

    def _complete(self, prompt, system, json_mode, timeout, on_log):
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        client = self.client(timeout)
        kwargs = {"model": self.model, "messages": messages, "max_tokens": MAX_TOKENS, "temperature": 0.3}
        if json_mode:
            try:
                resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
                return (resp.choices[0].message.content or "").strip()
            except Exception:  # noqa: BLE001 - not every server supports json mode; the prompt asks for JSON anyway
                pass
        resp = client.chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or "").strip()

    def list_models(self) -> list[str]:
        return sorted(m.id for m in self.client(30).models.list().data)
