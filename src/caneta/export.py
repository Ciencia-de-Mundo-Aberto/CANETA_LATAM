"""Export: sections → one manuscript; citations are data (references.json) rendered by pandoc.

- docx: citeproc + CSL style; the journal's Word template as reference-doc when downloaded. LaTeX
  equations become native Word equations: labelled ones are numbered and \\ref{label} becomes the number.
- LaTeX: a .zip project (main.tex + references.bib, citations as natbib \\citep/\\citet or biblatex).
  When the chosen journal offers a LaTeX template, its preamble, class files and title block are used
  and the example body is replaced by the manuscript.
- md, BibTeX, CSL-JSON always; PDF only when a TeX engine (tectonic / xelatex) exists."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import yaml

from . import references
from .project import Project

CSL_DIR = Path(__file__).parent / "data" / "csl"
STYLES = {"apa": "apa.csl", "abnt": "abnt.csl", "vancouver": "vancouver.csl"}
DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.S)
RAW_ENV = re.compile(r"(?<!\$\$)\\begin\{(equation|align|gather|multline)(\*?)\}(.+?)\\end\{\1\2\}", re.S)
REF = re.compile(r"\\(eq)?ref\{([^}]+)\}")
LABEL = re.compile(r"\\label\{([^}]+)\}")
TO_INNER = {"align": "aligned", "gather": "gathered"}
# title/abstract commands of journal classes; variants such as \PaperTitlePortuguese get the same text
# (for the author to translate) instead of keeping the template's example text
LANGS = r"(?:english|portuguese|spanish|brazilian)"
TITLE_CMDS = rf"(?:title|Title){LANGS}?(?![a-zA-Z])|PaperTitle\w*|articletitle|{LANGS}Title"
ABSTRACT_CMDS = rf"Abstract\w*|abstract(?!name)\w*|Resumo\w*|resumo|Resumen\w*|resumen|{LANGS}Abstract"
ABSTRACT_ENV = re.compile(r"(\\begin\{abstract\}(?:\[[^\]]*\])?).*?(\\end\{abstract\})", re.S)
FIRST_SECTION = re.compile(r"^[ \t]*\\section\*?\s*[\[{]", re.M)


class ExportError(Exception):
    pass


# ------------------------------------------------------------------ manuscript assembly
def _sections(project: Project, only_approved: bool):
    for s in project.sections:
        if only_approved and s.state != "approved":
            continue
        yield s, project.read_section(s.id).strip()


def _meta(project: Project, abstract: str | None = None) -> dict:
    meta = {"title": project.title, "lang": project.lang.replace("_", "-")}
    if project.data.get("authors"):
        meta["author"] = project.data["authors"]
    if abstract:
        meta["abstract"] = abstract
    return meta


def manuscript_markdown(project: Project, only_approved: bool = False, abstract_in_meta: bool = False) -> str:
    """The whole manuscript as Markdown with a YAML header. With abstract_in_meta the abstract
    sections go to the metadata (LaTeX templates typeset it with the abstract environment)."""
    body, abstract = [], []
    for s, text in _sections(project, only_approved):
        if abstract_in_meta and not text:  # pandoc would turn a trailing empty heading into the bibliography title
            continue
        if abstract_in_meta and s.kind == "abstract":
            abstract.append(text)
        else:
            body.append(f"# {s.title}\n\n{text}\n")
    header = yaml.safe_dump(_meta(project, "\n\n".join(abstract) or None), allow_unicode=True, sort_keys=False)
    return f"---\n{header}---\n\n" + "\n".join(body)


# ------------------------------------------------------------------ LaTeX math → Word
def for_word(md: str) -> str:
    """Make LaTeX equations Word-friendly: unwrap equation/align environments (Word has no \\label),
    number labelled equations as '(n)' and replace \\ref{label}/\\eqref{label} with the number."""
    md = RAW_ENV.sub(lambda m: f"$$\\begin{{{m.group(1)}{m.group(2)}}}{m.group(3)}\\end{{{m.group(1)}{m.group(2)}}}$$",
                     md)
    numbers: dict[str, int] = {}

    def math(m: re.Match) -> str:
        body = m.group(1)
        labels = LABEL.findall(body)
        body = LABEL.sub("", body)
        for env, inner in TO_INNER.items():
            body = re.sub(rf"\\(begin|end)\{{{env}\*?\}}", lambda x, i=inner: f"\\{x.group(1)}{{{i}}}", body)
        body = re.sub(r"\\(begin|end)\{(equation|multline)\*?\}", "", body).strip()
        if labels:
            n = len(set(numbers.values())) + 1
            for label in labels:
                numbers[label] = n
            body += f" \\qquad ({n})"
        return f"$${body}$$"

    md = DISPLAY_MATH.sub(math, md)

    def ref(m: re.Match) -> str:
        n = numbers.get(m.group(2))
        if n is None:
            return m.group(2)  # figure/table labels: keep the label text visible
        return f"({n})" if m.group(1) else str(n)

    return REF.sub(ref, md)


# ------------------------------------------------------------------ helpers
def _csl(project: Project) -> list[str]:
    name = STYLES.get(project.data.get("csl", "apa"))
    path = CSL_DIR / name if name else None
    return [f"--csl={path}"] if path and path.is_file() else []


def _reference_doc(project: Project) -> list[str]:
    folder = project.path / "templates"
    for p in sorted(folder.glob("*")) if folder.is_dir() else []:
        if p.suffix.lower() == ".docx" and not p.name.endswith(".ref.docx"):
            return [f"--reference-doc={p}"]
        if p.suffix.lower() == ".dotx":  # a .dotx is a .docx with another content type
            fixed = p.with_suffix(".ref.docx")
            if not fixed.exists():
                _dotx_to_docx(p, fixed)
            return [f"--reference-doc={fixed}"]
    return []


def _dotx_to_docx(src: Path, dest: Path) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(b"wordprocessingml.template.main+xml", b"wordprocessingml.document.main+xml")
            zout.writestr(item, data)


def replace_command_arg(tex: str, command: str, value: str) -> tuple[str, int]:
    """Replace the {argument} of every \\command[opt]{...} (command is a regex; braces balanced).
    Returns (new text, number of replacements)."""
    count = 0
    for m in reversed(list(re.finditer(rf"\\(?:{command})\s*(\[[^\]]*\])?\s*\{{", tex))):
        depth, i = 1, m.end()
        while i < len(tex) and depth:
            depth += {"{": 1, "}": -1}.get(tex[i], 0)
            i += 1
        tex = tex[: m.end()] + value + "}" + tex[i:]
        count += 1
    return tex, count


def _tex(md: str, *extra: str) -> str:
    import pypandoc

    # no auto_identifiers: pandoc's \label{introduuxe7uxe3o} for accented headings only adds noise
    return pypandoc.convert_text(md, "latex", format="markdown-auto_identifiers", extra_args=list(extra)).replace("\r\n", "\n")


def _escape_tex(text: str) -> str:
    return re.sub(r"([&%$#_{}])", r"\\\1", text)


# ------------------------------------------------------------------ LaTeX project export
def _latex_template(project: Project, dest: Path) -> Path | None:
    """Unpack the journal's LaTeX template (downloaded into templates/) into dest; return its main .tex."""
    from .ingest import IngestError, extract_zip
    from .manuscript_import import ImportError_, find_main_tex

    folder = project.path / "templates"
    if not folder.is_dir():
        return None
    for z in sorted(folder.glob("*.zip")):
        try:
            extract_zip(z, dest)
            return find_main_tex(dest)
        except (IngestError, ImportError_):
            shutil.rmtree(dest, ignore_errors=True)
            dest.mkdir(parents=True, exist_ok=True)
    loose = [p for p in folder.iterdir() if p.suffix.lower() in (".tex", ".cls", ".sty", ".bst", ".bbx", ".cbx")]
    if any(p.suffix.lower() == ".tex" for p in loose):
        for p in loose:
            shutil.copy2(p, dest / p.name)
        try:
            return find_main_tex(dest)
        except ImportError_:
            return None
    return None


