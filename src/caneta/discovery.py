"""Literature discovery through latam-oa-mcp, called directly (no LLM needed).

Path 1 — search(): up to 20 Diamond OA papers (SciELO + DOAJ), metadata + abstract, saved as OKF
in <project>/okf/. Path 2 — fetch_fulltext(): PDFs of selected papers converted to markdown in the
same OKF bundle, for comparing results.

complete_references() fills in what a citation needs when a reference has no abstract (typical of an
imported .bib): the abstract by DOI (OpenAlex, then Crossref), the text of the page for web references
(news, reports, declarations), and optionally the open full text. reference_context() then gives the
AI the best available basis for each reference."""

from __future__ import annotations

import asyncio
import html
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


def fetch_fulltext(project: Project, keys: list[str], ocr: bool = False, diamond_only: bool = True) -> dict:
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
        result = _run(server.fetch_full_papers([ref for _, ref in wanted], bundle="okf", ocr=ocr,
                                               diamond_only=diamond_only))
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


# ------------------------------------------------------------------ completing references without an abstract
OPENALEX = "https://api.openalex.org"
CROSSREF = "https://api.crossref.org"
HEADERS = {"User-Agent": "CANETA (https://github.com/Ciencia-de-Mundo-Aberto/CANETA_LATAM)"}
PAGE_CHARS = 4000


def _from_inverted_index(inv: dict) -> str:
    """OpenAlex stores abstracts as {word: [positions]}."""
    words = sorted((pos, w) for w, positions in inv.items() for pos in positions)
    return " ".join(w for _, w in words)


def _clean_markup(text: str) -> str:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return re.sub(r"\s+([.,;:!?)])", r"\1", text)


def abstract_for_doi(client, doi: str) -> tuple[str, str]:
    """(abstract, source) from OpenAlex, then Crossref (JATS); ('', '') when neither has one."""
    try:
        r = client.get(f"{OPENALEX}/works/doi:{doi}")
        if r.status_code == 200 and r.json().get("abstract_inverted_index"):
            return _from_inverted_index(r.json()["abstract_inverted_index"]), "openalex"
    except Exception:  # noqa: BLE001 - one source down must not stop the others
        pass
    try:
        r = client.get(f"{CROSSREF}/works/{doi}")
        if r.status_code == 200 and r.json()["message"].get("abstract"):
            jats = re.sub(r"<jats:title>.*?</jats:title>", " ", r.json()["message"]["abstract"], flags=re.S)
            return _clean_markup(jats), "crossref"
    except Exception:  # noqa: BLE001
        pass
    return "", ""


