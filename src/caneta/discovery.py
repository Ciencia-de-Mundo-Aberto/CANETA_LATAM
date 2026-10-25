"""Literature discovery through latam-oa-mcp, called directly (no LLM needed).

Path 1 — search(): up to 20 Diamond OA papers (SciELO + DOAJ), metadata + abstract, saved as OKF
in <project>/okf/. Path 2 — fetch_fulltext(): PDFs of selected papers converted to markdown in the
same OKF bundle, for comparing results."""

from __future__ import annotations

import asyncio
import re
import threading
from pathlib import Path

from . import references, semantic
from .project import Project

_LOCK = threading.Lock()  # the MCP module keeps its output folder in a global


def _mcp():
    from latam_oa_mcp import server

    return server


def _run(coro):
    return asyncio.run(coro)


def search(project: Project, query: str, sources: list[str] | None = None, year_from: int | None = None,
           year_to: int | None = None, limit: int = 20, sort: str = "recent", diamond_only: bool = True) -> dict:
    server = _mcp()
    with _LOCK:
        server.configure(library=project.path)
        result = _run(server.search_papers(query, sources=sources, year_from=year_from, year_to=year_to,
                                           limit=limit, sort=sort, diamond_only=diamond_only, bundle="okf",
                                           save_okf=True))
    project.log("mcp_search", query=query, returned=result.get("returned"), errors=result.get("errors"))
    known = {str(r.get("DOI", "")).lower() for r in project.references() if r.get("DOI")}
    for p in result.get("papers", []):
        p["in_project"] = bool(p.get("doi")) and p["doi"].lower() in known
    result["papers"] = rerank(project, result.get("papers", []))
    return result


def rerank(project: Project, papers: list[dict]) -> list[dict]:
    """With meaning-based search on, order results by closeness to the author's own text
    (title + introduction), whatever the language of each paper."""
    if not papers or not semantic.enabled():
        return papers
    intro = next((project.read_section(s.id) for s in project.sections if s.kind == "intro"), "")
    anchor = f"{project.title}\n{intro}".strip()
    sims = semantic.similarities(anchor, [f"{p.get('title', '')}. {p.get('abstract') or ''}" for p in papers])
    for p, s in zip(papers, sims, strict=True):
        p["semantic"] = round(s, 3)
    return sorted(papers, key=lambda p: -p["semantic"])


def add_papers(project: Project, papers: list[dict]) -> list[str]:
    """Selected search results → CSL-JSON references (bibliographic data comes from the MCP, never the LLM)."""
    refs = project.references()
    existing = {r["id"] for r in refs}
    items = []
    for p in papers:
        p = {**p, "okf_path": _portable(project, p["okf_path"]) if p.get("okf_path") else None}
        item = references.csl_from_paper(p, existing)
        existing.add(item["id"])
        items.append(item)
    refs, keys = references.add(refs, items)
    project.save_references(refs)
    project.log("references_added", keys=keys, source="mcp")
    return keys


def fetch_fulltext(project: Project, keys: list[str], ocr: bool = False) -> dict:
    refs = project.references()
    by_key = {r["id"]: r for r in refs}
    wanted = [(k, (by_key[k].get("caneta") or {}).get("ref") or by_key[k].get("DOI") or by_key[k].get("URL"))
              for k in keys if k in by_key]
    wanted = [(k, ref) for k, ref in wanted if ref]
    if not wanted:
        return {"papers": [], "error": "nenhuma referência selecionada tem DOI ou link"}
    server = _mcp()
    with _LOCK:
        server.configure(library=project.path)
        result = _run(server.fetch_full_papers([ref for _, ref in wanted], bundle="okf", ocr=ocr))
    by_ref = {p["ref"]: p for p in result.get("papers", [])}
    for key, ref in wanted:
        info = by_ref.get(ref) or {}
        meta = by_key[key].setdefault("caneta", {})
        if info.get("path"):
            meta["okf_path"] = _portable(project, info["path"])
        if info.get("fulltext"):
            meta["fulltext"] = info["fulltext"]
        meta["fetch_status"] = info.get("status")
    project.save_references(refs)
    project.log("mcp_fulltext", keys=keys, with_fulltext=result.get("with_fulltext"))
    return result


def _portable(project: Project, path: str) -> str:
    """Project-relative POSIX path, so a checkpoint opened on another machine/OS still finds it."""
    if _inside(path, project.path):
        return Path(path).resolve().relative_to(project.path.resolve()).as_posix()
    return str(path)


def _inside(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _okf_file(project: Project, ref: dict) -> Path | None:
    meta = ref.get("caneta") or {}
    if meta.get("okf_path"):
        p = Path(str(meta["okf_path"]).replace("\\", "/"))  # older projects stored Windows separators
        p = p if p.is_absolute() else project.path / p
        if p.is_file():
            return p
    doi = ref.get("DOI")
    if doi:
        from latam_oa_mcp import okf

        return okf.find_paper(project.path / "okf", doi)
    return None


def read_fulltext(project: Project, ref: dict) -> str:
    """The '# Full text' part of the OKF concept of a reference ('' when not downloaded)."""
    path = _okf_file(project, ref)
    if not path:
        return ""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^# Full text\s*$(.*)", text, re.M | re.S)
    body = m.group(1) if m else ""
    if "Not fetched yet" in body[:200] or "No PDF could be downloaded" in body[:200]:
        return ""
    return body.strip()
