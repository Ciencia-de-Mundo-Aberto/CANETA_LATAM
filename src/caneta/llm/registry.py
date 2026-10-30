"""Provider catalog shown in the setup wizard, and building a Provider from the user config.

Recommended models are the latest small ones (cheap, fast, good enough for section-sized work).
Edit this table (or pick another model in Settings) when catalogs change."""

from __future__ import annotations

import re

from .. import config
from .api_providers import AnthropicAPI, OpenAICompat
from .base import NullProvider, Provider
from .cli_agents import ClaudeCLI, CodexCLI, find_binary

NIM_URL = "https://integrate.api.nvidia.com/v1"
# models that classify, embed or read documents: they answer "safe" or vectors, never a review
NOT_CHAT = re.compile(r"safety|guard|embed|rerank|retriev|reward|parse|ocr|pii", re.I)
OLLAMA_URL = "http://localhost:11434/v1"

PROVIDERS: dict[str, dict] = {
    "claude-cli": {"group": "paid", "label": "Claude CLI (Claude Code)", "needs_key": False,
                   "default_model": "claude-haiku-5-5", "binary": "claude"},
    "codex-cli": {"group": "paid", "label": "Codex CLI (ChatGPT)", "needs_key": False,
                  "default_model": "gpt-6-luna", "binary": "codex"},
    "anthropic-api": {"group": "paid", "label": "API Anthropic (chave)", "needs_key": True,
                      "default_model": "claude-haiku-5-5", "key_url": "https://console.anthropic.com/settings/keys"},
    "openai-api": {"group": "paid", "label": "API OpenAI (chave)", "needs_key": True,
                   "default_model": "gpt-6-luna", "key_url": "https://platform.openai.com/api-keys"},
    "nvidia-nim": {"group": "free", "label": "NVIDIA NIM (chave gratuita)", "needs_key": True,
                   "default_model": "nvidia/nemotron-3.5-lightning-30b-a3b", "base_url": NIM_URL,
                   "key_url": "https://build.nvidia.com", "model_filter": "nemotron"},
    "codex-free": {"group": "free", "label": "Codex CLI com conta gratuita do ChatGPT", "needs_key": False,
                   "default_model": "gpt-6-luna", "binary": "codex"},
    "ollama": {"group": "local", "label": "Ollama (no seu computador)", "needs_key": False,
               "default_model": "", "base_url": OLLAMA_URL},
    "none": {"group": "none", "label": "Sem IA", "needs_key": False, "default_model": ""},
}


def detect_clis() -> dict[str, bool]:
    return {"claude": find_binary("claude") is not None, "codex": find_binary("codex") is not None}


def build(provider_id: str | None = None, model: str | None = None, base_url: str | None = None,
          api_key: str | None = None) -> Provider:
    cfg = config.load()
    pid = provider_id or cfg.provider
    spec = PROVIDERS.get(pid, PROVIDERS["none"])
    model = model if model is not None else (cfg.model if pid == cfg.provider else "") or spec["default_model"]
    key = api_key or (config.get_secret(pid) if spec["needs_key"] else None)
    if pid == "claude-cli":
        return ClaudeCLI(model)
    if pid in ("codex-cli", "codex-free"):
        return CodexCLI(model)
    if pid == "anthropic-api":
        return AnthropicAPI(key or "", model)
    if pid == "openai-api":
        return OpenAICompat(pid, None, key, model)
    if pid in ("nvidia-nim", "ollama"):
        url = base_url or (cfg.base_url if pid == cfg.provider and cfg.base_url else spec["base_url"])
        return OpenAICompat(pid, url, key, model)
    return NullProvider()


def test_connection(provider: Provider) -> dict:
    if isinstance(provider, NullProvider):
        return {"ok": True, "message": "Modo sem IA: nada a testar."}
    res = provider.complete("Responda apenas com a palavra OK.", timeout=120)
    if not res.ok:
        return {"ok": False, "message": res.error or "falha", "duration_s": res.duration_s}
    return {"ok": True, "message": res.text[:200], "duration_s": res.duration_s}


def list_models(provider_id: str, api_key: str | None = None, base_url: str | None = None) -> list[str]:
    spec = PROVIDERS[provider_id]
    prov = build(provider_id, model="", base_url=base_url, api_key=api_key)
    if not isinstance(prov, OpenAICompat):
        return [spec["default_model"]] if spec["default_model"] else []
    models = prov.list_models()
    flt = spec.get("model_filter")
    models = [m for m in models if flt in m.lower()] if flt else models
    return [m for m in models if not NOT_CHAT.search(m)]


def test_models(provider_id: str, models: list[str], api_key: str | None = None) -> list[dict]:
    """For the 'Testar modelos' button: a tiny completion per model, with latency."""
    out = []
    for m in models:
        prov = build(provider_id, model=m, api_key=api_key)
        res = prov.complete("Responda apenas com a palavra OK.", timeout=60)
        out.append({"model": m, "ok": res.ok, "duration_s": res.duration_s, "message": (res.text or res.error or "")[:120]})
    return out
