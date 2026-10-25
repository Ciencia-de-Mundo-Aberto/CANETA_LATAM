"""Prompt templates (Markdown with $placeholders; editable without touching code)."""

from __future__ import annotations

from pathlib import Path
from string import Template

FOLDER = Path(__file__).parent
LANG_NAMES = {"pt_BR": "português do Brasil", "es": "espanhol", "en": "inglês"}


def render(name: str, **values) -> str:
    text = (FOLDER / f"{name}.md").read_text(encoding="utf-8")
    return Template(text).safe_substitute(**{k: ("" if v is None else str(v)) for k, v in values.items()})


def out_lang(lang: str) -> str:
    return LANG_NAMES.get(lang, LANG_NAMES["pt_BR"])
