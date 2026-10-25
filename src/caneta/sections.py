"""Section kinds, states and transitions.

Advancing is suggested, never forced: every transition below is allowed from the UI,
except editing an approved section, which needs `reopen` first (it creates a snapshot).
"""

from __future__ import annotations

import re
import unicodedata

KINDS = ("abstract", "intro", "methods", "results", "discussion", "conclusion", "custom")
STATES = ("draft", "polished", "enriched", "in_review", "revising", "approved")

# Sections where the MCP helps: introduction gets related citations (search_papers);
# results/discussion get full-text comparison (fetch_full_papers).
ENRICH_KINDS = {"intro"}
COMPARE_KINDS = {"results", "discussion"}
METHOD_REVIEW_KINDS = {"methods", "results", "discussion"}

# Default structure for "start from scratch" (titles per manuscript language).
IMRAD = {
    "pt_BR": [("Resumo", "abstract"), ("Introdução", "intro"), ("Materiais e Métodos", "methods"),
              ("Resultados", "results"), ("Discussão", "discussion"), ("Conclusões", "conclusion")],
    "es": [("Resumen", "abstract"), ("Introducción", "intro"), ("Materiales y Métodos", "methods"),
           ("Resultados", "results"), ("Discusión", "discussion"), ("Conclusiones", "conclusion")],
    "en": [("Abstract", "abstract"), ("Introduction", "intro"), ("Materials and Methods", "methods"),
           ("Results", "results"), ("Discussion", "discussion"), ("Conclusions", "conclusion")],
}

_KIND_WORDS = {
    "abstract": ("resumo", "resumen", "abstract", "sumario"),
    "intro": ("introdu", "background", "contexto", "antecedentes"),
    "methods": ("metod", "method", "material", "procedimento", "procedimiento", "approach"),
    "results": ("resultado", "result", "achados", "findings"),
    "discussion": ("discuss", "discus"),
    "conclusion": ("conclus", "consideracoes finais", "consideraciones finales", "final remarks"),
}


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def guess_kind(title: str) -> str:
    t = fold(title)
    # "Results and discussion" counts as results (comparison helps both).
    for kind in ("abstract", "intro", "methods", "results", "discussion", "conclusion"):
        if any(w in t for w in _KIND_WORDS[kind]):
            return kind
    return "custom"


def slugify(text: str, max_len: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")
    return s[:max_len].rstrip("-") or "secao"


class StateError(Exception):
    pass


def can_edit(state: str) -> bool:
    return state != "approved"


def next_state_after_edit(state: str) -> str:
    """Manual edits keep the progress marker, except they invalidate an approval (blocked) ."""
    if state == "approved":
        raise StateError("section approved: reopen it before editing")
    return state
