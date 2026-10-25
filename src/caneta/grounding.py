"""Grounding rules: citations and claims must rest on what CANETA found (MCP) or the author provided.

Ten rules, softer than lccv-paper-maker's (no minimum citations, no forced diagrams, no word minimums,
no hard gates). They produce findings shown in the same cards as the reviewers' ones, and a few
automatic fixes applied to AI-written text before the author sees it."""

from __future__ import annotations

import re

from .references import CITE_RE, cited_keys

GAP_MARK = "[LACUNA: evidência necessária]"
UNVERIFIED_MARK = "[NÃO VERIFICADO]"
PLACEHOLDER = re.compile(r"\bTODO\b|\bTBD\b|\bXX+\b|\[citation needed\]|\[LACUNA[^\]]*\]|\[NÃO VERIFICADO\]",
                         re.IGNORECASE)
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
TERMINAL = (".", "!", "?", ":", ")", "]", '"', "”", "»", "*", "`", "$")
# Sentences that assert evidence and therefore need support (pt/es/en).
CLAIM = re.compile(
    r"\b(estudos|pesquisas|trabalhos|literatura|autores|estudios|trabajos|investigaciones|studies|research|"
    r"literature|demonstr\w+|mostr\w+|indicam|indican|evidenci\w+|shows?|showed|demonstrat\w+|suggest\w*|"
    r"sugere\w*|comprov\w+)\b",
    re.IGNORECASE)
# Sentences about the author's own work ("este trabalho descreve…", "we found…") need no citation.
OWN_WORK = re.compile(
    r"\b(n?est[ea]|presente|nosso|nossa)\s+(trabalho|estudo|artigo|pesquisa|trabajo|estudio|art[ií]culo)\b|"
    r"\b(this|our|the present)\s+(study|work|paper|article)\b|\b(we|nós|nosotros)\b",
    re.IGNORECASE)
NUMBER = re.compile(r"\d+(?:[.,]\d+)?\s*%|\b\d+(?:[.,]\d+)?\b")
CLAUSE = re.compile(r"[;,](?![^\[]*\])|\b(?:enquanto|whereas|while|mientras)\b")
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ\[(])")


def finding(rule: str, excerpt: str, description: str, priority: str = "média", label: str = "Evidência insuficiente",
            action: str = "", edit: dict | None = None) -> dict:
    return {"role": "fundamentacao", "rule": rule, "excerpt": excerpt, "rotulo": label, "prioridade": priority,
            "descricao": description, "acao_sugerida": action, "edit": edit, "confianca": "alta"}


# ------------------------------------------------------------------ rule 1 + 9: citation keys
def strip_unknown_keys(text: str, known: set[str]) -> tuple[str, list[str]]:
    """Remove citations of keys that are not in the project references (invented by a model)."""
    removed: list[str] = []

    def fix_bracket(m: re.Match) -> str:
        inner = m.group(1)
        parts = [p.strip() for p in inner.split(";")]
        keep = []
        for p in parts:
            km = CITE_RE.search(p)
            if km and km.group(1) not in known:
                removed.append(km.group(1))
            else:
                keep.append(p)
        return f"[{'; '.join(keep)}]" if keep else ""

    text = re.sub(r"\[([^\[\]]*@[^\[\]]*)\]", fix_bracket, text)

    def fix_bare(m: re.Match) -> str:
        if m.group(1) in known:
            return m.group(0)
        removed.append(m.group(1))
        return ""

    text = CITE_RE.sub(fix_bare, text)
    text = re.sub(r"[ \t]+([.,;:])", r"\1", text)
    return text, sorted(set(removed))


def citations_preserved(before: str, after: str) -> list[str]:
    """Rule 9: keys cited before an AI rewrite must still be cited after it."""
    return sorted(set(cited_keys(before)) - set(cited_keys(after)))


# ------------------------------------------------------------------ structural checks (from paper-maker)
def ends_mid_sentence(body: str) -> bool:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", body.strip()) if b.strip()]
    block = blocks[-1] if blocks else ""
    first = block.splitlines()[0].lstrip() if block else ""
    if not block or first.startswith(("```", "~~~", "|", "- ", "* ", "+ ", "!", ">", "#", "$$", "\\begin")):
        return False
    if re.match(r"\d+[.)]\s", first):
        return False
    return not block.rstrip().endswith(TERMINAL)


