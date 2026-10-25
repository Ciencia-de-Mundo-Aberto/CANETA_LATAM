"""Journal matching within the CANETA index: 117 Diamond OA journals (LATAM focus) with Qualis 2021-2024,
OpenAlex 2-year citedness, scope and themes.

Ranking: TF-IDF cosine (pure Python) between the manuscript (title + abstract + keywords + intro) and each
journal's scope + themes + subarea, plus a small boost for Qualis stratum and citedness. The author can
always choose another journal. Templates are not redistributed: they are downloaded on demand from the
journal's own site (URL in data/journals/templates.json)."""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from . import semantic
from .sections import fold

DATA = Path(__file__).parent / "data" / "journals"
INDEX = DATA / "_INDICE_GERAL.csv"
TEMPLATES = DATA / "templates.json"
QUALIS_BOOST = {"A1": 0.12, "A2": 0.10, "A3": 0.08, "A4": 0.06, "B1": 0.04, "B2": 0.03, "B3": 0.02, "B4": 0.01}
STOPWORDS = set("""
a o os as um uma uns umas de da do das dos em no na nos nas por para com sem sob sobre entre e ou que se seu sua
seus suas como mais menos muito ja nao sim ser estar foi sao este esta estes estas isso esse essa ao aos pelo pela
el la los las un una unos unas del al y o en con por para sin sobre entre que se su sus como mas es son fue
the of and or in on at to for with by from as is are was were be been this that these those an a its it their
revista journal article artigo articulo research pesquisa investigacion study estudo estudio studies estudos
trabalho trabalhos trabajo trabajos work works paper papers resultados results resultado result metodo metodos
methods method objetivo objective aims scope escopo publica publicar publish publishes original originais
cientifico cientifica scientific cientificos area areas campo field also tambem tambien new novo nueva nuevo
mostram mostra aumenta descreve describe describes presente present apresenta neste nesta
revistas periodico periodicos journals articulos artigos articles
aberto aberta abiertos abierto abierta open acesso acceso access gratuito gratuita gratuitos gratis free
diamante diamond online conteudo contenido content licenca licencia license creative commons issn qualis
publicacao publicacoes publicacion publicaciones publication publications pares peer review avaliados
""".split())
# every journal in the index is Diamond OA: words about open access, licences and peer review only repeat
# that (a music journal "100% gratuito, de acesso aberto" must not match a text about open access)
MARKUP = re.compile(
    r"\\[pt]?cite[pt]?\*?(\[[^\]]*\])*\{[^}]*\}"  # LaTeX citations
    r"|\\(?:eq)?ref\{[^}]*\}|\\label\{[^}]*\}"     # cross-references and labels
    r"|\[@[^\]]*\]|@[\w:.-]+"                       # pandoc citations
    r"|\{[^{}]*=[^{}]*\}|\(#[^)]*\)"                # pandoc attributes {reference-type=...} and #anchors
    r"|https?://\S+|\$[^$]*\$")                     # URLs and math
STEM = 5  # compare the first 5 letters: biologia/biology/biología → "biolo", marinho/marine/marino → "marin"


def tokens(text: str) -> list[str]:
    """Folded, stopword-free word stems, so pt/es/en texts and scopes can match each other."""
    return [w[:STEM] for w in re.findall(r"[a-z]{3,}", fold(MARKUP.sub(" ", text))) if w not in STOPWORDS]


