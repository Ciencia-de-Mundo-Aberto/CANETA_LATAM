"""End-to-end test: launches the server over stdio as an MCP client would and calls
every tool against the live APIs. Run with:  uv run python tests/test_server.py
"""

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import yaml

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = Path(tempfile.mkdtemp(prefix="okf_test_"))


def okf_conformance(bundle: Path) -> list[str]:
    """Problems found against OKF v0.2 section 11 (empty list = conformant)."""
    problems = []
    for path in bundle.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        rel = path.relative_to(bundle).as_posix()
        if path.name == "index.md":
            if m and (rel != "index.md" or set(yaml.safe_load(m.group(1))) != {"okf_version"}):
                problems.append(f"{rel}: index.md may only carry okf_version, at the bundle root")
        elif path.name == "log.md":
            heads = re.findall(r"^## (.+)$", text, re.M)
            if not heads or not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", h) for h in heads):
                problems.append(f"{rel}: log headings must be YYYY-MM-DD")
        elif not m or not (yaml.safe_load(m.group(1)) or {}).get("type"):
            problems.append(f"{rel}: missing frontmatter or type")
    return problems


def years_desc(items: list[dict]) -> bool:
    ys = [int(str(a["year"])[:4]) for a in items if str(a.get("year") or "")[:4].isdigit()]
    return ys == sorted(ys, reverse=True)


def check_search_papers(r: dict) -> bool:
    problems = okf_conformance(Path(r["bundle_path"]))
    if problems:
        print("       OKF problems:", problems)
    return (0 < r["returned"] <= 20 and not problems and years_desc(r["papers"])
            and all(p["diamond"] is True and Path(p["okf_path"]).exists() for p in r["papers"])
            and all(p["fulltext"] == "not-requested" for p in r["papers"]))


def check_fetch(r: dict) -> bool:
    problems = okf_conformance(Path(r["bundle"]))
    if problems:
        print("       OKF problems:", problems)
    papers = list((Path(r["bundle"]) / "papers").glob("*.md"))
    return (r["upgraded"] >= 1 and r["with_fulltext"] >= 1 and r["skipped_not_diamond"] >= 1
            and not problems and len(papers) == 21)  # 20 search results + index.md, no duplicates