def empty_headings(md: str) -> list[str]:
    lines = md.splitlines()
    heads = [(i, len(m.group(1)), m.group(2)) for i, line in enumerate(lines) if (m := HEADING.match(line))]
    empty = []
    for k, (i, level, title) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        nxt_level = heads[k + 1][1] if k + 1 < len(heads) else 0
        if not "\n".join(lines[i + 1 : end]).strip() and not (nxt_level > level):
            empty.append(title)
    return empty


def sentences(text: str) -> list[str]:
    clean = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    out = []
    for para in re.split(r"\n\s*\n", clean):
        para = " ".join(para.split())
        if para and not para.startswith(("#", "|", "!")):
            out += [s.strip() for s in SENTENCE.split(para) if s.strip()]
    return out


def unsupported_claims(text: str, material_text: str = "") -> list[str]:
    """Rule 3: sentences asserting evidence (claim words or numbers) without any citation.

    Numbers that also appear in the author's own material count as supported (their results)."""
    out = []
    for s in sentences(text):
        if CITE_RE.search(s) or "[LACUNA" in s or OWN_WORK.search(s):
            continue
        has_claim = bool(CLAIM.search(s))
        nums = [n.strip() for n in NUMBER.findall(s) if len(n.strip()) > 1]
        own = nums and material_text and all(n.replace(",", ".") in material_text.replace(",", ".") for n in nums)
        if has_claim or (nums and not own and re.search(r"%|\b(vezes|times|mais|menos|more|less)\b", s)):
            out.append(s)
    return out


def check_section(text: str, kind: str, known_keys: set[str], material_text: str = "",
                  word_limit: int | None = None, intro_or_discussion: bool = False) -> list[dict]:
    """Findings for one section (no-LLM review and pre-approval check)."""
    out: list[dict] = []
    for key in sorted(set(cited_keys(text)) - known_keys):
        out.append(finding("1", f"@{key}", f"A citação @{key} não está nas referências do projeto.", "alta",
                           "Erro", "Adicione a referência pela busca (MCP) ou pelo material, ou remova a citação."))
    if kind in ("intro", "discussion", "results") or intro_or_discussion:
        for s in unsupported_claims(text, material_text)[:8]:
            out.append(finding("3", s[:160], "Afirmação que pede evidência, sem citação nem apoio no material.",
                               "média", "Evidência insuficiente",
                               "Cite uma fonte encontrada pela busca ou marque como lacuna."))
    for m in PLACEHOLDER.finditer(text):
        out.append(finding("5", m.group(0), "Pendência no texto.", "baixa", "Imprecisão", "Resolva antes de submeter."))
    if text.strip() and ends_mid_sentence(text):
        last = text.strip().splitlines()[-1][-80:]
        out.append(finding("6", last, "A seção parece terminar no meio de uma frase.", "alta", "Erro"))
    for title in empty_headings(text):
        out.append(finding("7", title, "Título sem conteúdo.", "média", "Estrutura"))
    if word_limit:
        words = len(re.findall(r"[^\W_]+", text))
        if words > word_limit:
            out.append(finding("10", text.strip()[:60], f"{words} palavras; o limite da revista é {word_limit}.",
                               "baixa", "Estrutura", "Considere encurtar."))
    return out


def check_numbers_against_fulltext(text: str, fulltexts: dict[str, str]) -> list[dict]:
    """Rule 4: a number in a sentence that cites @key must appear in that work's full text."""
    out = []
    for s in sentences(text):
        # Only the clause that carries the citation is attributed to the cited work
        # ("X found 45 [@x], while we found 30" → 30 is the author's own number).
        for clause in CLAUSE.split(s):
            keys = [k for k in cited_keys(clause) if k in fulltexts]
            plain = re.sub(r"\[[^\]]*\]", " ", clause)
            nums = [n for n in NUMBER.findall(plain) if len(n.strip()) > 1]
            if not keys or not nums:
                continue
            body = " ".join(fulltexts[k] for k in keys).replace(",", ".")
            missing = [n for n in nums if n.strip().replace(",", ".") not in body]
            if missing:
                out.append(finding("4", s[:160], f"Número(s) {', '.join(missing)} não encontrado(s) no texto completo "
                                   f"de {', '.join('@' + k for k in keys)}.", "alta", "Imprecisão",
                                   f"Confira no artigo citado ou marque {UNVERIFIED_MARK}."))
    return out
