"""Writes downloaded papers as an Open Knowledge Format (OKF v0.2) bundle.

Spec: https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md

Bundle layout:
    <bundle>/
      index.md                      okf_version: "0.2"; lists papers and journals
      log.md                        newest-first history of downloads
      papers/index.md
      papers/<slug>.md              type: Scientific Article (metadata, abstract, full text)
      journals/index.md
      journals/<issn>.md            type: Journal (Diamond OA evidence from DOAJ)
      references/papers/<slug>.pdf  original PDFs, cited from each paper's `sources`
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

OKF_VERSION = "0.2"
PRODUCER = "latam-oa-mcp/0.2.0"
FULLTEXT_METHODS = {
    "pdf-layout": "Converted automatically from the {pdf} (structured markdown).",
    "pdf-plain-text": "Extracted as plain text from the {pdf}; headings and tables are not structured.",
    "pdf-ocr": "Obtained by OCR of the scanned {pdf}; expect recognition errors (merged words, wrong accents).",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(title: str, key: str) -> str:
    text = unicodedata.normalize("NFKD", title or "paper")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:70].rstrip("-") or "paper"
    return f"{text}-{hashlib.sha1(key.encode()).hexdigest()[:6]}"


# ------------------------------------------------------------------- HTML landing pages

_META_RE = re.compile(r"<meta\s+[^>]*>", re.I | re.S)
_ATTR_RE = re.compile(r'([\w:.-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', re.S)


def parse_meta(page: str) -> dict[str, list[str]]:
    """Collect <meta name/property=... content=...> tags (Highwire citation_*, DC.*, og:*)."""
    out: dict[str, list[str]] = {}
    for tag in _META_RE.findall(page):
        attrs = {m.group(1).lower(): html.unescape(m.group(2) if m.group(2) is not None else m.group(3))
                 for m in _ATTR_RE.finditer(tag)}
        name = (attrs.get("name") or attrs.get("property") or "").lower()
        if name and attrs.get("content"):
            out.setdefault(name, []).append(attrs["content"].strip())
    return out


def pdf_candidates(page: str, meta: dict[str, list[str]], base_url: str) -> list[str]:
    """PDF URLs advertised by a landing page: citation_pdf_url first, then OJS download links."""
    from urllib.parse import urljoin

    urls = list(meta.get("citation_pdf_url", []))
    for href in re.findall(r'href=["\']([^"\']*/article/download/[^"\']+)["\']', page, re.I):
        urls.append(html.unescape(href))
    seen, out = set(), []
    for u in urls:
        full = urljoin(base_url, u)
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out[:4]


def meta_metadata(meta: dict[str, list[str]]) -> dict:
    """Paper metadata from Highwire/Dublin Core tags, used when Crossref has gaps."""
    first = lambda *keys: next((meta[k][0] for k in keys if meta.get(k)), None)  # noqa: E731
    year = first("citation_publication_date", "citation_date", "dc.date", "citation_online_date")
    keywords = []
    for k in meta.get("citation_keywords", []):
        keywords += [x.strip() for x in re.split(r"[;,]", k) if x.strip()]
    return {
        "title": first("citation_title", "dc.title", "og:title"),
        "authors": meta.get("citation_author") or meta.get("dc.creator") or [],
        "journal": first("citation_journal_title"),
        "issns": meta.get("citation_issn", []),
        "doi": first("citation_doi"),
        "year": (re.search(r"\d{4}", year).group(0) if year and re.search(r"\d{4}", year) else None),
        "abstract": first("citation_abstract", "dc.description", "description", "og:description"),
        "keywords": keywords,
        "language": first("citation_language", "dc.language"),
    }


# ------------------------------------------------------------------- markdown helpers

def demote_headings(md: str) -> str:
    """Push converted headings one level down so they nest under '# Full text'."""
    out, fenced = [], False
    for line in md.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and re.match(r"^#{1,5} ", line):
            line = "#" + line
        out.append(line)
    return "\n".join(out)


def first_sentence(text: str | None, limit: int = 280) -> str | None:
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(abstract|resumo|resumen)[\s.:]+", "", text, flags=re.I)
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    s = m.group(1) if m else text
    if len(re.findall(r"[^\W\d_]", s)) < 15:  # junk like "." from a malformed abstract
        return None
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _frontmatter(data: dict) -> str:
    clean = {k: v for k, v in data.items() if v not in (None, "", [], {})}
    return "---\n" + yaml.safe_dump(clean, sort_keys=False, allow_unicode=True, width=10_000) + "---\n"


def _read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    try:
        return yaml.safe_load(m.group(1)) or {} if m else {}
    except yaml.YAMLError:
        return {}


def find_paper(bundle: Path, doi: str | None, url: str | None = None) -> Path | None:
    """Existing paper concept with this DOI (or resource/landing URL), if any."""
    folder = bundle / "papers"
    if not folder.exists() or not (doi or url):
        return None
    doi = (doi or "").lower()
    for p in folder.glob("*.md"):
        if p.name == "index.md":
            continue
        fm = _read_frontmatter(p)
        if doi and str(fm.get("doi") or "").lower() == doi:
            return p
        if url and (fm.get("resource") == url or any(s.get("resource") == url for s in fm.get("sources") or [])):
            return p
    return None


def read_paper(path: Path) -> tuple[dict, str | None]:
    """Frontmatter and abstract of a saved paper concept."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^# Abstract\n\n(.+?)\n\n(?:# |\Z)", text, re.S | re.M)
    return _read_frontmatter(path), (m.group(1).strip() if m else None)


