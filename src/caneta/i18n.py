"""UI strings in pt-BR, es and en (flat keys; missing keys fall back to pt-BR, then to the key)."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

FOLDER = Path(__file__).parent / "locales"


@cache
def strings(lang: str) -> dict:
    try:
        return json.loads((FOLDER / f"{lang}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def translator(lang: str):
    own, base = strings(lang), strings("pt_BR")

    def t(key: str, **kw) -> str:
        text = own.get(key) or base.get(key) or key
        return text.format(**kw) if kw else text

    return t