def page_text(client, url: str) -> str:
    """Readable text of a web reference (news, report, declaration): description + paragraphs."""
    try:
        r = client.get(url)
    except Exception:  # noqa: BLE001
        return ""
    if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
        return ""
    page = r.text
    desc = re.findall(r'<meta[^>]+(?:name|property)="(?:description|og:description)"[^>]+content="([^"]+)"', page, re.I)
    page = re.sub(r"<(script|style|nav|header|footer|aside|form)\b.*?</\1>", " ", page, flags=re.S | re.I)
    paras = [_clean_markup(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", page, re.S | re.I)]
    text = " ".join(dict.fromkeys([_clean_markup(html.unescape(d)) for d in desc[:1]] + [p for p in paras if len(p) > 60]))
    return text[:PAGE_CHARS]


LINK = re.compile(r"https?://[^\s}<>\"']+")


def ref_url(ref: dict) -> str:
    """The reference's link: URL, or one written in another field (a .bib @misc often has it in howpublished,
    which pandoc turns into 'publisher': 'Disponível em: https://...')."""
    if ref.get("URL"):
        return ref["URL"]
    for field in ("publisher", "note", "container-title", "archive", "source"):
        m = LINK.search(str(ref.get(field) or ""))
        if m:
            return m.group(0).rstrip(".,;)")
    return ""


def has_basis(project: Project, ref: dict) -> bool:
    meta = ref.get("caneta") or {}
    return bool(norm_text(ref.get("abstract")) or meta.get("page_text") or read_fulltext(project, ref))


def norm_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def complete_references(project: Project, keys: list[str] | None = None, fulltext: bool = False,
                        log=None) -> dict:
    """Fill in references that have no abstract. Never invents anything: every text comes from an open
    source (OpenAlex/Crossref abstract, the reference's own web page, or its open full text)."""
    import httpx

    say = log or (lambda *_: None)
    refs = project.references()
    todo = [r for r in refs if (keys is None or r["id"] in keys) and not has_basis(project, r)]
    stats = {"abstract": 0, "page": 0, "fulltext": 0, "missing": []}
    if not todo:
        return stats
    say(f"Completando {len(todo)} referência(s) sem resumo em fontes abertas…")
    with httpx.Client(timeout=30, follow_redirects=True, headers=HEADERS) as client:
        for r in todo:
            meta = r.setdefault("caneta", {})
            doi = r.get("DOI")
            if doi:
                text, source = abstract_for_doi(client, doi)
                if text:
                    r["abstract"], meta["abstract_source"] = text, source
                    stats["abstract"] += 1
                    say(f"  {r['id']}: resumo ({source})")
                    continue
            elif ref_url(r):
                text = page_text(client, ref_url(r))
                if text:
                    meta["page_text"] = text
                    stats["page"] += 1
                    say(f"  {r['id']}: conteúdo da página")
                    continue
            stats["missing"].append(r["id"])
    project.save_references(refs)
    if fulltext:
        with_doi = [k for k in stats["missing"] if next(r for r in refs if r["id"] == k).get("DOI")]
        if with_doi:
            say(f"Procurando o texto completo aberto de {len(with_doi)} referência(s)…")
            try:  # open copies of any journal: read to support a citation, not suggested as a venue
                fetch_fulltext(project, with_doi, diamond_only=False)
            except Exception as exc:  # noqa: BLE001
                say(f"  texto completo indisponível: {exc}")
            got = [k for k in with_doi if read_fulltext(project, next(r for r in project.references() if r["id"] == k))]
            stats["fulltext"] = len(got)
            stats["missing"] = [k for k in stats["missing"] if k not in got]
    for k in stats["missing"]:
        say(f"  {k}: só o título (nenhuma fonte aberta com resumo ou texto)")
    project.log("references_completed", **stats)
    return stats


def reference_context(project: Project, ref: dict, max_chars: int = 900, section_text: str = "") -> tuple[str, str]:
    """(kind, text): the best basis the AI has to cite a reference — abstract, full-text excerpt, page text,
    or only the title."""
    abstract = norm_text(ref.get("abstract"))
    if abstract:
        return "Resumo", abstract[:max_chars]
    full = read_fulltext(project, ref)
    if full:
        from .writing import fulltext_excerpts

        excerpt = fulltext_excerpts(project, [ref["id"]], section_text or ref.get("title", ""), per_work=max_chars)
        return "Trechos do texto completo", norm_text(excerpt.get(ref["id"]) or full)[:max_chars]
    page = norm_text((ref.get("caneta") or {}).get("page_text"))
    if page:
        return "Conteúdo da página", page[:max_chars]
    return "Só o título", "(nenhum resumo ou texto disponível)"


def basis_kind(project: Project, ref: dict) -> str:
    """Which basis reference_context() would use, without extracting it (for listings)."""
    if norm_text(ref.get("abstract")):
        return "Resumo"
    if read_fulltext(project, ref):
        return "Trechos do texto completo"
    if norm_text((ref.get("caneta") or {}).get("page_text")):
        return "Conteúdo da página"
    return "Só o título"


def basis_preview(project: Project, ref: dict, chars: int = 300) -> str:
    """Start of the basis text, for the author to check it matches the work (sources can be wrong)."""
    meta = ref.get("caneta") or {}
    text = norm_text(ref.get("abstract")) or norm_text(meta.get("page_text")) or norm_text(read_fulltext(project, ref))
    return text[:chars] + ("…" if len(text) > chars else "")
