"""Every finding must quote the section literally (adapted from lccv-paper-maker review/anchoring.py).

A reviewer that cannot point to the exact words did not read them: such findings are discarded,
with the reason kept for transparency."""

from __future__ import annotations

from .schemas import LABELS, MAX_FINDINGS, RANK, norm

MIN_QUOTE_CHARS = 8
ELLIPSES = ("...", "…")


def clean_edit(edit, text: str) -> dict | None:
    """Keep a proposed edit only when `find` occurs exactly once in the current text."""
    if not isinstance(edit, dict):
        return None
    find, replace = edit.get("find"), edit.get("replace")
    if not isinstance(find, str) or not isinstance(replace, str) or not find or text.count(find) != 1:
        return None
    return {"find": find, "replace": replace}


def problems(finding: dict, text: str, strict_quote: bool = True) -> list[str]:
    out = []
    if finding.get("prioridade") not in RANK:
        out.append(f"prioridade inválida: {finding.get('prioridade')!r}")
    if finding.get("rotulo") not in LABELS:
        out.append(f"rótulo inválido: {finding.get('rotulo')!r}")
    if not norm(finding.get("descricao")):
        out.append("sem descrição")
    quote = norm(finding.get("excerpt"))
    if strict_quote and (len(quote) < MIN_QUOTE_CHARS or any(m in quote for m in ELLIPSES)):
        out.append("trecho ausente, curto demais ou com reticências")
    elif quote and quote not in norm(text):
        out.append("trecho não encontrado na seção (apontamento sem âncora)")
    return out


def screen(findings, text: str, role: str, cap: int = MAX_FINDINGS, strict_quote: bool = True):
    """Split findings into anchored ones (annotated with role and a clean edit) and discarded ones."""
    kept, discarded = [], []
    for k, f in enumerate(findings if isinstance(findings, list) else [], 1):
        if not isinstance(f, dict):
            discarded.append({"n": k, "reasons": ["não é um objeto"]})
            continue
        why = problems(f, text, strict_quote)
        if not why and len(kept) >= cap:
            why.append(f"acima do limite de {cap} apontamentos por revisor")
        if why:
            discarded.append({"n": k, "reasons": why, "excerpt": norm(f.get("excerpt"))[:120]})
            continue
        kept.append(dict(f, role=role, edit=clean_edit(f.get("edit"), text)))
    return kept, discarded