# ------------------------------------------------------------------- concept writers

def write_journal(bundle: Path, journal: dict) -> str:
    """Write journals/<issn>.md and return its bundle-relative link."""
    issn = journal.get("eissn") or journal.get("pissn") or journal["issn"]
    path = bundle / "journals" / f"{issn}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    diamond = journal.get("diamond")
    fm = {
        "type": "Journal",
        "title": journal.get("title"),
        "description": (
            f"{'Diamond open access' if diamond else 'Open access'} journal"
            f"{' published by ' + journal['publisher'] if journal.get('publisher') else ''}"
            f"{' (' + journal['country'] + ')' if journal.get('country') else ''}."
        ),
        "resource": journal.get("url"),
        "tags": ["diamond-open-access"] if diamond else [],
        "issn": [i for i in (journal.get("pissn"), journal.get("eissn")) if i],
        "diamond_open_access": diamond,
        "has_apc": journal.get("has_apc"),
        "has_other_charges": journal.get("has_other_charges"),
        "license": journal.get("license"),
        "subjects": journal.get("subjects"),
        "generated": {"by": PRODUCER, "at": now_iso()},
        "sources": [{"id": "doaj", "resource": journal.get("doaj_url"), "title": "DOAJ journal record",
                     "author": "process:doaj"}],
    }
    body = [
        f"# {journal.get('title')}",
        "",
        f"Open access model: **{'Diamond (no APC, no other author charges)' if diamond else 'not confirmed as Diamond'}**,"
        " according to DOAJ.[^doaj]",
        "",
        f"- Journal site: {journal.get('url') or '-'}",
        f"- Author instructions: {journal.get('author_instructions') or '-'}",
        f"- Subjects: {', '.join(journal.get('subjects') or []) or '-'}",
        "",
        "[^doaj]: DOAJ journal record",
        "",
    ]
    path.write_text(_frontmatter(fm) + "\n" + "\n".join(body), encoding="utf-8")
    return f"/journals/{issn}.md"


