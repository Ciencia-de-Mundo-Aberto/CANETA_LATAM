"""MCP server for Latin American open access sources.

Sources (all public, no API key):
- LA Referencia  (VuFind API)  https://buscador.lareferencia.info/api/v1
- Oasisbr/IBICT  (VuFind API)  https://oasisbr.ibict.br/vufind/api/v1
- SciELO         (ArticleMeta) https://articlemeta.scielo.org/api/v1
- DOAJ           (search API)  https://doaj.org/api
- Crossref       (REST API)    https://api.crossref.org  (used for SciELO keyword search)
Plus a tool that searches the local journal template index (_INDICE_GERAL.csv).

SciELO has no public keyword-search API (search.scielo.org sits behind a bot
challenge), so scielo_search builds the list of every SciELO journal from
ArticleMeta and runs the keyword search in DOAJ and Crossref restricted to
those ISSNs.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import re
import sys
import time
import unicodedata
from datetime import date
from html import unescape as html_unescape
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urljoin

import httpx
from mcp.server.mcpserver import MCPServer

from . import okf

USER_AGENT = "latam-oa-mcp/0.1 (+open access research agent)"
TIMEOUT = httpx.Timeout(45.0, connect=15.0)

VUFIND = {
    "lareferencia": "https://buscador.lareferencia.info/api/v1",
    "oasisbr": "https://oasisbr.ibict.br/vufind/api/v1",
}
ARTICLEMETA = "https://articlemeta.scielo.org/api/v1"
DOAJ = "https://doaj.org/api"
CROSSREF = "https://api.crossref.org"
# Optional: an e-mail puts Crossref requests in its faster "polite" pool.
CROSSREF_MAILTO = os.environ.get("CROSSREF_MAILTO", "")

CACHE_DIR = Path(os.environ.get("LATAM_OA_CACHE_DIR", Path.home() / ".cache" / "latam-oa-mcp"))
CATALOG_FILE = CACHE_DIR / "scielo_journals.json"
DIAMOND_FILE = CACHE_DIR / "diamond_status.json"
CATALOG_MAX_AGE = 30 * 24 * 3600

# Downloaded papers go to <LIBRARY>/<bundle>/ as OKF bundles.
LIBRARY = Path(os.environ.get("LATAM_OA_LIBRARY", Path.home() / "Documents" / "diamond_papers_okf"))
MAX_RESULTS = 20
# "recent" sorting picks the newest papers among this many top relevance-ranked matches,
# so recency never pulls in papers that only mention a term in passing.
RELEVANCE_POOL = 60
MAX_FULLTEXT = 20
MAX_PDF_BYTES = 60 * 1024 * 1024
OCR_MAX_PAGES = 40       # OCR runs ~10-15 s/page, so scanned PDFs are capped
OCR_TIMEOUT = 1200
BROWSER_UA = "Mozilla/5.0 (compatible; latam-oa-mcp/0.2; open access research)"

TEMPLATES_DIR = Path(
    os.environ.get("LATAM_OA_TEMPLATES_DIR", Path.home() / "Documents" / "journal_templates")
)

VUFIND_FIELDS = [
    "id", "title", "authors", "publicationDates", "summary", "urls",
    "formats", "languages", "subjects", "institutions",
]

Format = Literal[
    "article", "masterThesis", "doctoralThesis", "bachelorThesis",
    "conferenceObject", "book", "bookPart", "report", "other",
]
SearchType = Literal["AllFields", "Title", "Author", "Subject"]

mcp = MCPServer("latam-oa", version="0.2.0")
logging.getLogger("httpx").setLevel(logging.WARNING)


def configure(library: str | Path | None = None, templates: str | Path | None = None,
              cache: str | Path | None = None) -> None:
    """Point the module at other folders at runtime (the env vars are only read at import).

    Used by apps that embed these tools, e.g. CANETA writes each project's OKF bundle inside
    the project folder: configure(library=<project>) + bundle="okf".
    """
    global LIBRARY, TEMPLATES_DIR, CACHE_DIR, CATALOG_FILE, DIAMOND_FILE, _diamond_cache, _catalog_mem
    if library is not None:
        LIBRARY = Path(library)
    if templates is not None:
        TEMPLATES_DIR = Path(templates)
    if cache is not None:
        CACHE_DIR = Path(cache)
        CATALOG_FILE = CACHE_DIR / "scielo_journals.json"
        DIAMOND_FILE = CACHE_DIR / "diamond_status.json"
        _diamond_cache = None
        _catalog_mem = None


# --------------------------------------------------------------------------- http

def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )


async def _get_json(url: str, params: Any = None, client: httpx.AsyncClient | None = None) -> Any:
    """GET JSON, retrying twice on 429/5xx (rate limits are common on DOAJ/Crossref)."""
    if client is None:
        async with _client() as c:
            return await _get_json(url, params, c)
    for attempt in range(4):
        r = await client.get(url, params=params)
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            retry_after = r.headers.get("Retry-After", "")
            wait = float(retry_after) if retry_after.isdigit() else 2.0 * 2 ** attempt
            await asyncio.sleep(min(wait, 20.0))
            continue
        r.raise_for_status()
        return r.json()


def _error(source: str, exc: Exception) -> dict:
    if isinstance(exc, httpx.HTTPStatusError):
        return {"error": f"{source} returned HTTP {exc.response.status_code}", "url": str(exc.request.url)}
    return {"error": f"{source} request failed: {type(exc).__name__}: {exc}"}


def _year(a: dict) -> int:
    y = str(a.get("year") or "")[:4]
    return int(y) if y.isdigit() else 0


def _pick(ranked: list[dict], limit: int, sort: str) -> list[dict]:
    """Take `limit` items from a relevance-ranked list, newest first when sort == "recent"."""
    if sort == "recent":
        ranked = sorted(ranked, key=_year, reverse=True)  # stable: ties keep relevance order
    return ranked[:limit]


def _clip(text: str | None, n: int) -> str | None:
    if not text:
        return text
    return text if len(text) <= n else text[: n - 1] + "…"


# ------------------------------------------------------------------- VuFind (LA Referencia / Oasisbr)

def _vufind_record(rec: dict, abstract_chars: int) -> dict:
    authors = rec.get("authors") or {}
    names: list[str] = []
    for key in ("primary", "secondary", "corporate"):
        group = authors.get(key) or {}
        names.extend(group.keys() if isinstance(group, dict) else group)
    summary = [s for s in rec.get("summary") or [] if s and len(s) > 3]
    return {
        "id": rec.get("id"),
        "title": rec.get("title"),
        "authors": names,
        "year": (rec.get("publicationDates") or [None])[0],
        "type": rec.get("formats"),
        "languages": rec.get("languages"),
        "institutions": rec.get("institutions"),
        "subjects": [" / ".join(s) for s in rec.get("subjects") or []],
        "abstract": _clip(summary[0], abstract_chars) if summary else None,
        "urls": [u.get("url") for u in rec.get("urls") or [] if u.get("url")],
    }


async def _vufind_search(
    source: str, query: str, search_type: str, fmt: str | None, year_from: int | None,
    year_to: int | None, extra_filters: list[str], limit: int, page: int,
) -> dict:
    params: list[tuple[str, str]] = [
        ("lookfor", query), ("type", search_type),
        ("limit", str(max(1, min(limit, 50)))), ("page", str(max(1, page))),
    ]
    params += [("field[]", f) for f in VUFIND_FIELDS]
    if fmt:
        params.append(("filter[]", f'format:"{fmt}"'))
    if year_from or year_to:
        params.append(("filter[]", f'publishDate:"[{year_from or "*"} TO {year_to or "*"}]"'))
    params += [("filter[]", f) for f in extra_filters]
    try:
        data = await _get_json(f"{VUFIND[source]}/search", params)
    except Exception as exc:  # noqa: BLE001
        return _error(source, exc)
    return {
        "source": source,
        "total": data.get("resultCount", 0),
        "page": page,
        "records": [_vufind_record(r, 600) for r in data.get("records") or []],
    }


@mcp.tool()
async def search_lareferencia(
    query: str,
    search_type: SearchType = "AllFields",
    format: Format | None = None,
    country: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 10,
    page: int = 1,
) -> dict:
    """Search LA Referencia, the Latin American network of open access repositories
    (articles, theses and dissertations from Brazil, Argentina, Chile, Colombia, Mexico,
    Peru, Ecuador, etc.). Repository records carry no ISSN, so they are NOT filtered by
    Diamond status; for Diamond-only journal articles use scielo_search or
    doaj_search_articles. Pass DOIs/URLs of interesting records to download_papers.

    Args:
        query: Search terms (Portuguese, Spanish or English).
        search_type: Field to search: AllFields, Title, Author or Subject.
        format: Restrict document type, e.g. "article", "masterThesis", "doctoralThesis".
        country: Restrict to one national node, e.g. "Brasil", "Argentina", "Colombia", "México", "Chile", "Perú".
        year_from: First publication year (inclusive).
        year_to: Last publication year (inclusive).
        limit: Results per page (1-50).
        page: Page number, starting at 1.
    """
    extra = [f'network_name_str:"{country}"'] if country else []
    return await _vufind_search("lareferencia", query, search_type, format, year_from, year_to, extra, limit, page)


@mcp.tool()
async def search_oasisbr(
    query: str,
    search_type: SearchType = "AllFields",
    format: Format | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 10,
    page: int = 1,
) -> dict:
    """Search Oasisbr (IBICT), the Brazilian portal of open access scientific output:
    journal articles, theses, dissertations and repository items from hundreds of
    Brazilian institutions. Not filtered by Diamond status (records carry no ISSN).

    Args:
        query: Search terms.
        search_type: Field to search: AllFields, Title, Author or Subject.
        format: Restrict document type, e.g. "article", "masterThesis", "doctoralThesis", "bachelorThesis".
        year_from: First publication year (inclusive).
        year_to: Last publication year (inclusive).
        limit: Results per page (1-50).
        page: Page number, starting at 1.
    """
    return await _vufind_search("oasisbr", query, search_type, format, year_from, year_to, [], limit, page)


@mcp.tool()
async def get_repository_record(source: Literal["lareferencia", "oasisbr"], record_id: str) -> dict:
    """Get the full record (complete abstract, links) for an id returned by
    search_lareferencia or search_oasisbr."""
    params = [("id", record_id)] + [("field[]", f) for f in VUFIND_FIELDS]
    try:
        data = await _get_json(f"{VUFIND[source]}/record", params)
    except Exception as exc:  # noqa: BLE001
        return _error(source, exc)
    records = data.get("records") or []
    if not records:
        return {"error": f"record {record_id!r} not found in {source}"}
    return _vufind_record(records[0], 20000)


# ------------------------------------------------------------------- SciELO ArticleMeta

def _isis(obj: dict, tag: str) -> list[dict]:
    return obj.get(tag) or []


def _isis_first(obj: dict, tag: str) -> str | None:
    vals = _isis(obj, tag)
    return vals[0].get("_") if vals else None


def _scielo_journal(j: dict) -> dict:
    return {
        "title": _isis_first(j, "v100"),
        "abbreviation": _isis_first(j, "v150"),
        "issns": j.get("issns"),
        "collection": j.get("collection"),
        "publisher": [p.get("_") for p in _isis(j, "v480")],
        "country": _isis_first(j, "v310"),
        "subject_areas": [s.get("_") for s in _isis(j, "v441")],
        "status": _isis_first(j, "v50"),
        "url": _isis_first(j, "v690"),
    }


def _scielo_article(a: dict, abstract_chars: int) -> dict:
    art = a.get("article") or {}
    authors = []
    for au in _isis(art, "v10"):
        name = ", ".join(x for x in (au.get("s"), au.get("n")) if x)
        authors.append({"name": name, "orcid": au.get("k")} if au.get("k") else {"name": name})
    abstracts = {}
    for ab in _isis(art, "v83"):
        text = ab.get("a") or ab.get("_")
        if text:
            abstracts[ab.get("l", "?")] = _clip(text, abstract_chars)
    keywords: dict[str, list[str]] = {}
    for kw in _isis(art, "v85"):
        if kw.get("k"):
            keywords.setdefault(kw.get("l", "?"), []).append(kw["k"])
    journal = a.get("title") or {}
    return {
        "pid": a.get("code"),
        "collection": a.get("collection"),
        "document_type": a.get("document_type"),
        "titles": {t.get("l", "?"): t.get("_") for t in _isis(art, "v12")},
        "authors": authors,
        "journal": _isis_first(journal, "v100"),
        "issns": journal.get("issns"),
        "publication_date": a.get("publication_date"),
        "doi": _isis_first(art, "v237") or a.get("doi"),
        "languages": [x.get("_") for x in _isis(art, "v40")],
        "abstracts": abstracts,
        "keywords": keywords,
        "fulltext": a.get("fulltexts"),
    }


@mcp.tool()
async def scielo_list_collections() -> dict:
    """List SciELO network collections (country codes like scl=Brazil, arg=Argentina,
    col=Colombia, mex=Mexico, chl=Chile, prt=Portugal, spa=Spain, ...) with journal and
    document counts. Use the code as the `collection` argument of the other scielo_* tools."""
    try:
        data = await _get_json(f"{ARTICLEMETA}/collection/identifiers/")
    except Exception as exc:  # noqa: BLE001
        return _error("scielo", exc)
    return {
        "collections": [
            {
                "code": c.get("code"),
                "name": (c.get("name") or {}).get("pt") or c.get("original_name"),
                "domain": c.get("domain"),
                "active": c.get("is_active"),
                "documents": c.get("document_count"),
                "current_journals": (c.get("journal_count") or {}).get("current"),
            }
            for c in data
            if c.get("type") == "journals"
        ]
    }


@mcp.tool()
async def scielo_get_journal(issn: str, collection: str = "scl") -> dict:
    """Get SciELO metadata for a journal by ISSN (print or electronic).

    Args:
        issn: Journal ISSN, e.g. "1519-6984".
        collection: SciELO collection code (default "scl" = Brazil).
    """
    try:
        data = await _get_json(f"{ARTICLEMETA}/journal/", {"collection": collection, "issn": issn})
    except Exception as exc:  # noqa: BLE001
        return _error("scielo", exc)
    if not data:
        return {"error": f"ISSN {issn} not found in SciELO collection {collection!r}"}
    return _scielo_journal(data[0] if isinstance(data, list) else data)


@mcp.tool()
async def scielo_list_articles(
    issn: str,
    collection: str = "scl",
    from_date: str | None = None,
    until_date: str | None = None,
    limit: int = 10,
    offset: int = 0,
    with_metadata: bool = True,
) -> dict:
    """List articles of a SciELO journal. Note: ArticleMeta filters by the date the record
    was processed/updated in SciELO, which is usually close to the publication date.

    Args:
        issn: Journal ISSN.
        collection: SciELO collection code (default "scl" = Brazil).
        from_date: Start date YYYY-MM-DD (processing date).
        until_date: End date YYYY-MM-DD (processing date).
        limit: Number of articles (1-20 with metadata, up to 1000 ids only).
        offset: Pagination offset.
        with_metadata: If true, fetch title/authors/DOI/abstract for each article (slower).
    """
    params: dict[str, Any] = {"collection": collection, "issn": issn, "offset": offset}
    params["limit"] = max(1, min(limit, 20 if with_metadata else 1000))
    if from_date:
        params["from"] = from_date
    if until_date:
        params["until"] = until_date
    try:
        data = await _get_json(f"{ARTICLEMETA}/article/identifiers/", params)
    except Exception as exc:  # noqa: BLE001
        return _error("scielo", exc)
    ids = [o["code"] for o in data.get("objects") or []]
    result: dict[str, Any] = {"total": (data.get("meta") or {}).get("total"), "offset": offset}
    if not with_metadata:
        result["pids"] = ids
        return result
    articles = []
    for pid in ids:
        art = await scielo_get_article(pid, collection, abstract_chars=600)
        articles.append(art)
    result["articles"] = articles
    return result


@mcp.tool()
async def scielo_get_article(pid: str, collection: str = "scl", abstract_chars: int = 20000) -> dict:
    """Get a SciELO article by PID (e.g. "S1519-69842024000100535"): titles, authors with
    ORCID, DOI, abstracts and keywords per language, and full-text HTML/PDF links."""
    try:
        data = await _get_json(
            f"{ARTICLEMETA}/article/", {"collection": collection, "code": pid, "format": "json"}
        )
    except Exception as exc:  # noqa: BLE001
        return _error("scielo", exc)
    if not data:
        return {"error": f"PID {pid} not found in SciELO collection {collection!r}"}
    return _scielo_article(data, abstract_chars)


# ------------------------------------------------------------------- DOAJ

def _doaj_journal(res: dict) -> dict:
    b = res.get("bibjson") or {}
    apc = b.get("apc") or {}
    other = b.get("other_charges") or {}
    has_apc = apc.get("has_apc")
    has_other = other.get("has_other_charges")
    return {
        "title": b.get("title"),
        "pissn": b.get("pissn"),
        "eissn": b.get("eissn"),
        "publisher": (b.get("publisher") or {}).get("name"),
        "country": (b.get("publisher") or {}).get("country"),
        "subjects": [s.get("term") for s in b.get("subject") or []],
        "languages": b.get("language"),
        "license": [l.get("type") for l in b.get("license") or []],
        "has_apc": has_apc,
        "apc_max": apc.get("max"),
        "has_other_charges": has_other,
        "diamond": has_apc is False and not has_other,
        "url": (b.get("ref") or {}).get("journal"),
        "author_instructions": (b.get("ref") or {}).get("author_instructions"),
        "doaj_url": f"https://doaj.org/toc/{b.get('eissn') or b.get('pissn')}",
    }


def _doaj_article(res: dict) -> dict:
    b = res.get("bibjson") or {}
    j = b.get("journal") or {}
    doi = next((i.get("id") for i in b.get("identifier") or [] if i.get("type") == "doi"), None)
    links = [l.get("url") for l in b.get("link") or [] if l.get("url")]
    return {
        "doaj_id": res.get("id"),
        "title": b.get("title"),
        "authors": [a.get("name") for a in b.get("author") or []],
        "journal": j.get("title"),
        "issns": [i.upper() for i in j.get("issns") or []],
        "country": j.get("country"),
        "year": b.get("year"),
        "doi": doi,
        "keywords": b.get("keywords"),
        "abstract": _clip(b.get("abstract"), 1500),
        "fulltext_links": links,
    }


# ------------------------------------------------------------------- Diamond OA status (DOAJ)

_diamond_cache: dict[str, dict] | None = None


def _load_diamond_cache() -> dict[str, dict]:
    global _diamond_cache
    if _diamond_cache is None:
        _diamond_cache = {}
        try:
            data = json.loads(DIAMOND_FILE.read_text(encoding="utf-8"))
            cutoff = time.time() - CATALOG_MAX_AGE
            _diamond_cache = {k: v for k, v in data.items() if v.get("checked", 0) > cutoff}
        except (OSError, ValueError):
            pass
    return _diamond_cache


async def _diamond_lookup(client: httpx.AsyncClient, issns: list[str]) -> dict[str, dict]:
    """Diamond status per ISSN from DOAJ (cached 30 days on disk).

    Each value: {"diamond": True|False|None, "journal": {...DOAJ journal...}|None}.
    diamond is None when the journal is not indexed in DOAJ (status unknown).
    """
    cache = _load_diamond_cache()
    wanted = {i.strip().upper() for i in issns if i and _ISSN_RE.match(i.strip().upper())}
    missing = sorted(i for i in wanted if i not in cache)
    for k in range(0, len(missing), 40):
        chunk = missing[k:k + 40]
        q = " OR ".join(f'issn:"{i}"' for i in chunk)
        data = await _get_json(f"{DOAJ}/search/journals/{quote(q, safe='')}", {"pageSize": 100}, client)
        for res in data.get("results") or []:
            j = _doaj_journal(res)
            for i in (j.get("pissn"), j.get("eissn")):
                if i:
                    cache[i.upper()] = {"diamond": j["diamond"], "journal": j, "checked": time.time()}
        for i in chunk:
            cache.setdefault(i, {"diamond": None, "journal": None, "checked": time.time()})
    if missing:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            DIAMOND_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return {i: cache[i] for i in wanted}


def _article_diamond(issns: list[str] | None, status: dict[str, dict]) -> tuple[bool | None, dict | None]:
    """Combine per-ISSN status: True if any ISSN is Diamond, False if DOAJ says it charges."""
    found = [status.get(i.upper()) for i in issns or [] if status.get(i.upper())]
    for s in found:
        if s["diamond"]:
            return True, s["journal"]
    for s in found:
        if s["diamond"] is False:
            return False, s["journal"]
    return None, None


async def _doaj_ranked(
    query: str,
    issn: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = MAX_RESULTS,
    diamond_only: bool = True,
    sort: Literal["recent", "relevance"] = "recent",
) -> dict:
    """DOAJ article search with Diamond filter; returns up to `limit` picks from the top RELEVANCE_POOL."""
    q = f"({query})"
    if issn:
        q += f' AND bibjson.journal.issns:"{issn}"'
    if year_from or year_to:
        # DOAJ rejects open-ended ranges ("*"), so use explicit bounds.
        q += f" AND bibjson.year:[{year_from or 1600} TO {year_to or date.today().year + 1}]"
    articles, total, excluded = [], 0, {"not_diamond": 0, "not_in_doaj": 0}
    async with _client() as client:
        try:
            # Diamond filtering drops results, so read up to 3 relevance-ranked pages.
            for page in range(1, 4):
                data = await _get_json(f"{DOAJ}/search/articles/{quote(q, safe='')}",
                                       {"page": page, "pageSize": 100}, client)
                total = data.get("total") or 0
                batch = [_doaj_article(r) for r in data.get("results") or []]
                status = await _diamond_lookup(client, [i for a in batch for i in a.get("issns") or []])
                for a in batch:
                    diamond, journal = _article_diamond(a.get("issns"), status)
                    a["diamond"] = diamond
                    if diamond_only and not diamond:
                        excluded["not_diamond" if diamond is False else "not_in_doaj"] += 1
                        continue
                    articles.append(a)
                if len(articles) >= RELEVANCE_POOL or page * 100 >= total:
                    break
        except Exception as exc:  # noqa: BLE001
            return _error("doaj", exc)
    articles = _pick(articles[:RELEVANCE_POOL], max(1, limit), sort)
    return {"total_hits": total, "returned": len(articles), "excluded": excluded, "articles": articles}


@mcp.tool()
async def doaj_search_articles(
    query: str,
    issn: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = MAX_RESULTS,
    diamond_only: bool = True,
    sort: Literal["recent", "relevance"] = "recent",
) -> dict:
    """Search articles in DOAJ (Directory of Open Access Journals), worldwide. Returns
    metadata and abstracts only and saves nothing. By default: only Diamond OA journals
    (no APC, no other author charges), at most 20, the most recent among the most
    relevant. To also save the results as an OKF bundle, use search_papers.

    Args:
        query: Search terms. Use OR between synonyms/translations. Elasticsearch
            query-string syntax is accepted (e.g. 'bibjson.title:"microplastics"').
        issn: Restrict to one journal ISSN.
        year_from: First publication year.
        year_to: Last publication year.
        limit: Maximum articles (1-20).
        diamond_only: Keep only articles whose journal is Diamond OA in DOAJ.
        sort: "recent" (newest first among the top relevance-ranked matches) or "relevance".
    """
    return await _doaj_ranked(query, issn, year_from, year_to, max(1, min(limit, MAX_RESULTS)),
                              diamond_only, sort)


@mcp.tool()
async def doaj_search_journals(
    query: str,
    diamond_only: bool = True,
    country: str | None = None,
    limit: int = 10,
    page: int = 1,
) -> dict:
    """Search journals in DOAJ, by default only Diamond OA journals (no APC and no other
    author charges).

    Args:
        query: Subject or title terms, e.g. "ecology", "engenharia civil". Elasticsearch
            query-string syntax accepted, e.g. 'bibjson.subject.term:biology'.
        diamond_only: Only journals with has_apc=false and no other charges.
        country: ISO 2-letter publisher country, e.g. "BR", "AR", "CO", "MX".
        limit: Results per page (1-50).
        page: Page number, starting at 1.
    """
    q = f"({query})"
    if diamond_only:
        q += " AND bibjson.apc.has_apc:false AND bibjson.other_charges.has_other_charges:false"
    if country:
        q += f" AND bibjson.publisher.country:{country.upper()}"
    try:
        data = await _get_json(
            f"{DOAJ}/search/journals/{quote(q, safe='')}",
            {"page": max(1, page), "pageSize": max(1, min(limit, MAX_RESULTS))},
        )
    except Exception as exc:  # noqa: BLE001
        return _error("doaj", exc)
    return {"total": data.get("total"), "page": page, "journals": [_doaj_journal(r) for r in data.get("results") or []]}


@mcp.tool()
async def doaj_check_journal(issn: str) -> dict:
    """Check in DOAJ whether a journal (by ISSN) is Diamond open access: no APC and no
    other author charges. Returns indexed=false if the journal is not in DOAJ."""
    try:
        data = await _get_json(f"{DOAJ}/search/journals/{quote(f'issn:{issn}', safe='')}")
    except Exception as exc:  # noqa: BLE001
        return _error("doaj", exc)
    results = data.get("results") or []
    if not results:
        return {"issn": issn, "indexed": False,
                "note": "Not in DOAJ. Check the journal's own site for fees."}
    return {"issn": issn, "indexed": True, **_doaj_journal(results[0])}


# ------------------------------------------------------------------- SciELO keyword search

SUBJECT_AREAS = [
    "Agricultural Sciences", "Applied Social Sciences", "Biological Sciences", "Engineering",
    "Exact and Earth Sciences", "Health Sciences", "Human Sciences", "Linguistics, Letters and Arts",
]
_ISSN_RE = re.compile(r"^\d{4}-\d{3}[\dX]$")
_BOOL_RE = re.compile(r"\b(?:AND|OR|NOT)\b|[()\"]")
_catalog_lock = asyncio.Lock()
_catalog_mem: dict | None = None


async def _build_catalog(client: httpx.AsyncClient) -> dict:
    cols = await _get_json(f"{ARTICLEMETA}/collection/identifiers/", client=client)
    cols = [c for c in cols if c.get("type") == "journals"]
    sem = asyncio.Semaphore(6)

    async def fetch(code: str) -> tuple[str, list | None]:
        async with sem:
            try:
                return code, await _get_json(f"{ARTICLEMETA}/journal/", {"collection": code}, client)
            except Exception:  # noqa: BLE001
                return code, None

    journals, failed = [], []
    for code, items in await asyncio.gather(*(fetch(c["code"]) for c in cols)):
        if items is None:
            failed.append(code)
            continue
        for j in items:
            info = _scielo_journal(j)
            raw = (info.get("issns") or []) + [j.get("code") or ""]
            cleaned = (i.strip().upper() for i in raw if isinstance(i, str))
            issns = sorted({i for i in cleaned if _ISSN_RE.match(i)})
            if issns:
                journals.append({**info, "issns": issns, "collection": code})
    return {
        "built_at": time.time(),
        "collections": {c["code"]: (c.get("name") or {}).get("pt") or c.get("original_name") for c in cols},
        "failed_collections": failed,
        "journals": journals,
    }


async def _get_catalog(client: httpx.AsyncClient, refresh: bool = False) -> dict:
    """Catalog of every SciELO journal, cached in memory and on disk for 30 days."""
    global _catalog_mem
    async with _catalog_lock:
        fresh = lambda d: d and time.time() - d.get("built_at", 0) < CATALOG_MAX_AGE  # noqa: E731
        if not refresh and fresh(_catalog_mem):
            return _catalog_mem
        if not refresh and CATALOG_FILE.exists():
            try:
                data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
                if fresh(data):
                    _catalog_mem = data
                    return data
            except (OSError, ValueError):
                pass
        data = await _build_catalog(client)
        if data["journals"] and not data["failed_collections"]:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            CATALOG_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _catalog_mem = data
        return data


def _select_journals(catalog: dict, collections: list[str] | None, subject_area: str | None) -> list[dict]:
    wanted = {c.strip().lower() for c in collections} if collections else None
    out = []
    for j in catalog["journals"]:
        if wanted and j["collection"] not in wanted:
            continue
        if subject_area and not any(_norm(subject_area) in _norm(s) for s in j.get("subject_areas") or []):
            continue
        out.append(j)
    return out


def _crossref_item(it: dict) -> dict:
    abstract = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", it.get("abstract") or "")).strip()
    parts = ((it.get("issued") or {}).get("date-parts") or [[None]])[0] or [None]
    authors = [
        ", ".join(x for x in (a.get("family"), a.get("given")) if x) or a.get("name", "")
        for a in it.get("author") or []
    ]
    doi = it.get("DOI")
    return {
        "title": (it.get("title") or [None])[0],
        "authors": authors,
        "journal": (it.get("container-title") or [None])[0],
        "issns": it.get("ISSN") or [],
        "year": parts[0],
        "doi": doi,
        "keywords": [],
        "abstract": _clip(abstract, 1500) or None,
        "fulltext_links": [f"https://doi.org/{doi}"] if doi else [],
    }


async def _scielo_doaj_chunk(client, sem, query, issns, year_from, year_to, rows):
    q = f"({query}) AND bibjson.journal.issns:(" + " OR ".join(f'"{i}"' for i in issns) + ")"
    if year_from or year_to:
        q += f" AND bibjson.year:[{year_from or 1600} TO {year_to or date.today().year + 1}]"
    async with sem:
        data = await _get_json(f"{DOAJ}/search/articles/{quote(q, safe='')}", {"pageSize": rows}, client)
    return data.get("total", 0), [_doaj_article(r) for r in data.get("results") or []]


async def _scielo_crossref_chunk(client, sem, query, issns, year_from, year_to, rows):
    filters = [f"issn:{i}" for i in issns] + ["type:journal-article"]
    if year_from:
        filters.append(f"from-pub-date:{year_from}")
    if year_to:
        filters.append(f"until-pub-date:{year_to}")
    params = {
        "query.bibliographic": " ".join(_BOOL_RE.sub(" ", query).split()),
        "filter": ",".join(filters),
        "rows": rows,
        "select": "DOI,title,container-title,ISSN,issued,author,abstract,score",
    }
    if CROSSREF_MAILTO:
        params["mailto"] = CROSSREF_MAILTO
    async with sem:
        data = await _get_json(f"{CROSSREF}/works", params, client)
    msg = data.get("message") or {}
    return msg.get("total-results", 0), [_crossref_item(i) for i in msg.get("items") or []]


async def _scielo_ranked(
    query: str,
    collections: list[str] | None = None,
    subject_area: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = MAX_RESULTS,
    sort: Literal["recent", "relevance"] = "recent",
    sources: list[Literal["doaj", "crossref"]] | None = None,
    diamond_only: bool = True,
) -> dict:
    """SciELO-restricted search (DOAJ + Crossref) with Diamond filter; see scielo_search."""
    sources = sources or ["doaj", "crossref"]
    use_or = bool(re.search(r"\bOR\b", query))
    terms = [t for t in (_norm(w) for w in _BOOL_RE.sub(" ", query).split()) if len(t) >= 3 and ":" not in t]

    async with _client() as client:
        try:
            catalog = await _get_catalog(client)
        except Exception as exc:  # noqa: BLE001
            return _error("scielo articlemeta", exc)
        journals = _select_journals(catalog, collections, subject_area)
        if not journals:
            return {"error": "no SciELO journals match these collections/subject_area",
                    "valid_subject_areas": SUBJECT_AREAS}
        by_issn = {i: j for j in journals for i in j["issns"]}
        issns = sorted(by_issn)

        jobs: list[tuple[str, Any]] = []  # (label, coroutine factory)
        doaj_sem, cr_sem = asyncio.Semaphore(3), asyncio.Semaphore(2)
        if "doaj" in sources:
            for k in range(0, len(issns), 100):
                chunk = issns[k:k + 100]
                jobs.append(("doaj", lambda c=chunk: _scielo_doaj_chunk(
                    client, doaj_sem, query, c, year_from, year_to, 100)))
        if "crossref" in sources:
            for k in range(0, len(issns), 150):
                chunk = issns[k:k + 150]
                jobs.append(("crossref", lambda c=chunk: _scielo_crossref_chunk(
                    client, cr_sem, query, c, year_from, year_to, 200)))
        labels = [label for label, _ in jobs]
        results = await asyncio.gather(*(make() for _, make in jobs), return_exceptions=True)
        # Rate limits hit under parallel load: retry failed chunks one at a time.
        for idx, res in enumerate(results):
            if isinstance(res, Exception):
                await asyncio.sleep(3)
                try:
                    results[idx] = await jobs[idx][1]()
                except Exception as exc:  # noqa: BLE001
                    results[idx] = exc

    totals = {"doaj": 0, "crossref": 0}
    truncated, errors = 0, []
    merged: dict[str, dict] = {}
    by_title: dict[str, str] = {}  # normalized title -> merged key (catches DOI-less duplicates)
    for label, res in zip(labels, results):
        if isinstance(res, Exception):
            errors.append(_error(label, res)["error"])
            continue
        total, items = res
        totals[label] += total
        truncated += total > len(items) and len(items) >= 100
        for it in items:
            if not it.get("title"):
                continue
            it["title"] = re.sub(r"<[^>]+>", "", it["title"]).strip()
            tkey = re.sub(r"[^a-z0-9]", "", _norm(it["title"]))[:100]
            key = (it.get("doi") or "").lower() or tkey
            if key not in merged:
                # Same paper without/with another DOI, or with a bilingual title
                # ("Título ... Title ...") that starts with the other one.
                same = tkey if tkey in by_title else next(
                    (t for t in by_title if min(len(t), len(tkey)) >= 40
                     and (t.startswith(tkey) or tkey.startswith(t))), None)
                if same:
                    key = by_title[same]
            by_title.setdefault(tkey, key)
            cur = merged.get(key)
            if cur is None:
                journal = next((by_issn[i] for i in it.get("issns") or [] if i.upper() in by_issn), None)
                merged[key] = cur = {
                    **{k: it.get(k) for k in ("title", "authors", "journal", "year", "doi", "abstract", "keywords")},
                    "issns": [],
                    "links": list(it.get("fulltext_links") or []),
                    "scielo_collection": journal["collection"] if journal else None,
                    "found_in": [],
                }
            else:
                for k in ("abstract", "keywords", "year", "journal", "doi"):
                    if not cur.get(k) and it.get(k):
                        cur[k] = it[k]
                if len(it["title"]) < len(cur["title"]):
                    cur["title"] = it["title"]  # prefer the single-language title
                cur["links"] = list(dict.fromkeys(cur["links"] + list(it.get("fulltext_links") or [])))
            cur["issns"] = sorted(set(cur["issns"]) | {i.upper() for i in it.get("issns") or []})
            if label not in cur["found_in"]:
                cur["found_in"].append(label)

    articles, dropped = [], 0
    for art in merged.values():
        title = _norm(art["title"])
        body = _norm(" ".join([art.get("abstract") or "", " ".join(art.get("keywords") or [])]))
        hits = [t for t in terms if t in title or t in body]
        if terms and not (hits if use_or else len(hits) == len(terms)):
            dropped += 1
            continue
        art["score"] = sum(3 for t in terms if t in title) + sum(1 for t in terms if t in body) + len(art["found_in"]) - 1
        articles.append(art)

    excluded = {"not_diamond": 0, "not_in_doaj": 0}
    try:
        async with _client() as client:
            status = await _diamond_lookup(client, [i for a in articles for i in a["issns"]])
    except Exception as exc:  # noqa: BLE001
        return _error("doaj (diamond check)", exc)
    kept = []
    for art in articles:
        art["diamond"], _ = _article_diamond(art["issns"], status)
        if diamond_only and not art["diamond"]:
            excluded["not_diamond" if art["diamond"] is False else "not_in_doaj"] += 1
            continue
        kept.append(art)
    articles = kept

    articles.sort(key=lambda a: (a["score"], _year(a)), reverse=True)
    picked = _pick(articles[:RELEVANCE_POOL], max(1, limit), sort)
    return {
        "query": query,
        "journals_searched": len(journals),
        "collections_searched": sorted({j["collection"] for j in journals}),
        "matches": len(articles),
        "returned": len(picked),
        "raw_hits": totals,
        "discarded_without_terms": dropped,
        "excluded": excluded,
        "truncated_chunks": truncated,
        "errors": errors,
        "articles": picked,
    }


@mcp.tool()
async def scielo_search(
    query: str,
    collections: list[str] | None = None,
    subject_area: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = MAX_RESULTS,
    sort: Literal["recent", "relevance"] = "recent",
    sources: list[Literal["doaj", "crossref"]] | None = None,
    diamond_only: bool = True,
) -> dict:
    """Keyword search restricted to SciELO journals, across every SciELO collection
    (Brazil, Argentina, Chile, Colombia, Mexico, Portugal, Spain, South Africa, ...).
    Returns metadata and abstracts only and saves nothing. By default: only Diamond OA
    journals (checked in DOAJ: no APC, no other author charges), at most 20, the most
    recent among the most relevant. To also save the results as an OKF bundle, use
    search_papers.

    SciELO has no public keyword-search API, so this searches DOAJ (titles, abstracts,
    keywords) and Crossref (titles, authors) restricted to the ISSNs of all SciELO
    journals (catalog from ArticleMeta, cached 30 days). Takes ~20-60 s.

    Args:
        query: Search terms. Use OR between synonyms/translations, e.g.
            "hidroides OR hydroids OR Hydrozoa". Without OR, all terms must appear.
        collections: SciELO collection codes (default: all), e.g. ["scl"] for Brazil.
        subject_area: One SciELO area, e.g. "Biological Sciences", "Health Sciences".
        year_from: First publication year.
        year_to: Last publication year.
        limit: Maximum articles returned (1-20).
        sort: "recent" (newest first among the top relevance-ranked matches) or "relevance".
        sources: Which indexes to query (default both).
        diamond_only: Keep only articles from journals DOAJ lists as Diamond OA.
    """
    return await _scielo_ranked(query, collections, subject_area, year_from, year_to,
                                max(1, min(limit, MAX_RESULTS)), sort, sources, diamond_only)


@mcp.tool()
async def scielo_find_journals(
    query: str = "",
    collection: str | None = None,
    subject_area: str | None = None,
    limit: int = 50,
    refresh_catalog: bool = False,
) -> dict:
    """Find journals in the SciELO catalog (all collections) by title or ISSN, collection
    and subject area. Useful to pick ISSNs for doaj_search_articles or scielo_list_articles.

    Args:
        query: Words to match in the title, abbreviation or ISSN (empty = all).
        collection: SciELO collection code, e.g. "scl" (Brazil), "arg", "mex".
        subject_area: SciELO area, e.g. "Biological Sciences", "Health Sciences".
        limit: Maximum journals returned.
        refresh_catalog: Rebuild the cached catalog from ArticleMeta now.
    """
    async with _client() as client:
        try:
            catalog = await _get_catalog(client, refresh=refresh_catalog)
        except Exception as exc:  # noqa: BLE001
            return _error("scielo articlemeta", exc)
    words = _norm(query).split()
    out = []
    for j in _select_journals(catalog, [collection] if collection else None, subject_area):
        hay = _norm(" ".join([j.get("title") or "", j.get("abbreviation") or "", " ".join(j["issns"])]))
        if all(w in hay for w in words):
            out.append(j)
    out.sort(key=lambda j: (j.get("status") != "C", j.get("title") or ""))
    return {
        "catalog_journals": len(catalog["journals"]),
        "catalog_built": time.strftime("%Y-%m-%d", time.localtime(catalog["built_at"])),
        "matches": len(out),
        "journals": out[: max(1, limit)],
    }


# ------------------------------------------------------------------- download + OKF conversion

_DOI_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/\S+)$", re.I)
CONVERT_WORKERS = max(2, min(6, (os.cpu_count() or 4) // 2))
_convert_sem = asyncio.Semaphore(CONVERT_WORKERS)
_PAGE_HEADERS = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8"}


async def _crossref_meta(client: httpx.AsyncClient, doi: str) -> dict:
    data = await _get_json(f"{CROSSREF}/works/{quote(doi, safe='/')}", client=client)
    it = data.get("message") or {}
    meta = _crossref_item(it)
    meta["title"] = re.sub(r"<[^>]+>", "", meta.get("title") or "").strip() or None
    meta["license"] = ((it.get("license") or [{}])[0]).get("URL")
    meta["language"] = it.get("language")
    meta["issns"] = [i.upper() for i in it.get("ISSN") or []]
    return meta


async def _download_pdf(client: httpx.AsyncClient, url: str, depth: int = 0) -> tuple[bytes, str] | None:
    """Download url if it is a PDF; if it is an HTML viewer page, follow its PDF link once."""
    async with client.stream("GET", url, headers=_PAGE_HEADERS) as r:
        if r.status_code != 200:
            return None
        chunks, size = [], 0
        async for chunk in r.aiter_bytes():
            size += len(chunk)
            if size > MAX_PDF_BYTES:
                return None
            chunks.append(chunk)
        final = str(r.url)
        ctype = r.headers.get("content-type", "").lower()
    data = b"".join(chunks)
    if data[:5] == b"%PDF-":
        return data, final
    if depth == 0 and "html" in ctype:
        page = data.decode("utf-8", "replace")
        pm = okf.parse_meta(page)
        cands = okf.pdf_candidates(page, pm, final)
        cands += [html_unescape(u) for u in re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', page, re.I)]
        for cand in cands[:3]:
            got = await _download_pdf(client, urljoin(final, cand), depth=1)
            if got:
                return got
    return None


async def _run_converter(pdf_path: Path, mode: str, max_pages: int, timeout: float) -> str | None:
    """Run convert.py in a subprocess (PyMuPDF writes to stdout, which would corrupt MCP stdio)."""
    tmp_dir = CACHE_DIR / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out = tmp_dir / f"{pdf_path.stem}.{mode}.md"
    async with _convert_sem:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "latam_oa_mcp.convert", str(pdf_path), str(out), str(max_pages), mode,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    md = out.read_text(encoding="utf-8") if proc.returncode == 0 and out.exists() else None
    out.unlink(missing_ok=True)
    return md


def _has_text(md: str | None) -> bool:
    """True when the markdown has real text (scanned PDFs yield almost no letters)."""
    if not md:
        return False
    body = re.sub(r"<!--.*?-->|\*|#|\|", " ", md)
    return len(re.findall(r"[^\W\d_]", body)) >= 500


async def _convert_pdf(pdf_path: Path, ocr: bool) -> tuple[str | None, str | None]:
    """Convert a PDF to markdown. Returns (markdown, method).

    1. layout: structured markdown without OCR (fast, born-digital PDFs).
    2. plain:  raw text, when layout is too slow (e.g. huge image-heavy PDFs).
    3. ocr:    scanned PDFs with no text layer, first OCR_MAX_PAGES pages only.
    """
    md = await _run_converter(pdf_path, "layout", 200, 240)
    if _has_text(md):
        return md, "pdf-layout"
    if md is None:  # timed out or crashed: try the fast plain-text path
        md = await _run_converter(pdf_path, "plain", 400, 120)
        if _has_text(md):
            return md, "pdf-plain-text"
    if ocr:
        md = await _run_converter(pdf_path, "ocr", OCR_MAX_PAGES, OCR_TIMEOUT)
        if _has_text(md):
            return md, "pdf-ocr"
    return None, None


async def _try_landing(client: httpx.AsyncClient, url: str, meta: dict) -> tuple[str | None, tuple | None]:
    """Fetch a landing page; fill missing metadata from its meta tags; find and download the PDF."""
    r = await client.get(url, headers=_PAGE_HEADERS)
    final = str(r.url)
    if r.content[:5] == b"%PDF-":
        return final, (r.content, final)
    if r.status_code != 200:
        return None, None
    pm = okf.parse_meta(r.text)
    for k, v in okf.meta_metadata(pm).items():
        if v and not meta.get(k):
            meta[k] = v
    for cand in okf.pdf_candidates(r.text, pm, final):
        pdf = await _download_pdf(client, cand)
        if pdf:
            return final, pdf
    return final, None


async def _doaj_links_for_doi(client: httpx.AsyncClient, doi: str) -> list[str]:
    q = 'doi:"' + doi + '"'
    data = await _get_json(f"{DOAJ}/search/articles/{quote(q, safe='')}", {"pageSize": 3}, client)
    return [u for r in data.get("results") or [] for u in _doaj_article(r)["fulltext_links"]]


async def _process_paper(client, sem, ref: str, root: Path, diamond_only: bool, overwrite: bool,
                         ocr: bool) -> dict:
    async with sem:
        m = _DOI_RE.match(ref.strip())
        doi = m.group(1) if m else None
        meta: dict = {}
        if doi:
            try:
                meta = await _crossref_meta(client, doi)
            except Exception:  # noqa: BLE001 - fall back to the landing page metadata
                meta = {}
        landings = [f"https://doi.org/{doi}" if doi else ref]
        landing_final, pdf = None, None
        for i, url in enumerate(landings):
            try:
                final, pdf = await _try_landing(client, url, meta)
                landing_final = landing_final or final
            except Exception:  # noqa: BLE001 - unreachable; try the next candidate
                pass
            if pdf:
                break
            if i == 0 and doi:
                # Dead or PDF-less DOI landing page: try the full-text links DOAJ has.
                try:
                    landings += [u for u in await _doaj_links_for_doi(client, doi) if u not in landings][:2]
                except Exception:  # noqa: BLE001
                    pass
        meta["doi"] = doi or meta.get("doi")
        meta["title"] = re.sub(r"<[^>]+>", "", html_unescape(meta.get("title") or "")).strip() or None
        meta["issns"] = sorted({i.strip().upper() for i in meta.get("issns") or [] if i})
        if not meta.get("title"):
            return {"ref": ref, "status": "failed", "reason": "no metadata found (DOI/URL unreachable)"}

        # The concept search_papers wrote knows the journal's ISSNs even when the DOI's
        # registration (e.g. Zenodo) does not; reuse them, and upgrade that file in place.
        existing = okf.find_paper(root, meta["doi"], ref)
        old_fm, old_abstract = okf.read_paper(existing) if existing else ({}, None)
        meta["issns"] = sorted(set(meta["issns"]) | {str(i).upper() for i in old_fm.get("issn") or []})

        status = await _diamond_lookup(client, meta["issns"])
        diamond, journal = _article_diamond(meta["issns"], status)
        if diamond_only and not diamond:
            reason = ("journal charges APC or other fees (DOAJ)" if diamond is False
                      else "journal not in DOAJ: Diamond status unknown")
            return {"ref": ref, "title": meta["title"], "status": "skipped", "reason": reason}

        if existing:
            if old_fm.get("fulltext") in okf.FULLTEXT_METHODS and not overwrite:
                return {"ref": ref, "title": meta["title"], "status": "already_in_bundle",
                        "fulltext": old_fm["fulltext"], "path": str(existing)}
            meta["abstract"] = meta.get("abstract") or old_abstract
            meta["keywords"] = meta.get("keywords") or old_fm.get("tags") or []
        slug = existing.stem if existing else okf.slugify(meta["title"], meta["doi"] or ref)
        paper_path = root / "papers" / f"{slug}.md"

        md, method, pdf_rel, pdf_url = None, None, None, None
        if pdf:
            pdf_path = root / "references" / "papers" / f"{slug}.pdf"
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(pdf[0])
            pdf_rel, pdf_url = f"/references/papers/{slug}.pdf", pdf[1]
            md, method = await _convert_pdf(pdf_path, ocr)
        journal_link = okf.write_journal(root, journal) if journal else None
        fulltext = method or ("pdf-not-converted" if pdf else "abstract-only")
        okf.write_paper(root, slug, meta, journal_link, md, pdf_rel, pdf_url, landing_final, diamond, fulltext)
        return {
            "ref": ref, "title": meta["title"], "status": "upgraded" if existing else "saved",
            "fulltext": fulltext, "path": str(paper_path),
            "pdf": str(root / pdf_rel.lstrip("/")) if pdf_rel else None,
        }


def _bundle_dir(bundle: str) -> Path:
    name = re.sub(r"[^a-z0-9]+", "-", _norm(bundle)).strip("-")[:60] or "papers"
    root = LIBRARY / name
    root.mkdir(parents=True, exist_ok=True)
    return root


async def _enrich_from_landing(client: httpx.AsyncClient, sem: asyncio.Semaphore, art: dict) -> None:
    """Fill a missing abstract/keywords from the publisher page meta tags (no PDF download)."""
    url = f"https://doi.org/{art['doi']}" if art.get("doi") else (art.get("links") or [None])[0]
    if not url:
        return
    async with sem:
        try:
            r = await client.get(url, headers=_PAGE_HEADERS, timeout=20)
        except Exception:  # noqa: BLE001 - enrichment is best effort
            return
    if r.status_code != 200 or r.content[:5] == b"%PDF-":
        return
    found = okf.meta_metadata(okf.parse_meta(r.text))
    for k in ("abstract", "keywords", "authors", "year"):
        if not art.get(k) and found.get(k):
            art[k] = found[k]
    art["landing"] = str(r.url)


def _merge_results(pools: list[tuple[str, list[dict]]]) -> list[dict]:
    """Merge relevance-ranked lists from several sources; rank = best position in any list."""
    merged: dict[str, dict] = {}
    for source, items in pools:
        for pos, a in enumerate(items):
            title = re.sub(r"<[^>]+>", "", html_unescape(a.get("title") or "")).strip()
            if not title:
                continue
            key = (a.get("doi") or "").lower() or re.sub(r"[^a-z0-9]", "", _norm(title))[:100]
            links = a.get("links") or a.get("fulltext_links") or []
            cur = merged.get(key)
            if cur is None:
                merged[key] = {
                    "title": title, "authors": a.get("authors") or [], "journal": a.get("journal"),
                    "issns": [i.upper() for i in a.get("issns") or []], "year": a.get("year"),
                    "doi": a.get("doi"), "abstract": a.get("abstract"), "keywords": a.get("keywords") or [],
                    "links": list(links), "diamond": a.get("diamond"),
                    "scielo_collection": a.get("scielo_collection"), "found_in": [source], "rank": pos,
                }
                continue
            cur["rank"] = min(cur["rank"], pos)
            cur["found_in"].append(source)
            cur["links"] = list(dict.fromkeys(cur["links"] + list(links)))
            for k in ("abstract", "keywords", "year", "journal", "scielo_collection"):
                if not cur.get(k) and a.get(k):
                    cur[k] = a[k]
    return sorted(merged.values(), key=lambda a: a["rank"])


@mcp.tool()
async def search_papers(
    query: str,
    sources: list[Literal["scielo", "doaj"]] | None = None,
    collections: list[str] | None = None,
    subject_area: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = MAX_RESULTS,
    sort: Literal["recent", "relevance"] = "recent",
    diamond_only: bool = True,
    bundle: str | None = None,
    save_okf: bool = True,
) -> dict:
    """PATH 1 - main search. Finds up to 20 Diamond open access papers (SciELO journals +
    DOAJ worldwide), the most recent among the most relevant, and saves their METADATA
    AND ABSTRACT as an Open Knowledge Format (OKF v0.2) bundle: one markdown concept per
    paper and per journal, plus index.md and log.md. No PDFs are downloaded here, so it is
    fast (~20-60 s, dominated by the SciELO search).

    To read whole papers, pass the `ref` of the selected results to fetch_full_papers
    (PATH 2), which upgrades those concepts in place with the full text.

    Args:
        query: Search terms. Use OR between synonyms/translations, e.g.
            "hidroides OR hydroids OR Hydrozoa". Without OR, all terms must appear.
        sources: "scielo" (SciELO journals, all collections) and/or "doaj" (all DOAJ
            journals). Default both.
        collections: SciELO collection codes (default all), e.g. ["scl"] for Brazil.
        subject_area: SciELO area filter, e.g. "Biological Sciences".
        year_from: First publication year.
        year_to: Last publication year.
        limit: Number of papers (1-20).
        sort: "recent" (newest first among the top relevance-ranked matches) or "relevance".
        diamond_only: Only journals DOAJ lists as Diamond OA (no APC, no other charges).
        bundle: OKF bundle (folder) name; default derived from the query. Reusing a name
            adds to the same bundle.
        save_okf: Save the results as OKF concepts (default true).
    """
    sources = sources or ["scielo", "doaj"]
    limit = max(1, min(limit, MAX_RESULTS))
    jobs = {}
    if "scielo" in sources:
        jobs["scielo"] = _scielo_ranked(query, collections, subject_area, year_from, year_to,
                                        RELEVANCE_POOL, "relevance", None, diamond_only)
    if "doaj" in sources:
        jobs["doaj"] = _doaj_ranked(query, None, year_from, year_to, RELEVANCE_POOL, diamond_only, "relevance")
    results = await asyncio.gather(*jobs.values(), return_exceptions=True)

    pools, stats, errors = [], {}, []
    for name, res in zip(jobs, results):
        if isinstance(res, Exception) or "error" in res:
            errors.append(f"{name}: {res if isinstance(res, Exception) else res['error']}")
            continue
        errors += [f"{name}: {e}" for e in res.get("errors") or []]
        stats[name] = {"matches": res.get("matches", res.get("total_hits")), "excluded": res["excluded"]}
        pools.append((name, res["articles"]))

    picked = _pick(_merge_results(pools)[:RELEVANCE_POOL], limit, sort)
    async with _client() as client:
        sem = asyncio.Semaphore(6)
        await asyncio.gather(*(_enrich_from_landing(client, sem, a) for a in picked if not a.get("abstract")))
        status = await _diamond_lookup(client, [i for a in picked for i in a["issns"]]) if save_okf else {}

    root = _bundle_dir(bundle or " ".join(_BOOL_RE.sub(" ", query).split()[:4])) if save_okf else None
    papers, new = [], 0
    for a in picked:
        ref = a.get("doi") or (a["links"] or [None])[0]
        item = {
            "ref": ref, "title": a["title"], "authors": a["authors"][:6], "journal": a["journal"],
            "year": a["year"], "doi": a.get("doi"), "diamond": a["diamond"], "found_in": a["found_in"],
            "abstract": _clip(re.sub(r"\s+", " ", a.get("abstract") or ""), 700) or None,
        }
        if root is not None:
            existing = okf.find_paper(root, a.get("doi"), ref)
            fm = okf.read_paper(existing)[0] if existing else {}
            if fm.get("fulltext") in okf.FULLTEXT_METHODS:
                item.update(okf_path=str(existing), fulltext=fm["fulltext"])  # never downgrade
            else:
                _, journal = _article_diamond(a["issns"], status)
                journal_link = okf.write_journal(root, journal) if journal else None
                slug = existing.stem if existing else okf.slugify(a["title"], a.get("doi") or ref or a["title"])
                meta = {k: a.get(k) for k in ("title", "authors", "journal", "issns", "year", "doi", "abstract", "keywords")}
                landing = a.get("landing") or (a["links"] or [None])[0]
                path = okf.write_paper(root, slug, meta, journal_link, None, None, None, landing,
                                       a["diamond"], "not-requested")
                item.update(okf_path=str(path), fulltext="not-requested")
                new += not existing
        papers.append(item)

    out = {"query": query, "returned": len(papers), "sources": stats, "errors": errors}
    if root is not None:
        okf.rebuild_indexes(root)
        if papers:
            okf.append_log(root, [f'**Creation**: Search "{query}" saved {len(papers)} papers '
                                  f"({new} new) with metadata and abstract."])
        out.update(bundle=root.name, bundle_path=str(root), index=str(root / "index.md"),
                   next_step=f"For full text, call fetch_full_papers(papers=[<ref>, ...], bundle='{root.name}').")
    out["papers"] = papers
    return out


@mcp.tool()
async def fetch_full_papers(
    papers: list[str],
    bundle: str,
    diamond_only: bool = True,
    overwrite: bool = False,
    ocr: bool = False,
) -> dict:
    """PATH 2 - full text on request. Download the PDFs of SELECTED papers and convert the
    whole paper into the OKF bundle. Use it after search_papers, for the papers the user
    actually wants to read: the paper's existing concept (metadata + abstract) is upgraded
    in place with a '# Full text' section; the original PDF is kept under
    references/papers/. Max 20 papers per call; ~5-20 s per paper.

    The PDF is located through the publisher page (citation_pdf_url / OJS download links,
    falling back to DOAJ full-text links). If no PDF is reachable, the paper keeps its
    metadata and abstract (fulltext: abstract-only).

    Args:
        papers: DOIs (e.g. "10.11606/1807-0205/2021.61.06") or article URLs from
            search_papers results (each result has a `ref` field to pass here).
        bundle: Bundle name, normally the one search_papers returned.
        diamond_only: Skip papers whose journal is not Diamond OA in DOAJ.
        overwrite: Re-download papers that already have full text.
        ocr: OCR scanned PDFs without a text layer (slow: ~10-15 s/page, first 40 pages).
            Off by default; born-digital PDFs never need it.
    """
    refs = list(dict.fromkeys(p.strip() for p in papers if p and p.strip()))[:MAX_FULLTEXT]
    root = _bundle_dir(bundle)
    root.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(CONVERT_WORKERS + 2)
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=20.0), follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as client:
        results = await asyncio.gather(
            *(_process_paper(client, sem, ref, root, diamond_only, overwrite, ocr) for ref in refs),
            return_exceptions=True,
        )
    out = []
    for ref, res in zip(refs, results):
        out.append(res if isinstance(res, dict) else
                   {"ref": ref, "status": "failed", "reason": f"{type(res).__name__}: {res}"})

    log = []
    for r in out:
        if r["status"] in ("saved", "upgraded"):
            rel = "/papers/" + Path(r["path"]).name
            what = {"pdf-layout": "full text converted from PDF", "pdf-plain-text": "full text extracted as plain text",
                    "pdf-ocr": "full text from OCR of a scanned PDF"}.get(r["fulltext"], "no PDF available; metadata and abstract only")
            verb = "**Update**: Full text for" if r["status"] == "upgraded" else "**Creation**: Added"
            log.append(f"{verb} [{r['title']}]({rel}) ({what}).")
    okf.rebuild_indexes(root)
    okf.append_log(root, log)

    count = lambda **kw: sum(all(r.get(k) == v for k, v in kw.items()) for r in out)  # noqa: E731
    return {
        "bundle": str(root),
        "index": str(root / "index.md"),
        "saved_new": count(status="saved"),
        "upgraded": count(status="upgraded"),
        "with_fulltext": sum(r.get("fulltext") in okf.FULLTEXT_METHODS and r["status"] != "already_in_bundle"
                             for r in out),
        "with_fulltext_by_ocr": sum(r.get("fulltext") == "pdf-ocr" for r in out),
        "pdf_not_converted": sum(r.get("fulltext") == "pdf-not-converted" for r in out),
        "no_pdf_found": sum(r.get("fulltext") == "abstract-only" for r in out),
        "already_in_bundle": count(status="already_in_bundle"),
        "skipped_not_diamond": count(status="skipped"),
        "failed": count(status="failed"),
        "papers": out,
    }


@mcp.tool()
def list_okf_bundles() -> dict:
    """List the OKF paper bundles in the library folder, with paper counts and paths to
    each bundle's index.md (start reading there)."""
    bundles = []
    if LIBRARY.exists():
        for d in sorted(p for p in LIBRARY.iterdir() if p.is_dir()):
            papers = [p for p in (d / "papers").glob("*.md") if p.name != "index.md"]
            bundles.append({"bundle": d.name, "index": str(d / "index.md"), "papers": len(papers)})
    return {"library": str(LIBRARY), "bundles": bundles}