CASES = [
    ("search_lareferencia", {"query": "microplasticos", "format": "article", "year_from": 2022, "limit": 3},
     lambda r: r["total"] > 0 and r["records"][0]["title"]),
    ("search_lareferencia", {"query": "microplasticos", "country": "Argentina", "limit": 2},
     lambda r: r["total"] > 0),
    ("search_oasisbr", {"query": "biologia marinha", "format": "doctoralThesis", "limit": 3},
     lambda r: r["total"] > 0 and all("doctoralThesis" in x["type"] for x in r["records"])),
    ("scielo_list_collections", {}, lambda r: any(c["code"] == "scl" for c in r["collections"])),
    ("scielo_get_journal", {"issn": "1519-6984"}, lambda r: r["title"] == "Brazilian Journal of Biology"),
    ("scielo_list_articles", {"issn": "1519-6984", "from_date": "2025-01-01", "limit": 2},
     lambda r: r["total"] > 0 and len(r["articles"]) == 2 and r["articles"][0]["titles"]),
    ("doaj_search_articles", {"query": "microplastics", "year_from": 2023, "limit": 3},
     lambda r: r["total_hits"] > 0 and r["articles"][0]["fulltext_links"]
     and all(a["diamond"] is True for a in r["articles"])),
    ("doaj_search_articles", {"query": "hydroids OR Hydrozoa", "limit": 500},
     lambda r: 0 < r["returned"] <= 20 and all(a["diamond"] is True for a in r["articles"])
     and r["excluded"]["not_diamond"] > 0 and years_desc(r["articles"])),
    ("doaj_search_journals", {"query": "biology", "country": "BR", "limit": 5},
     lambda r: r["total"] > 0 and all(j["diamond"] for j in r["journals"])),
    ("doaj_check_journal", {"issn": "2215-2075"}, lambda r: r["indexed"] and r["diamond"] is True),
    ("doaj_check_journal", {"issn": "1519-6984"}, lambda r: r["indexed"] and r["diamond"] is False),
    ("doaj_check_journal", {"issn": "0000-0000"}, lambda r: r["indexed"] is False),
    ("scielo_find_journals", {"query": "zoologia", "collection": "scl"},
     lambda r: r["catalog_journals"] > 1000 and any("1984-4689" in j["issns"] for j in r["journals"])),
    ("scielo_search", {"query": "hidroides OR hydroids OR Hydrozoa", "diamond_only": False, "limit": 100},
     lambda r: r["matches"] >= 30 and r["returned"] == 20 and not r["errors"]
     and len(r["collections_searched"]) > 5
     and len({(a["doi"] or a["title"]).lower() for a in r["articles"]}) == len(r["articles"])),
    ("scielo_search", {"query": "hidroides OR hydroids OR Hydrozoa"},
     lambda r: r["matches"] >= 5 and not r["errors"] and r["excluded"]["not_diamond"] > 0
     and all(a["diamond"] is True for a in r["articles"]) and years_desc(r["articles"])),
    ("scielo_search", {"query": "dengue", "collections": ["scl"], "subject_area": "Health Sciences",
                       "year_from": 2020, "limit": 20},
     lambda r: r["matches"] > 0 and r["collections_searched"] == ["scl"]
     and all(int(str(a["year"])[:4]) >= 2020 for a in r["articles"] if a["year"])),
    ("find_journal_templates", {"area": "biolog"}, lambda r: r["count"] >= 1 and Path(r["journals"][0]["files"][0]).exists()),
    ("find_journal_templates", {"query": "producao"}, lambda r: r["count"] >= 1),
    # PATH 1: 20 most recent relevant Diamond papers, saved as metadata + abstract OKF.
    ("search_papers", {"query": "hidroides OR hydroids OR Hydrozoa", "bundle": "test"}, check_search_papers),
    # PATH 2: full text for selected papers (one from the search, upgraded in place)
    # + a non-Diamond paper (Zoologia: APC) + a bad DOI.
    ("fetch_full_papers", {"papers": ["10.11606/1807-0205/2021.61.06",
                                      "10.1590/S1984-46702010000600016",
                                      "10.9999/does-not-exist"], "bundle": "test"},
     check_fetch),
    ("fetch_full_papers", {"papers": ["10.11606/1807-0205/2021.61.06"], "bundle": "test"},
     lambda r: r["already_in_bundle"] == 1),
    ("list_okf_bundles", {}, lambda r: r["bundles"][0]["papers"] == 20),
]


async def main() -> int:
    env = {**os.environ, "LATAM_OA_LIBRARY": str(LIBRARY)}
    params = StdioServerParameters(command=sys.executable, args=["-m", "latam_oa_mcp.server"], cwd=str(ROOT), env=env)
    failures = 0
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            print(f"tools ({len(tools)}): {sorted(tools)}\n")

            # Chain: get a record id from search, then fetch the full record / article.
            chained = []
            for name, args, check in CASES:
                res = await session.call_tool(name, args)
                data = json.loads(res.content[0].text) if res.content else {}
                ok = not res.is_error and "error" not in data and bool(check(data))
                failures += not ok
                print(f"[{'OK ' if ok else 'FAIL'}] {name} {args}")
                if not ok:
                    print("      ", json.dumps(data, ensure_ascii=False)[:600])
                if name == "search_lareferencia" and ok and not chained:
                    chained.append(("get_repository_record", {"source": "lareferencia", "record_id": data["records"][0]["id"]}))
                if name == "search_oasisbr" and ok:
                    chained.append(("get_repository_record", {"source": "oasisbr", "record_id": data["records"][0]["id"]}))
                if name == "scielo_list_articles" and ok:
                    chained.append(("scielo_get_article", {"pid": data["articles"][0]["pid"]}))

            for name, args in chained:
                res = await session.call_tool(name, args)
                data = json.loads(res.content[0].text)
                ok = not res.is_error and "error" not in data and bool(data.get("title") or data.get("titles"))
                failures += not ok
                print(f"[{'OK ' if ok else 'FAIL'}] {name} {args}")
                print("      ", json.dumps(data, ensure_ascii=False)[:300])

    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
