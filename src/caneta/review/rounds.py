"""Per-section review rounds (adapted and simplified from lccv-paper-maker review/rounds.py + consolidate.py).

Each section is reviewed independently. Reviewers run in parallel; their findings are anchored
(literal excerpt), grouped when they point at the same problem, and the AUTHOR decides each group:
accept (apply the proposed edit) · edit (apply the author's own text) · reject (reason required) · defer.

    reviews/<section>/round-N/outputs/<role>.json   raw anchored findings + discarded ones
    reviews/<section>/round-N/groups.json           groups and the author's decisions
"""

from __future__ import annotations

import difflib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import grounding, prompts, references, style
from ..llm.base import NullProvider, Provider
from ..project import Project, ProjectError, now, read_json, write_json
from ..sections import fold
from . import anchoring
from .schemas import (
    ACCEPTED,
    DEFERRED,
    EDITED,
    FINDING_SCHEMA,
    OPEN,
    RANK,
    REJECTED,
    RESOLVED,
    ROLES,
    norm,
    roles_for,
)

SIMILAR_RATIO = 0.6


class ReviewError(Exception):
    pass


def _dir(project: Project, section_id: str) -> Path:
    return project.path / "reviews" / section_id


def rounds(project: Project, section_id: str) -> list[int]:
    folder = _dir(project, section_id)
    if not folder.is_dir():
        return []
    return sorted(int(p.name[6:]) for p in folder.glob("round-*")
                  if p.name[6:].isdigit() and (p / "groups.json").is_file())


def load_round(project: Project, section_id: str, number: int) -> dict:
    data = read_json(_dir(project, section_id) / f"round-{number}" / "groups.json")
    if not isinstance(data, dict):
        raise ReviewError(f"rodada {number} não encontrada")
    return data


def latest(project: Project, section_id: str) -> dict | None:
    numbers = rounds(project, section_id)
    return load_round(project, section_id, numbers[-1]) if numbers else None


def _save_round(project: Project, section_id: str, number: int, data: dict) -> None:
    counts: dict[str, int] = {}
    for g in data["groups"]:
        counts[g["state"]] = counts.get(g["state"], 0) + 1
    data["counts"] = counts
    write_json(_dir(project, section_id) / f"round-{number}" / "groups.json", data)


# ------------------------------------------------------------------ prompt building
def _context(project: Project, section_id: str) -> dict:
    sec = project.section(section_id)
    abstract = next((project.read_section(s.id) for s in project.sections if s.kind == "abstract" and s.id != sec.id),
                    "")
    journal = project.journal or {}
    jline = journal.get("name") or "ainda não escolhida"
    if journal.get("escopo"):
        jline += f" — escopo: {journal['escopo'][:400]}"
    return {
        "title": project.title,
        "journal": jline,
        "section_list": "; ".join(s.title for s in project.sections),
        "abstract_block": f"- Resumo do artigo: {norm(abstract)[:1500]}" if abstract.strip() else "",
        "section_title": sec.title,
        "section_kind": sec.kind,
        "out_lang": prompts.out_lang(project.lang),
    }


def cited_reference_block(project: Project, text: str, max_chars: int = 700) -> str:
    """Labels + abstracts of the works this section cites (for the references reviewer)."""
    refs = {r["id"]: r for r in project.references()}
    lines = []
    for key in references.cited_keys(text):
        ref = refs.get(key)
        if not ref:
            lines.append(f"- @{key}: (NÃO consta nas referências do projeto)")
            continue
        abstract = norm(ref.get("abstract"))[:max_chars] or "(sem resumo disponível)"
        lines.append(f"- @{key}: {references.label(ref)}\n  Resumo: {abstract}")
    return "\n".join(lines) or "(a seção não cita nenhuma referência)"


def build_prompt(project: Project, section_id: str, role: str, text: str) -> str:
    extra = ""
    if role == "referencias":
        extra = "## Referências citadas nesta seção\n" + cited_reference_block(project, text)
    return prompts.render(
        "review_common", role_label=ROLES[role]["label"], role_instructions=prompts.render(f"role_{role}"),
        extra_block=extra, section_text=text, **_context(project, section_id))


