"""Grammar and style checking without an LLM (LanguageTool).

Only runs when the author turned it on in Settings: 'public' sends the section text to
api.languagetool.org (free tier: ~20 requests/min, 20 KB per request); 'local' uses the
author's own LanguageTool server. Matches become review findings with an anchored edit."""

from __future__ import annotations

import httpx

from . import config

PUBLIC_URL = "https://api.languagetool.org/v2"
LANGS = {"pt_BR": "pt-BR", "es": "es", "en": "en-US"}
MAX_CHARS = 19_000
WINDOW = 24  # characters of context on each side, so the excerpt is unique and anchorable


class StyleError(Exception):
    pass


def enabled() -> bool:
    return config.load().languagetool in ("public", "local")


def check(text: str, lang: str = "pt_BR", timeout: float = 30) -> list[dict]:
    cfg = config.load()
    if cfg.languagetool == "off":
        return []
    url = (PUBLIC_URL if cfg.languagetool == "public" else cfg.languagetool_url.rstrip("/")) + "/check"
    try:
        r = httpx.post(url, data={"text": text[:MAX_CHARS], "language": LANGS.get(lang, "pt-BR")}, timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise StyleError(f"LanguageTool indisponível: {exc}") from exc
    return to_findings(text, r.json().get("matches") or [])


def to_findings(text: str, matches: list[dict], cap: int = 25) -> list[dict]:
    out = []
    for m in matches:
        off, length = int(m.get("offset", 0)), int(m.get("length", 0))
        if length <= 0 or off + length > len(text):
            continue
        start, end = max(0, off - WINDOW), min(len(text), off + length + WINDOW)
        window = text[start:end]
        reps = [r.get("value") for r in m.get("replacements") or [] if r.get("value") is not None]
        edit = None
        if reps:
            fixed = window[: off - start] + reps[0] + window[off - start + length:]
            edit = {"find": window, "replace": fixed}
        category = ((m.get("rule") or {}).get("category") or {}).get("id", "")
        out.append({
            "excerpt": window.strip(), "rotulo": "Imprecisão" if category in ("GRAMMAR", "TYPOS") else "Clareza",
            "prioridade": "baixa", "descricao": m.get("message") or m.get("shortMessage") or "Sugestão de estilo",
            "acao_sugerida": f"Sugestão: {', '.join(reps[:3])}" if reps else "", "edit": edit, "confianca": "média",
        })
        if len(out) >= cap:
            break
    return out
