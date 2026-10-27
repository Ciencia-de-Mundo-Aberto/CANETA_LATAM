"""AI writing actions. Each one returns a PROPOSAL (never writes the section): the author sees a diff
and accepts or discards it. Every proposal goes through the grounding rules first:
invented citation keys are removed, dropped citations are reported, numbers are checked."""

from __future__ import annotations

import difflib
import html
import re
from dataclasses import dataclass, field

from . import grounding, prompts, references, semantic
from .llm.base import NoLLM, NullProvider, Provider
from .project import Project
from .review.anchoring import clean_edit
from .review.schemas import norm

POLISH_SCHEMA = {"texto": "a seção revisada, em Markdown, sem o título", "notas": "o que mudou, em poucas linhas"}
ENRICH_SCHEMA = {"texto": "a seção inteira com as citações, em Markdown, sem o título",
                 "notas": "onde e por que citou cada referência"}
COMPARE_SCHEMA = {"paragrafo": "parágrafo de comparação com citações [@chave]", "notas": "observações"}
EDIT_SCHEMA = {"edits": [{"find": "trecho exato", "replace": "texto novo", "justificativa": "por quê"}],
               "notas": "observações"}


@dataclass
class Proposal:
    kind: str
    section_id: str
    original: str
    proposed: str
    notes: str = ""
    warnings: list[str] = field(default_factory=list)
    edits: list[dict] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    review: dict | None = None  # {"number", "group"} when the proposal fixes a review finding

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class WritingError(Exception):
    pass


def _require_llm(provider: Provider) -> None:
    if isinstance(provider, NullProvider):
        raise NoLLM("Esta ação usa IA. No modo sem IA, escreva o texto você mesmo; a busca de citações e a "
                    "revisão de fundamentação continuam disponíveis.")


def _run(project: Project, provider: Provider, purpose: str, section_id: str, prompt: str, schema: dict,
         on_log=None) -> dict:
    res = provider.complete(prompt, schema=schema, on_log=on_log)
    project.log_ai(purpose, provider.name, provider.model, prompt, res.ok, section_id, res.duration_s)
    if not res.ok:
        raise WritingError(res.error or "falha na IA")
    return res.data or {}


def _ground(project: Project, original: str, proposed: str) -> tuple[str, list[str]]:
    """Strip invented citation keys and report citations that were dropped."""
    known = {r["id"] for r in project.references()}
    proposed, removed = grounding.strip_unknown_keys(proposed, known)
    warnings = []
    if removed:
        warnings.append("Citações inventadas removidas (não estão nas referências): " + ", ".join("@" + k for k in removed))
    lost = grounding.citations_preserved(original, proposed)
    if lost:
        warnings.append("A proposta removeu citações que existiam: " + ", ".join("@" + k for k in lost)
                        + ". Confira antes de aceitar.")
    return proposed.strip() + "\n", warnings


def polish(project: Project, section_id: str, provider: Provider, extra: str = "", on_log=None) -> Proposal:
    _require_llm(provider)
    sec = project.section(section_id)
    text = project.read_section(section_id)
    if not text.strip():
        raise WritingError("a seção está vazia: escreva algo (pode ser em tópicos) antes de formatar")
    prompt = prompts.render("polish", section_title=sec.title, out_lang=prompts.out_lang(project.lang),
                            section_text=text, extra=f"\nInstrução extra do autor: {extra}" if extra else "")
    data = _run(project, provider, "polish", section_id, prompt, POLISH_SCHEMA, on_log)
    proposed, warnings = _ground(project, text, str(data.get("texto") or ""))
    if not proposed.strip():
        raise WritingError("a IA devolveu um texto vazio")
    return Proposal("polish", section_id, text, proposed, str(data.get("notas") or ""), warnings,
                    provider=provider.name, model=provider.model)


def enrich_intro(project: Project, section_id: str, provider: Provider, keys: list[str], max_new: int = 3,
                 on_log=None) -> Proposal:
    """Insert citations ONLY from the selected references (found by the MCP search)."""
    _require_llm(provider)
    refs = {r["id"]: r for r in project.references()}
    chosen = [refs[k] for k in keys if k in refs]
    if not chosen:
        raise WritingError("selecione ao menos uma referência")
    text = project.read_section(section_id)
    block = "\n".join(f"- [@{r['id']}] {references.label(r)}\n  Resumo: {norm(r.get('abstract'))[:900] or '(sem resumo)'}"
                      for r in chosen)
    prompt = prompts.render("enrich_intro", out_lang=prompts.out_lang(project.lang), references=block,
                            max_new=max_new, section_text=text or "(seção vazia: escreva um parágrafo curto)")
    data = _run(project, provider, "enrich_intro", section_id, prompt, ENRICH_SCHEMA, on_log)
    proposed, warnings = _ground(project, text, str(data.get("texto") or ""))
    outside = [k for k in references.cited_keys(proposed) if k not in keys and k not in references.cited_keys(text)]
    if outside:
        warnings.append("Citou referências fora da seleção: " + ", ".join("@" + k for k in outside))
    return Proposal("enrich", section_id, text, proposed, str(data.get("notas") or ""), warnings,
                    provider=provider.name, model=provider.model)