def export_latex(project: Project, only_approved: bool = False) -> Path:
    out_dir = project.path / "exports"
    work = Path(tempfile.mkdtemp(prefix="caneta-tex-", dir=out_dir))
    try:
        refs = project.references()
        main = _latex_template(project, work)
        if refs:  # next to the main .tex, where \bibliography{references} looks for it
            ((main.parent if main else work) / "references.bib").write_text(references.to_bibtex(refs),
                                                                              encoding="utf-8")
        if main is None:  # generic article (pandoc template) with natbib + references.bib
            md = manuscript_markdown(project, only_approved, abstract_in_meta=True)
            extra = ["--standalone", "--natbib", "--wrap=preserve"]
            if refs:
                extra.append("--metadata=bibliography:references.bib")
            (work / "main.tex").write_text(_tex(md, *extra), encoding="utf-8", newline="\n")
        else:
            _fill_template(project, main, only_approved, bool(refs))
        target = out_dir / f"{project.slug}-latex.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(work.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(work).as_posix())
        return target
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _fill_template(project: Project, main: Path, only_approved: bool, has_refs: bool) -> None:
    """Keep the journal's preamble and title block; replace its example body with the manuscript.
    Title and abstract go where the template expects them: \\title or class commands such as
    \\PaperTitle, an abstract environment in the title block, or \\Abstract{...} commands."""
    tex = main.read_text(encoding="utf-8", errors="replace")
    start = tex.find("\\begin{document}")
    if start < 0:
        raise ExportError("o template LaTeX da revista não tem \\begin{document}")
    after = tex[start + len("\\begin{document}"):]
    uses_biblatex = "biblatex" in tex[:start]
    cite_flag = "--biblatex" if uses_biblatex else "--natbib"
    # the title block (\maketitle, \coverpage, abstract, keywords...) is whatever precedes the first \section
    sec = FIRST_SECTION.search(after)
    mk = after.find("\\maketitle")
    end = sec.start() if sec else (mk + len("\\maketitle") if mk >= 0 else 0)
    head = tex[: start + len("\\begin{document}")] + after[:end].rstrip() + "\n"

    title = _escape_tex(project.title)
    head, n = replace_command_arg(head, TITLE_CMDS, title)
    if not n:
        head = head.replace("\\begin{document}", f"\\title{{{title}}}\n\\begin{{document}}", 1)
    authors = project.data.get("authors") or []
    # only a single plain \author{A \and B}; structured ones (\authorinfo, affiliations) are the author's to fill
    if authors and len(re.findall(r"\\author\s*[\[{]", head)) == 1 and re.search(
            r"\\author\s*\{[^\\{}]*(?:\\and\b[^\\{}]*)*\}", head):
        head, _ = replace_command_arg(head, "author", " \\and ".join(_escape_tex(a) for a in authors))

    abstract_md = "\n\n".join(text for s, text in _sections(project, only_approved) if s.kind == "abstract")
    abstract = _tex(abstract_md, cite_flag).strip() if abstract_md else ""
    head, n = replace_command_arg(head, ABSTRACT_CMDS, abstract)
    head, envs = ABSTRACT_ENV.subn(lambda m: f"{m.group(1)}\n{abstract}\n{m.group(2)}", head)
    abstract_tex = ""
    if not n and not envs and abstract:
        abstract_tex = f"\\begin{{abstract}}\n{abstract}\n\\end{{abstract}}\n"

    body_md = manuscript_markdown(project, only_approved, abstract_in_meta=True).split("\n---\n", 1)[1]
    body = _tex(body_md, cite_flag, "--wrap=preserve")
    split_at = head.find("\\begin{document}")
    pre, front = head[:split_at], head[split_at + len("\\begin{document}"):]
    if has_refs and not uses_biblatex and "natbib" not in pre:
        pre += "\\usepackage{natbib}\n"
    if has_refs and uses_biblatex and "\\addbibresource" not in pre:
        pre += "\\addbibresource{references.bib}\n"
    bib = ""
    if has_refs:
        if uses_biblatex:
            bib = "\n\\printbibliography\n"
        else:
            # the template's style; none but a \bibliography means the class sets it; else plainnat
            style = re.search(r"\\bibliographystyle\{[^}]+\}", tex)
            if style:
                style_cmd = style.group(0) + "\n"
            else:
                style_cmd = "" if "\\bibliography{" in tex else "\\bibliographystyle{plainnat}\n"
            bib = f"\n{style_cmd}\\bibliography{{references}}\n"
    main.write_text(f"{pre}\\begin{{document}}{front}\n{abstract_tex}\n{body}\n{bib}\n\\end{{document}}\n",
                    encoding="utf-8", newline="\n")