def write_paper(bundle: Path, slug: str, meta: dict, journal_link: str | None,
                fulltext_md: str | None, pdf_rel: str | None, pdf_url: str | None,
                landing_url: str | None, diamond: bool | None, fulltext: str = "abstract-only") -> Path:
    path = bundle / "papers" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    doi = meta.get("doi")
    resource = f"https://doi.org/{doi}" if doi else landing_url
    sources = []
    if pdf_rel:
        sources.append({"id": "pdf", "resource": pdf_rel, "title": "Original PDF (local copy)"})
    if pdf_url:
        sources.append({"id": "publisher-pdf", "resource": pdf_url, "title": "Publisher PDF",
                        "author": meta.get("journal")})
    if landing_url:
        sources.append({"id": "landing", "resource": landing_url, "title": "Publisher landing page"})
    if journal_link:
        sources.append({"id": "journal", "resource": journal_link, "title": "Journal record (Diamond OA evidence)"})

    fm = {
        "type": "Scientific Article",
        "title": meta.get("title"),
        "description": first_sentence(meta.get("abstract")) or
        f"Article in {meta.get('journal') or 'an open access journal'} ({meta.get('year') or 'n.d.'}).",
        "resource": resource,
        "tags": (meta.get("keywords") or [])[:12],
        "authors": meta.get("authors") or [],
        "year": int(meta["year"]) if str(meta.get("year") or "").isdigit() else meta.get("year"),
        "journal": meta.get("journal"),
        "issn": meta.get("issns") or [],
        "doi": doi,
        "language": meta.get("language"),
        "license": meta.get("license"),
        "open_access": "diamond" if diamond else ("not-diamond" if diamond is False else "unverified"),
        "fulltext": fulltext,
        "status": "stable" if fulltext_md else "draft",
        "generated": {"by": PRODUCER, "at": now_iso()},
        "sources": sources,
    }
    body = [f"# {meta.get('title') or 'Untitled'}", ""]
    byline = "; ".join(meta.get("authors") or [])
    if byline:
        body += [f"**Authors:** {byline}", ""]
    venue = meta.get("journal") or "-"
    if journal_link:
        venue = f"[{venue}]({journal_link})"
    body += [f"**Published in:** {venue}, {meta.get('year') or 'n.d.'}" + (f" · DOI [{doi}](https://doi.org/{doi})" if doi else ""), ""]
    if meta.get("abstract"):
        body += ["# Abstract", "", re.sub(r"\s+", " ", meta["abstract"]).strip(), ""]
    if fulltext_md:
        body += [
            "# Full text",
            "",
            "> " + FULLTEXT_METHODS.get(fulltext, "Converted from the {pdf}.").format(
                pdf=f"[original PDF]({pdf_rel})") + f" Produced by {PRODUCER}.[^pdf] "
            "Check tables, figures and equations against the PDF.",
            "",
            demote_headings(fulltext_md).strip(),
            "",
            "[^pdf]: Original PDF (local copy)",
            "",
        ]
    else:
        if fulltext == "not-requested":
            ref = meta.get("doi") or landing_url
            reason = ("Not fetched yet: this concept holds metadata and abstract only. To add the full "
                      f"text, call the MCP tool fetch_full_papers with papers=[\"{ref}\"] and this bundle.")
        elif pdf_rel:
            reason = ("The PDF was downloaded but no text could be extracted (scanned PDF without OCR, or "
                      f"conversion failed); read the [original PDF]({pdf_rel}).")
        else:
            reason = "No PDF could be downloaded; only the metadata and abstract are available."
        body += ["# Full text", "", reason + (f" See the [publisher page]({landing_url})." if landing_url else ""), ""]
    path.write_text(_frontmatter(fm) + "\n" + "\n".join(body), encoding="utf-8")
    return path


# ------------------------------------------------------------------- index and log

def _index_section(folder: Path, heading: str) -> list[str]:
    lines = [f"# {heading}", ""]
    for p in sorted(folder.glob("*.md")):
        if p.name in ("index.md", "log.md"):
            continue
        fm = _read_frontmatter(p)
        title = fm.get("title") or p.stem
        extra = f" ({fm['year']})" if fm.get("year") else ""
        desc = fm.get("description") or ""
        lines.append(f"* [{title}{extra}]({p.name}) - {desc}")
    return lines + [""]


def rebuild_indexes(bundle: Path) -> None:
    papers, journals = bundle / "papers", bundle / "journals"
    if papers.exists():
        (papers / "index.md").write_text("\n".join(_index_section(papers, "Papers")), encoding="utf-8")
    if journals.exists():
        (journals / "index.md").write_text("\n".join(_index_section(journals, "Journals")), encoding="utf-8")
    n_papers = len([p for p in papers.glob("*.md") if p.name != "index.md"]) if papers.exists() else 0
    n_full = sum(1 for p in papers.glob("*.md") if p.name != "index.md"
                 and _read_frontmatter(p).get("fulltext") in FULLTEXT_METHODS) if papers.exists() else 0
    n_journals = len([p for p in journals.glob("*.md") if p.name != "index.md"]) if journals.exists() else 0
    root = [
        "---", f'okf_version: "{OKF_VERSION}"', "---", "",
        "# Contents", "",
        f"* [Papers](papers/) - {n_papers} papers ({n_full} with full text; the rest have metadata and abstract)",
        f"* [Journals](journals/) - {n_journals} journals, with Diamond OA evidence from DOAJ",
        "* [Update log](log.md) - download history",
        "",
    ]
    (bundle / "index.md").write_text("\n".join(root), encoding="utf-8")


def append_log(bundle: Path, entries: list[str]) -> None:
    if not entries:
        return
    path = bundle / "log.md"
    today = f"## {date.today().isoformat()}"
    text = path.read_text(encoding="utf-8") if path.exists() else "# Bundle Update Log\n"
    head, _, rest = text.partition("\n")
    new_lines = [f"* {e}" for e in entries]
    if rest.lstrip().startswith(today):
        # Insert under today's existing heading.
        rest = rest.lstrip()
        after = rest[len(today):]
        text = f"{head}\n\n{today}\n" + "\n".join(new_lines) + after
    else:
        text = f"{head}\n\n{today}\n" + "\n".join(new_lines) + "\n" + ("\n" + rest.lstrip() if rest.strip() else "")
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