def fulltext_excerpts(project: Project, keys: list[str], section_text: str, per_work: int = 2500) -> dict[str, str]:
    """For each reference with a downloaded full text (OKF), the paragraphs most related to the section."""
    from .discovery import read_fulltext

    words = {w for w in re.findall(r"[^\W\d_]{5,}", section_text.lower())}
    refs = {r["id"]: r for r in project.references()}
    out = {}
    for k in keys:
        full = read_fulltext(project, refs.get(k) or {})
        if not full:
            continue
        paras = [p.strip() for p in re.split(r"\n\s*\n", full) if len(p.strip()) > 80]
        if semantic.enabled() and paras:
            # passages closest in meaning to the author's section (any language), numbers favoured
            sims = semantic.similarities(section_text[:4000], paras)
            rank = {id(p): s + (0.05 if re.search(r"\d", p) else 0) for p, s in zip(paras, sims, strict=True)}
            scored = sorted(paras, key=lambda p: -rank[id(p)])
        else:
            scored = sorted(paras, key=lambda p: (-len(words & set(re.findall(r"[^\W\d_]{5,}", p.lower())))
                                                  - (2 if re.search(r"\d", p) else 0)))
        chunk, size = [], 0
        for p in scored:
            if size + len(p) > per_work:
                break
            chunk.append(p)
            size += len(p)
        out[k] = "\n\n".join(chunk)
    return out


def compare_results(project: Project, section_id: str, provider: Provider, keys: list[str], on_log=None) -> Proposal:
    _require_llm(provider)
    sec = project.section(section_id)
    text = project.read_section(section_id)
    excerpts = fulltext_excerpts(project, keys, text)
    if not excerpts:
        raise WritingError("nenhuma das referências selecionadas tem texto completo baixado; use 'Baixar texto "
                           "completo' na aba Referências")
    block = "\n\n".join(f"### [@{k}]\n{v}" for k, v in excerpts.items())
    prompt = prompts.render("compare_results", out_lang=prompts.out_lang(project.lang), section_title=sec.title,
                            section_text=text, excerpts=block)
    data = _run(project, provider, "compare_results", section_id, prompt, COMPARE_SCHEMA, on_log)
    para = str(data.get("paragrafo") or "").strip()
    if not para:
        raise WritingError(str(data.get("notas") or "a IA não encontrou base para comparação nos trechos"))
    proposed, warnings = _ground(project, text, text.rstrip() + "\n\n" + para)
    full = {k: v for k, v in excerpts.items()}
    for f in grounding.check_numbers_against_fulltext(para, full):
        warnings.append(f["descricao"])
    return Proposal("compare", section_id, text, proposed, str(data.get("notas") or ""), warnings,
                    provider=provider.name, model=provider.model)


def interactive_edit(project: Project, section_id: str, provider: Provider, instruction: str,
                     selection: str = "", on_log=None) -> Proposal:
    _require_llm(provider)
    if not instruction.strip():
        raise WritingError("escreva o que deseja mudar")
    text = project.read_section(section_id)
    sel = f"Trecho selecionado pelo autor (foque nele): <<<{selection}>>>" if selection.strip() else ""
    prompt = prompts.render("interactive_edit", out_lang=prompts.out_lang(project.lang), instruction=instruction,
                            selection_block=sel, section_text=text)
    data = _run(project, provider, "interactive_edit", section_id, prompt, EDIT_SCHEMA, on_log)
    proposed, valid, dropped = text, [], 0
    for e in data.get("edits") or []:
        edit = clean_edit(e, proposed)
        if not edit:
            dropped += 1
            continue
        proposed = proposed.replace(edit["find"], edit["replace"], 1)
        valid.append({**edit, "justificativa": e.get("justificativa", "")})
    proposed, warnings = _ground(project, text, proposed)
    if dropped:
        warnings.append(f"{dropped} edição(ões) descartada(s): o trecho não aparecia exatamente uma vez no texto.")
    if not valid:
        notes = str(data.get("notas") or "").strip()
        raise WritingError(f"A IA não propôs mudanças. Motivo dela: {notes}" if notes
                           else "A IA não propôs nenhuma mudança aplicável.")
    return Proposal("edit", section_id, text, proposed, str(data.get("notas") or ""), warnings, valid,
                    provider=provider.name, model=provider.model)


def fix_finding(project: Project, section_id: str, provider: Provider, finding: dict, number: int,
                on_log=None) -> Proposal:
    """'I agree, fix it for me': the AI rewrites the flagged passage to address a review finding that came
    without a ready replacement. Same anchored edit + diff as an interactive edit; the author still accepts."""
    instruction = (f"Corrija o problema apontado pela revisão ({finding.get('rotulo', '')}): "
                   f"{finding.get('descricao', '')}")
    if finding.get("acao_sugerida"):
        instruction += f" Ação sugerida pelo revisor: {finding['acao_sugerida']}"
    instruction += (" Mude só o necessário, no trecho selecionado ou logo ao redor dele; preserve as citações "
                    "[@chave] e não invente dados, números nem referências.")
    prop = interactive_edit(project, section_id, provider, instruction, finding.get("excerpt", ""), on_log=on_log)
    prop.kind = "review_fix"
    prop.review = {"number": number, "group": finding["id"]}
    return prop


def diff_html(before: str, after: str) -> str:
    """Word-level inline diff (<del>/<ins>) for the proposal preview."""
    a, b = re.findall(r"\S+|\s+", before), re.findall(r"\S+|\s+", after)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape("".join(a[i1:i2])))
        if op in ("delete", "replace"):
            out.append(f"<del>{html.escape(''.join(a[i1:i2]))}</del>")
        if op in ("insert", "replace"):
            out.append(f"<ins>{html.escape(''.join(b[j1:j2]))}</ins>")
    return "".join(out).replace("\n", "<br>")