@lru_cache(maxsize=1)
def load_index() -> list[dict]:
    with open(INDEX, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["id"] = f"{r['area']}/{r['pasta']}"
    return rows


@lru_cache(maxsize=1)
def templates_map() -> dict:
    try:
        return json.loads(TEMPLATES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get(journal_id: str) -> dict | None:
    return next((r for r in load_index() if r["id"] == journal_id), None)


def _doc(row: dict) -> str:
    return " ".join([row.get("escopo", ""), row.get("temas_principais", ""), row.get("subarea", ""),
                     row.get("journal", ""), row.get("qualis_area_mae", "")])


@lru_cache(maxsize=1)
def _idf() -> tuple[dict, list[Counter]]:
    docs = [Counter(tokens(_doc(r))) for r in load_index()]
    df = Counter(t for d in docs for t in d)
    n = len(docs)
    return {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}, docs


def _vec(counts: Counter, idf: dict) -> dict:
    v = {t: (1 + math.log(c)) * idf.get(t, 0.0) for t, c in counts.items() if t in idf}
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {t: x / norm for t, x in v.items()}


def match(text: str, k: int = 5, area: str | None = None) -> list[dict]:
    idf, docs = _idf()
    q = _vec(Counter(tokens(text)), idf)
    out = []
    for row, d in zip(load_index(), docs, strict=True):
        if area and area != row["area"]:
            continue
        dv = _vec(d, idf)
        common = {t: q[t] * dv[t] for t in q.keys() & dv.keys()}
        sim = sum(common.values())
        try:
            cited = float(row.get("citacoes_2anos_openalex") or 0)
        except ValueError:
            cited = 0.0
        terms = [t for t, _ in sorted(common.items(), key=lambda x: -x[1])[:6]]
        if len(common) == 1:
            sim *= 0.4  # a single shared word is weak evidence ("colonizam" vs "colonial")
        boost = QUALIS_BOOST.get(row.get("qualis_estrato", ""), 0.0) + min(cited, 3.0) * 0.01
        score = sim + boost * (len(common) >= 2)  # prestige only breaks ties between real matches
        out.append({**row, "score": round(score, 4), "similarity": round(sim, 4), "terms": terms})
    if text.strip() and semantic.enabled():
        return _hybrid(text, out, k)
    out.sort(key=lambda r: -r["score"])
    return [r for r in out if r["similarity"] > 0][:k]


@lru_cache(maxsize=2)
def _journal_vectors(model: str):
    return semantic.embed([_doc(r) for r in load_index()])


def _hybrid(text: str, rows: list[dict], k: int) -> list[dict]:
    """0.8 × meaning (multilingual embeddings) + 0.2 × TF-IDF, both scaled to their best match."""
    vecs = _journal_vectors(semantic.MODEL)
    q = semantic.embed([text])[0]
    by_id = {r["id"]: i for i, r in enumerate(load_index())}
    sems = {r["id"]: float(vecs[by_id[r["id"]]] @ q) for r in rows}
    top_sem = max(sems.values()) or 1.0
    top_tf = max((r["similarity"] for r in rows), default=0.0) or 1.0
    for r in rows:
        r["semantic"] = round(sems[r["id"]], 4)
        boost = QUALIS_BOOST.get(r.get("qualis_estrato", ""), 0.0) * 0.25  # tie-breaker only
        r["score"] = round(semantic.SEMANTIC_WEIGHT * sems[r["id"]] / top_sem
                           + (1 - semantic.SEMANTIC_WEIGHT) * r["similarity"] / top_tf + boost, 4)
    rows.sort(key=lambda r: -r["score"])
    return rows[:k]


def profile(row: dict) -> dict:
    """What the project stores when a journal is chosen."""
    return {"id": row["id"], "name": row["journal"], "issn": row["issn"], "area": row["area"],
            "qualis": row.get("qualis_estrato"), "escopo": row.get("escopo", "")[:800],
            "evidence": row.get("diamond_evidence_url"), "template_types": row.get("template_types")}


def template_sources(journal_id: str) -> list[dict]:
    return templates_map().get(journal_id, [])


def fetch_template(journal_id: str, dest_dir: Path) -> list[Path]:
    """Download the journal's own template files (from its site) into dest_dir."""
    import httpx

    dest_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for item in template_sources(journal_id):
        url, name = item.get("url"), Path(item.get("file", "template")).name
        if not url or not url.startswith(("http://", "https://")):
            continue
        target = dest_dir / name
        if target.exists():
            saved.append(target)
            continue
        try:
            r = httpx.get(url, follow_redirects=True, timeout=60,
                          headers={"User-Agent": "Mozilla/5.0 (compatible; CANETA/0.1)"})
        except httpx.HTTPError:
            continue
        is_html = r.content.lstrip()[:15].lower().startswith((b"<!doctype", b"<html"))
        is_office_or_zip = r.content[:4] in (b"PK\x03\x04", b"\xd0\xcf\x11\xe0")
        is_tex = name.lower().endswith((".tex", ".cls", ".sty", ".bst"))
        if r.status_code == 200 and not is_html and (is_office_or_zip or is_tex):
            target.write_bytes(r.content)
            saved.append(target)
    return saved
