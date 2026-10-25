"""References as data: CSL-JSON is the source of truth (pandoc --citeproc reads it natively).

Section text cites with pandoc syntax: [@silva2023], [@a2020; @b2021, p. 4].
BibTeX is only an import/export format (converted by pandoc)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .sections import fold

# @key not preceded by a word char (so e-mails are not keys); pandoc key charset, simplified.
CITE_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_][\w:.\-/]*[A-Za-z0-9_]|[A-Za-z0-9_])")
STOP = {"a", "an", "the", "o", "os", "as", "um", "uma", "de", "da", "do", "das", "dos", "el", "la", "los", "las",
        "en", "em", "on", "of", "and", "e", "y", "para", "por", "com", "con", "with", "in", "no", "na"}


def cited_keys(text: str) -> list[str]:
    """Keys cited in a section, in order of first appearance."""
    seen: dict[str, None] = {}
    for m in CITE_RE.finditer(text or ""):
        seen.setdefault(m.group(1), None)
    return list(seen)


def _name_parts(name: str) -> dict:
    name = re.sub(r"\s+", " ", (name or "").strip())
    if "," in name:
        family, given = (x.strip() for x in name.split(",", 1))
    else:
        parts = name.split(" ")
        family, given = parts[-1], " ".join(parts[:-1])
    return {"family": family, "given": given} if given else {"family": family}


def make_key(authors: list[str], year, title: str, existing: set[str]) -> str:
    family = _name_parts(authors[0])["family"] if authors else "anon"
    fam = re.sub(r"[^a-z]", "", fold(family)) or "anon"
    word = next((w for w in re.findall(r"[a-z]+", fold(title or "")) if w not in STOP and len(w) > 2), "")
    base = f"{fam}{year or 'sd'}{word}"
    key, n = base, 2
    while key in existing:
        key, n = f"{base}{chr(96 + n)}", n + 1  # silva2023hidroidesb, ...c
    return key


def csl_from_paper(paper: dict, existing: set[str], source: str = "mcp") -> dict:
    """CSL-JSON item from a latam-oa-mcp search result (search_papers/scielo_search/doaj item)."""
    authors = paper.get("authors") or []
    year = str(paper.get("year") or "")[:4]
    item = {
        "id": make_key(authors, year if year.isdigit() else None, paper.get("title", ""), existing),
        "type": "article-journal",
        "title": paper.get("title"),
        "author": [_name_parts(a) for a in authors if a],
        "container-title": paper.get("journal"),
        "DOI": paper.get("doi"),
        "URL": (f"https://doi.org/{paper['doi']}" if paper.get("doi") else (paper.get("links") or [None])[0]),
        "abstract": paper.get("abstract"),
        "caneta": {"source": source, "verified": True, "diamond": paper.get("diamond"),
                   "okf_path": paper.get("okf_path"), "fulltext": paper.get("fulltext"), "ref": paper.get("ref")},
    }
    if year.isdigit():
        item["issued"] = {"date-parts": [[int(year)]]}
    return {k: v for k, v in item.items() if v not in (None, "", [])}


def add(refs: list[dict], items: list[dict]) -> tuple[list[dict], list[str]]:
    """Merge items, deduplicating by DOI or by key. Returns (refs, keys of the given items)."""
    by_doi = {str(r.get("DOI", "")).lower(): r for r in refs if r.get("DOI")}
    keys = {r["id"] for r in refs}
    out_keys = []
    for item in items:
        doi = str(item.get("DOI", "")).lower()
        if doi and doi in by_doi:
            out_keys.append(by_doi[doi]["id"])
            continue
        if item["id"] in keys:
            out_keys.append(item["id"])
            continue
        refs.append(item)
        keys.add(item["id"])
        if doi:
            by_doi[doi] = item
        out_keys.append(item["id"])
    return refs, out_keys


def import_bibtex(path: Path) -> list[dict]:
    """BibTeX/BibLaTeX → CSL-JSON via pandoc. Uploaded entries are marked as not verified."""
    import pypandoc

    raw = pypandoc.convert_file(str(path), "csljson", format="biblatex")
    items = json.loads(raw) if raw.strip() else []
    for it in items:
        it["caneta"] = {"source": "upload", "verified": False}
    return items


def import_ris_or_json(path: Path) -> list[dict]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else []
    else:
        import pypandoc

        items = json.loads(pypandoc.convert_file(str(path), "csljson", format="ris") or "[]")
    for it in items:
        it.setdefault("caneta", {"source": "upload", "verified": False})
    return items


def to_bibtex(refs: list[dict]) -> str:
    import pypandoc

    clean = [{k: v for k, v in r.items() if k != "caneta"} for r in refs]
    return pypandoc.convert_text(json.dumps(clean, ensure_ascii=False), "biblatex", format="csljson")


def label(ref: dict) -> str:
    """Short human label: 'Silva et al. (2023) — Title'."""
    authors = ref.get("author") or []
    first = authors[0].get("family", "") if authors else "s.a."
    etal = " et al." if len(authors) > 2 else (f" & {authors[1].get('family', '')}" if len(authors) == 2 else "")
    year = ((ref.get("issued") or {}).get("date-parts") or [[None]])[0][0] or "s.d."
    return f"{first}{etal} ({year}) — {ref.get('title', '')}"
