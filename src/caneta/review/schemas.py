"""Review vocabulary (from the review report format of the open book) and the finding schema."""

from __future__ import annotations

import re

from ..sections import METHOD_REVIEW_KINDS

ROLES: dict[str, dict] = {
    "editor": {"label": "Editor — clareza e estrutura", "default": True, "kinds": None},
    "metodologo": {"label": "Metodólogo — rigor", "default": True, "kinds": METHOD_REVIEW_KINDS},
    "referencias": {"label": "Referências — fundamentação", "default": True, "kinds": None},
    "adversario": {"label": "Adversário — objeções fortes", "default": False, "kinds": None},
}
LABELS = ("Erro", "Inconsistência", "Exagero", "Imprecisão", "Evidência insuficiente", "Clareza", "Estrutura")
PRIORITIES = ("crítica", "alta", "média", "baixa")
RANK = {"crítica": 3, "alta": 2, "média": 1, "baixa": 0}
OPEN, ACCEPTED, EDITED, REJECTED, DEFERRED = "aberto", "aceito", "editado", "rejeitado", "adiado"
RESOLVED = "resolvido"  # the author fixed it directly in the editor
GROUP_STATES = (OPEN, ACCEPTED, EDITED, REJECTED, DEFERRED, RESOLVED)
MAX_FINDINGS = 8

FINDING_SCHEMA = {
    "parecer": "aprovar | revisar",
    "findings": [
        {
            "excerpt": "trecho copiado LITERALMENTE da seção (mínimo 8 caracteres, sem reticências)",
            "rotulo": " | ".join(LABELS),
            "prioridade": " | ".join(PRIORITIES),
            "descricao": "qual é o problema, em uma ou duas frases",
            "acao_sugerida": "o que o autor deve fazer",
            "confianca": "alta | média | baixa",
            "edit": {"find": "trecho exato a substituir (ou null se a correção não for local)",
                     "replace": "texto novo"},
        }
    ],
}


def norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def roles_for(kind: str, include_adversary: bool = False) -> list[str]:
    out = []
    for role, spec in ROLES.items():
        if role == "adversario" and not include_adversary:
            continue
        if spec["kinds"] is None or kind in spec["kinds"]:
            out.append(role)
    return out