# ------------------------------------------------------------------ grouping
def _similar(a: dict, b: dict) -> bool:
    qa, qb = fold(norm(a["excerpt"])), fold(norm(b["excerpt"]))
    if not qa or not qb:
        return False
    return qa in qb or qb in qa or difflib.SequenceMatcher(None, qa, qb).ratio() > SIMILAR_RATIO


def make_groups(findings: list[dict], number: int) -> list[dict]:
    parent = list(range(len(findings)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(findings)):
        for j in range(i + 1, len(findings)):
            if _similar(findings[i], findings[j]):
                parent[find(i)] = find(j)
    clusters: dict[int, list[dict]] = {}
    for i, f in enumerate(findings):
        clusters.setdefault(find(i), []).append(f)
    ordered = sorted(clusters.values(), key=lambda m: -max(RANK[f["prioridade"]] for f in m))
    groups = []
    for n, members in enumerate(ordered, 1):
        top = max(members, key=lambda f: (RANK[f["prioridade"]], f.get("edit") is not None))
        roles = sorted({f["role"] for f in members})
        groups.append({
            "id": f"G{number}-{n:02d}", "state": OPEN, "prioridade": top["prioridade"], "rotulo": top["rotulo"],
            "excerpt": top["excerpt"], "descricao": top["descricao"], "acao_sugerida": top.get("acao_sugerida"),
            "edit": next((f["edit"] for f in members if f.get("edit")), None), "roles": roles,
            "support": len(roles),
            "members": [{"role": f["role"], "prioridade": f["prioridade"], "descricao": f["descricao"]}
                        for f in members],
        })
    return groups


# ------------------------------------------------------------------ running a round
def run_round(project: Project, section_id: str, provider: Provider, roles: list[str] | None = None,
              include_adversary: bool = False, on_log=None) -> dict:
    sec = project.section(section_id)
    text = project.read_section(section_id)
    if not text.strip():
        raise ReviewError("a seção está vazia")
    number = (rounds(project, section_id) or [0])[-1] + 1
    folder = _dir(project, section_id) / f"round-{number}"
    log = on_log or (lambda _m: None)
    project.set_state(section_id, "in_review")

    # 1) grounding rules (always; this is the whole review in no-AI mode)
    known = {r["id"] for r in project.references()}
    material = material_text(project)
    rule_findings = grounding.check_section(text, sec.kind, known, material)
    kept_rules, _ = anchoring.screen(rule_findings, text, "fundamentacao", cap=20, strict_quote=False)
    write_json(folder / "outputs" / "fundamentacao.json", {"findings": kept_rules})
    findings = list(kept_rules)
    verdicts, errors = {}, {}

    # 1b) grammar/style without AI (LanguageTool), only when the author turned it on
    if style.enabled():
        try:
            kept_lt, _ = anchoring.screen(style.check(text, project.lang), text, "gramatica", cap=25,
                                          strict_quote=False)
            write_json(folder / "outputs" / "gramatica.json", {"findings": kept_lt})
            findings += kept_lt
            log(f"[gramática] {len(kept_lt)} sugestão(ões) do LanguageTool")
        except style.StyleError as exc:
            errors["gramatica"] = str(exc)

    # 2) AI reviewers in parallel
    if not isinstance(provider, NullProvider):
        chosen = roles or roles_for(sec.kind, include_adversary)

        def one(role: str):
            prompt = build_prompt(project, section_id, role, text)
            log(f"[{role}] revisando com {provider.name} {provider.model}…")
            res = provider.complete(prompt, schema=FINDING_SCHEMA, on_log=None)
            project.log_ai("review", provider.name, provider.model, prompt, res.ok, section_id, res.duration_s)
            return role, res

        with ThreadPoolExecutor(max_workers=3) as pool:
            for role, res in pool.map(one, chosen):
                if not res.ok:
                    errors[role] = res.error
                    write_json(folder / "outputs" / f"{role}.error.json", {"error": res.error, "raw": res.text[-2000:]})
                    log(f"[{role}] falhou: {res.error}")
                    continue
                kept, discarded = anchoring.screen((res.data or {}).get("findings"), text, role)
                verdicts[role] = (res.data or {}).get("parecer")
                write_json(folder / "outputs" / f"{role}.json", {"findings": kept, "discarded": discarded,
                                                                 "parecer": verdicts[role]})
                log(f"[{role}] {len(kept)} apontamento(s), {len(discarded)} descartado(s) por falta de âncora")
                findings += kept

    groups = make_groups(findings, number)
    data = {"round": number, "section": section_id, "created_at": now(), "provider": provider.name,
            "model": provider.model, "verdicts": verdicts, "errors": errors, "groups": groups,
            "text_sha": _sha(text)}
    _save_round(project, section_id, number, data)
    project.set_state(section_id, "revising")
    project.log("review_round", section=section_id, round=number, groups=len(groups), errors=list(errors))
    return data


def material_text(project: Project, limit: int = 400_000) -> str:
    """Plain text of the author's material (md/txt/csv/converted PDFs) for 'own results' checks."""
    out, total = [], 0
    folder = project.path / "material"
    for p in sorted(folder.rglob("*")) if folder.is_dir() else []:
        if p.is_file() and p.suffix.lower() in (".md", ".txt", ".csv", ".tex"):
            try:
                chunk = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            out.append(chunk)
            total += len(chunk)
            if total > limit:
                break
    return "\n".join(out)


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ author decisions
STALE_MSG = ("este trecho não está mais no texto (a seção mudou depois da revisão): corrija direto no editor "
             "e clique em 'Já corrigi', ou rejeite/adie")


def mark_stale(text: str, groups: list[dict]) -> list[dict]:
    """Flag open findings whose passage is no longer in the section exactly once (the text was polished,
    edited or rewritten after the review): their edit buttons cannot work."""
    for g in groups:
        target = (g.get("edit") or {}).get("find") or g["excerpt"]
        g["stale"] = g["state"] == OPEN and text.count(target) != 1
    return groups


def decide(project: Project, section_id: str, number: int, group_id: str, action: str,
           replace: str | None = None, reason: str | None = None) -> dict:
    data = load_round(project, section_id, number)
    group = next((g for g in data["groups"] if g["id"] == group_id), None)
    if group is None:
        raise ReviewError("apontamento não encontrado")
    text = project.read_section(section_id)
    if action == "accept":
        edit = group.get("edit")
        if edit:
            if text.count(edit["find"]) != 1:
                raise ReviewError(STALE_MSG)
            project.write_section(section_id, text.replace(edit["find"], edit["replace"], 1), f"revisao {group_id}")
        group["state"] = ACCEPTED
    elif action == "edit":
        if replace is None:
            raise ReviewError("escreva o novo texto")
        target = (group.get("edit") or {}).get("find") or group["excerpt"]
        if text.count(target) != 1:
            raise ReviewError(STALE_MSG)
        project.write_section(section_id, text.replace(target, replace, 1), f"revisao {group_id} editada")
        group["state"] = EDITED
    elif action == "reject":
        if not (reason or "").strip():
            raise ReviewError("informe o motivo da rejeição")
        group["state"] = REJECTED
    elif action == "resolve":
        group["state"] = RESOLVED
    elif action == "defer":
        group["state"] = DEFERRED
    elif action == "reopen":
        group["state"] = OPEN
    else:
        raise ReviewError(f"ação desconhecida: {action}")
    group["decision"] = {"action": action, "reason": reason, "at": now()}
    _save_round(project, section_id, number, data)
    project.log("review_decision", section=section_id, round=number, group=group_id, action=action)
    return group


def open_groups(project: Project, section_id: str) -> int:
    data = latest(project, section_id)
    return sum(1 for g in (data or {}).get("groups", []) if g["state"] == OPEN)


def approve(project: Project, section_id: str) -> list[dict]:
    """Approve (soft): returns the remaining warnings (open groups, placeholders) but never blocks."""
    try:
        sec = project.section(section_id)
    except ProjectError as exc:
        raise ReviewError(str(exc)) from exc
    text = project.read_section(section_id)
    warnings = [f for f in grounding.check_section(text, sec.kind, {r["id"] for r in project.references()})
                if f["rule"] in ("1", "5", "6")]
    if open_groups(project, section_id):
        warnings.append({"descricao": f"{open_groups(project, section_id)} apontamento(s) ainda em aberto."})
    project.set_state(section_id, "approved")
    return warnings
