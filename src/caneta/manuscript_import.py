"""Mode A: an existing article → sections.

Accepted: .docx, .odt, .tex, .md/.txt, .rtf, or a .zip of a LaTeX project (main .tex + \\input/\\include
files + .bib + figures). pandoc converts to Markdown with metadata, so the title, authors and abstract
come from the document itself (\\title, \\author, the abstract environment; the Title/Abstract styles in
Word). The text is split at the main heading level (logic from lccv-paper-maker
draft_checks.parse_sections) and each section's kind is guessed from its title.

LaTeX specifics: \\cite/\\citep/\\citet become pandoc citations [@key]; .bib files in the project become
references (marked as uploaded, not verified); cross-references come back as \\ref{label}; equations stay
in LaTeX; the whole LaTeX source is kept in material/latex-source for the author's reference."""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import ingest, references
from . import project as store

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FORMATS = {".md": "markdown", ".markdown": "markdown", ".txt": "markdown", ".docx": "docx", ".odt": "odt",
           ".tex": "latex", ".rtf": "rtf"}
MD_OUT = "markdown-raw_html-native_divs-native_spans-fenced_divs-bracketed_spans"
# pandoc renders \ref{x} as [\[x\]](#x){reference-type="ref" reference="x"}, or [2](#x){...} when it can
# number the target (e.g. a section): give the LaTeX back
PANDOC_REF = re.compile(r'\[(?:\\\[[^\]]*\\\]|[^\]]*)\]\(#[^)]*\)\{reference-type="(\w+)" reference="([^"]+)"\}')
MAIN_NAMES = ("main", "manuscript", "paper", "article", "artigo", "manuscrito", "ms")


class ImportError_(Exception):
    pass


@dataclass
class Part:
    title: str
    body: str


# ------------------------------------------------------------------ conversion
def _split_front_matter(text: str) -> tuple[dict, str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end > 0:
            try:
                meta = yaml.safe_load(text[4:end]) or {}
            except yaml.YAMLError:
                meta = {}
            return (meta if isinstance(meta, dict) else {}), text[end + 5:]
    return {}, text


def _flat(value) -> str:
    """Metadata values may be strings, lists or nested pandoc structures."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(_flat(v) for v in value)
    if isinstance(value, dict):
        return " ".join(_flat(v) for v in value.values())
    return str(value).strip()


def to_markdown(path: Path) -> tuple[dict, str]:
    """(metadata: title/author/abstract, markdown body)."""
    fmt = FORMATS.get(path.suffix.lower())
    if not fmt:
        raise ImportError_(f"formato não suportado: {path.suffix} (use .docx, .odt, .tex, .md ou um .zip LaTeX)")
    if fmt == "markdown":
        return _split_front_matter(path.read_text(encoding="utf-8", errors="replace"))
    import pypandoc

    out = pypandoc.convert_file(str(path), MD_OUT, format=fmt, extra_args=["--wrap=none", "--standalone"],
                                cworkdir=str(path.parent))  # so \input{chapter} resolves next to the main file
    meta, body = _split_front_matter(out.replace("\r\n", "\n"))
    body = PANDOC_REF.sub(lambda m: f"\\{m.group(1)}{{{m.group(2)}}}", body)
    return meta, body


def find_main_tex(folder: Path) -> Path:
    candidates = []
    for p in folder.rglob("*.tex"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\\documentclass" in text:
            score = (2 if "\\begin{document}" in text else 0) + (1 if p.stem.lower() in MAIN_NAMES else 0)
            candidates.append((score, p.stat().st_size, p))
    if not candidates:
        raise ImportError_("o .zip não tem um .tex com \\documentclass")
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return candidates[0][2]


# ------------------------------------------------------------------ splitting
def split(md: str) -> tuple[str | None, list[Part]]:
    """(document title or None, sections at the main heading level). Text before the first heading
    becomes an opening part only when it is not just the title."""
    lines = md.splitlines()
    heads, in_code = [], False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            in_code = not in_code
        m = None if in_code else HEADING.match(line)
        if m:
            heads.append((i, len(m.group(1)), re.sub(r"\*\*|__|\{[^}]*\}", "", m.group(2)).strip()))
    if not heads:
        return None, [Part("Texto", md.strip())] if md.strip() else []
    levels = sorted({h[1] for h in heads})
    title = None
    if len(levels) > 1 and sum(1 for h in heads if h[1] == levels[0]) == 1:
        main = levels[1]
        title = next(h[2] for h in heads if h[1] == levels[0])
    else:
        main = levels[0]
    parts = []
    first_main = next((i for i, lv, _ in heads if lv == main), len(lines))
    pre = "\n".join(line for line in lines[:first_main] if not (HEADING.match(line) and title and title in line)).strip()
    if pre:
        parts.append(Part("Abertura", pre))
    for k, (i, level, t) in enumerate(heads):
        if level != main:
            continue
        end = next((n for n, lv, _ in heads[k + 1:] if lv <= main), len(lines))
        parts.append(Part(t, "\n".join(lines[i + 1:end]).strip("\n")))
    return title, parts


# ------------------------------------------------------------------ import
ABSTRACT_TITLE = {"pt_BR": "Resumo", "es": "Resumen", "en": "Abstract"}


def import_article(path: Path, lang: str = "pt_BR", title: str | None = None) -> store.Project:
    path = Path(path)
    staging = None
    bibs: list[Path] = []
    try:
        if path.suffix.lower() == ".zip":
            staging = Path(tempfile.mkdtemp(prefix="caneta-latex-"))
            ingest.extract_zip(path, staging)
            source = find_main_tex(staging)
            bibs = sorted(staging.rglob("*.bib"))
        else:
            source = path
            bibs = sorted(p for p in path.parent.glob("*.bib")) if path.suffix.lower() == ".tex" else []
        meta, md = to_markdown(source)
        doc_title, parts = split(md)
        name = title or _flat(meta.get("title")) or doc_title or path.stem
        proj = store.create(name, lang=lang, mode="A", structure="empty")
        authors = meta.get("author")
        if authors:
            proj.data["authors"] = [_flat(a) for a in (authors if isinstance(authors, list) else [authors])]
            proj.save()
        abstract = _flat(meta.get("abstract"))
        if abstract and not any(p.title == "Abertura" for p in parts):
            proj.add_section(ABSTRACT_TITLE.get(lang, "Resumo"), "abstract", abstract + "\n")
        for part in parts:
            if part.title == "Abertura":
                body = (abstract + "\n\n" + part.body).strip() if abstract else part.body
                proj.add_section(ABSTRACT_TITLE.get(lang, "Resumo") if len(body) < 3000 else "Abertura",
                                 "abstract" if len(body) < 3000 else None, body + "\n")
            else:
                proj.add_section(part.title, None, part.body + "\n")
        added = _import_bibs(proj, bibs)
        if staging is not None:
            shutil.copytree(staging, proj.path / "material" / "latex-source", dirs_exist_ok=True)
        proj.log("article_imported", source=path.name, sections=len(proj.sections), references=len(added),
                 main_tex=source.name if staging else None)
        return proj
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def _import_bibs(proj: store.Project, bibs: list[Path]) -> list[str]:
    keys: list[str] = []
    for bib in bibs:
        try:
            refs, new = references.add(proj.references(), references.import_bibtex(bib))
        except Exception:  # noqa: BLE001 - a broken .bib must not stop the import
            continue
        proj.save_references(refs)
        keys += new
    return keys