# ------------------------------------------------------------------ entry point
def export(project: Project, fmt: str, only_approved: bool = False) -> Path:
    import pypandoc

    out_dir = project.path / "exports"
    out_dir.mkdir(exist_ok=True)
    refs = project.references()
    refs_file = out_dir / "references.csl.json"
    clean = [{k: v for k, v in r.items() if k != "caneta"} for r in refs]
    refs_file.write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    base = out_dir / project.slug
    if fmt == "md":
        target = base.with_suffix(".md")
        target.write_text(manuscript_markdown(project, only_approved), encoding="utf-8")
        return target
    if fmt == "csljson":
        return refs_file
    if fmt == "bib":
        target = base.with_suffix(".bib")
        target.write_text(references.to_bibtex(refs) if refs else "", encoding="utf-8")
        return target
    if fmt in ("tex", "latex"):
        return export_latex(project, only_approved)
    cite_args = (["--citeproc", f"--bibliography={refs_file}"] + _csl(project)) if refs else []
    if fmt == "docx":
        target = base.with_suffix(".docx")
        pypandoc.convert_text(for_word(manuscript_markdown(project, only_approved)), "docx", format="markdown",
                              outputfile=str(target), extra_args=cite_args + _reference_doc(project))
        return target
    if fmt == "pdf":
        engine = next((e for e in ("tectonic", "xelatex", "lualatex") if shutil.which(e)), None)
        if not engine:
            raise ExportError("nenhum motor TeX encontrado (instale o tectonic) — exporte em .docx")
        target = base.with_suffix(".pdf")
        pypandoc.convert_text(manuscript_markdown(project, only_approved), "pdf", format="markdown",
                              outputfile=str(target), extra_args=cite_args + [f"--pdf-engine={engine}"])
        return target
    raise ExportError(f"formato desconhecido: {fmt}")