# ------------------------------------------------------------------- local templates

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


@mcp.tool()
def find_journal_templates(query: str = "", area: str = "") -> dict:
    """Search the local collection of Diamond OA journal manuscript templates
    (_INDICE_GERAL.csv in the templates folder). Returns journal, ISSN, area and the
    absolute paths of the template files.

    Args:
        query: Words to match in journal name, publisher, subarea or ISSN (empty = all).
        area: Filter by area folder, e.g. "Engenharias", "Ciencias_Biologicas",
            "Ciencias_da_Saude", "Ciencias_Humanas" (partial match, accents ignored).
    """
    index = TEMPLATES_DIR / "_INDICE_GERAL.csv"
    if not index.exists():
        return {"error": f"index not found: {index}. Set LATAM_OA_TEMPLATES_DIR."}
    with open(index, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    terms = _norm(query).split()
    out = []
    for r in rows:
        if area and _norm(area) not in _norm(r["area"]):
            continue
        hay = _norm(" ".join(r.get(k, "") for k in ("journal", "publisher", "subarea", "issn", "pasta", "country")))
        if all(t in hay for t in terms):
            folder = TEMPLATES_DIR / r["area"] / r["pasta"]
            out.append({
                "journal": r["journal"], "issn": r["issn"], "area": r["area"],
                "subarea": r["subarea"], "country": r["country"], "publisher": r["publisher"],
                "diamond_evidence_url": r["diamond_evidence_url"],
                **{k: r.get(k, "") for k in ("qualis_estrato", "qualis_quadrienio", "qualis_area_mae",
                                              "citacoes_2anos_openalex", "indice_h_openalex", "obs_metricas",
                                              "escopo", "temas_principais") if r.get(k)},
                "files": [str(folder / name) for name in r["arquivos"].split("; ") if name],
            })
    return {"templates_dir": str(TEMPLATES_DIR), "count": len(out), "journals": out}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
