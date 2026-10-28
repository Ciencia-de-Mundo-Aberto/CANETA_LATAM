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


def manuscript_markdown(project: Project, only_approved: bool = False, abstract_in_meta: bool = False,
                        layout: bool = False, refs_block: bool = False) -> str:
    """The whole manuscript as Markdown with a YAML header. With abstract_in_meta the abstract
    sections go to the metadata (LaTeX templates typeset it with the abstract environment).

    layout=True prepares a typeset document (Word, PDF, LaTeX): headings carry their cross-reference
    label ({#sec:x}), appendices are unnumbered and come after the references (refs_block puts a
    "References" heading + pandoc's #refs div before them), tables and figures get the labels their
    \\ref{} point to — recovered from the original LaTeX for projects imported before CANETA kept them."""
    sections = [(s, text) for s, text in _sections(project, only_approved)]
    labels = section_labels(project) if layout else {}
    body, abstract, appendix = [], [], []
    for s, text in sections:
        if abstract_in_meta and not text:  # pandoc would turn a trailing empty heading into the bibliography title
            continue
        if abstract_in_meta and s.kind == "abstract":
            abstract.append(text)
            continue
        attrs = []
        if labels.get(s.id):
            attrs.append(f"#{labels[s.id]}")
        is_appendix = layout and bool(APPENDIX.match(s.title.strip()))
        if is_appendix:
            attrs.append(".unnumbered")
        heading = f"# {s.title}" + (" {" + " ".join(attrs) + "}" if attrs else "")
        if is_appendix:  # its subheadings too, or they come out as "9.0.1"
            text = re.sub(r"^(#{2,6} [^{\n]+?)\s*$", r"\1 {.unnumbered}", text, flags=re.M)
        (appendix if is_appendix else body).append(f"{heading}\n\n{text}\n")
    text = "\n\n".join(body)
    if layout:
        text = _restore_floats(project, text)
    if refs_block and project.references():
        title = REFS_TITLE.get(project.lang, REFS_TITLE["pt_BR"])
        text = text.rstrip() + f"\n\n# {title} {{.unnumbered}}\n\n::: {{#refs}}\n:::\n"
    if appendix:
        text = text.rstrip() + "\n\n" + "\n\n".join(appendix)
    header = yaml.safe_dump(_meta(project, "\n\n".join(abstract) or None), allow_unicode=True, sort_keys=False)
    return f"---\n{header}---\n\n" + text


# ------------------------------------------------------------------ cross-references (sections, figures, tables)
APPENDIX = re.compile(r"(anexos?|ap[eê]ndices?|appendix|appendices|annex(es)?)\b", re.I)
REFS_TITLE = {"pt_BR": "Referências", "es": "Referencias", "en": "References"}
SOURCE_SECTION = re.compile(r"\\section\*?\s*\{(?P<title>(?:[^{}]|\{[^{}]*\})+)\}\s*(?:%[^\n]*\n\s*)*\\label\{(?P<label>[^}]+)\}")
SOURCE_TABLE = re.compile(r"\\begin\{table\*?\}.*?\\end\{table\*?\}", re.S)
CAPTION_LINE = re.compile(r"^(\s*:\s+)(.+?)\s*$")
CAPTION_TEXT = r"(?:[^\[\]]|\[[^\[\]]*\])*"  # a caption may hold [@citations]
IMAGE_ID = re.compile(rf"!\[{CAPTION_TEXT}\]\([^)]*\)\{{[^}}]*#(fig:[^\s}}]+)")
IMAGE = re.compile(rf"!\[({CAPTION_TEXT})\]\(([^)]*)\)\{{([^}}]*#fig:[^}}]*)\}}")


def _norm_title(t: str) -> str:
    return re.sub(r"\W+", " ", t).strip().lower()


def _latex_source(project: Project) -> str:
    """The original LaTeX (\\input files inlined) kept by the import, or ''."""
    folder = project.path / "material" / "latex-source"
    if not folder.is_dir():
        return ""
    from .latex_figures import inline_inputs
    from .manuscript_import import ImportError_, find_main_tex

    try:
        main = find_main_tex(folder)
    except ImportError_:
        return ""
    return inline_inputs(main.read_text(encoding="utf-8", errors="replace"), main.parent)


def section_labels(project: Project) -> dict[str, str]:
    """{section id: label}. Sections imported before labels were kept get them back from the original LaTeX,
    matched by title."""
    out = {s.id: s.label for s in project.sections if s.label}
    missing = [s for s in project.sections if not s.label]
    if missing:
        source = {_norm_title(m.group("title")): m.group("label") for m in SOURCE_SECTION.finditer(_latex_source(project))}
        for s in missing:
            if source.get(_norm_title(s.title)):
                out[s.id] = source[_norm_title(s.title)]
    return out


def _restore_floats(project: Project, md: str) -> str:
    """Give tables their \\label from the original LaTeX (in order) and bring back figures the text refers to
    (\\ref{fig:x}) but that are missing — TikZ drawings dropped by an import before CANETA converted them."""
    source = _latex_source(project)
    if not source:
        return md
    md = label_tables(md, source)
    wanted = [lab for lab in dict.fromkeys(re.findall(r"\\ref\{(fig:[^}]+)\}", md)) if lab not in IMAGE_ID.findall(md)]
    if wanted:
        md = _insert_figures(project, md, source, wanted)
    return md


def label_tables(md: str, source: str) -> str:
    """pandoc keeps a LaTeX table's caption but not its \\label: put the labels back on the Markdown table
    captions, in the order the tables appear in the LaTeX source."""
    table_labels = [lab for t in SOURCE_TABLE.findall(source) for lab in re.findall(r"\\label\{([^}]+)\}", t)[:1]]
    if not table_labels:
        return md
    lines, k = md.splitlines(), 0
    for i, line in enumerate(lines):
        m = CAPTION_LINE.match(line)
        if m and k < len(table_labels) and "{#" not in line and _after_table(lines, i):
            lines[i] = f"{m.group(1)}{m.group(2)} {{#{table_labels[k]}}}"
            k += 1
    return "\n".join(lines)


def _after_table(lines: list[str], i: int) -> bool:
    """A ': caption' line belongs to a table when the block just above it is one (dashes or pipes)."""
    j = i - 1
    while j >= 0 and not lines[j].strip():
        j -= 1
    block = []
    while j >= 0 and lines[j].strip():
        block.append(lines[j])
        j -= 1
    return any(re.match(r"^\s*[-+|: ]{5,}$", b) and "-" in b for b in block)


def _insert_figures(project: Project, md: str, source: str, wanted: list[str]) -> str:
    import pypandoc

    from . import latex_figures

    fig_dir = project.path / "figures"
    fig_dir.mkdir(exist_ok=True)
    # render only the figures that are missing and not already rendered on an earlier export
    blocks = {}
    for m in latex_figures.FIGURE.finditer(source):
        label = latex_figures.command_arg(m.group(3), "label") or ""
        if label in wanted:
            blocks[label] = m
    for label, m in blocks.items():
        name = re.sub(r"[^\w-]+", "_", label).strip("_")
        png = fig_dir / f"{name}.png"
        caption_tex = latex_figures.command_arg(m.group(3), "caption") or ""
        if not png.exists():
            _, report = latex_figures.convert_figures(m.group(0), fig_dir, document=source)
            if not report or not report[0]["image"]:
                continue
            rendered = fig_dir / report[0]["image"]
            if rendered != png:
                rendered.replace(png)
        caption = pypandoc.convert_text(caption_tex, "markdown", format="latex", extra_args=["--wrap=none"]).strip()
        caption = re.sub(r"\[\\?\[?@", "[@", caption).replace("\r\n", " ")
        figure = f"\n![{caption}](figures/{png.name}){{#{label} width=\"100%\"}}\n"
        # right after the paragraph that first mentions it
        at = md.find(f"\\ref{{{label}}}")
        end = md.find("\n\n", at)
        md = md[:end] + "\n" + figure + md[end:] if end > 0 else md + figure
    return md


def _numbers(md: str) -> dict[str, str]:
    """Label → number as Word shows it: sections in heading order (unnumbered ones skipped), figures and
    tables in order of appearance."""
    out: dict[str, str] = {}
    sec = 0
    for m in re.finditer(r"^# .*?(?:\{([^}]*)\})?\s*$", md, re.M):
        attrs = m.group(1) or ""
        if ".unnumbered" in attrs or attrs.strip() == "-":
            continue
        sec += 1
        ident = re.search(r"#([^\s}]+)", attrs)
        if ident:
            out[ident.group(1)] = str(sec)
    for n, label in enumerate(dict.fromkeys(IMAGE_ID.findall(md)), 1):
        out[label] = str(n)
    tables = [m.group(1) for m in re.finditer(r"^\s*:\s+.*\{#(tab:[^\s}]+)\}\s*$", md, re.M)]
    for n, label in enumerate(dict.fromkeys(tables), 1):
        out[label] = str(n)
    return out


def _word_captions(md: str, lang: str) -> str:
    """Word does not number captions: write "Figura 1:" / "Tabela 1:" into them."""
    fig, tab = {"en": ("Figure", "Table"), "es": ("Figura", "Tabla")}.get(lang[:2], ("Figura", "Tabela"))
    count = {"fig": 0, "tab": 0}

    def image(m: re.Match) -> str:
        count["fig"] += 1
        return f"![{fig} {count['fig']}: {m.group(1)}]({m.group(2)}){{{m.group(3)}}}"

    md = IMAGE.sub(image, md)

    def caption(m: re.Match) -> str:
        count["tab"] += 1
        return f"{m.group(1)}{tab} {count['tab']}: {m.group(2)}{m.group(3)}"

    return re.sub(r"^(\s*:\s+)(.*?)(\s*\{#tab:[^}]+\}\s*)$", caption, md, flags=re.M)


# ------------------------------------------------------------------ LaTeX math → Word
def for_word(md: str, lang: str = "pt_BR") -> str:
    """Make LaTeX equations Word-friendly: unwrap equation/align environments (Word has no \\label),
    number labelled equations as '(n)' and replace \\ref{label}/\\eqref{label} with the number —
    of the equation, section, figure or table. Figure and table captions get "Figura n:" / "Tabela n:"."""
    targets = _numbers(md)
    md = _word_captions(md, lang)
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
            target = targets.get(m.group(2))
            return target if target else m.group(2)  # unknown label: keep it visible rather than drop it
        return f"({n})" if m.group(1) else str(n)

    return REF.sub(ref, md)


# ------------------------------------------------------------------ helpers
TABLE_FILTER = Path(__file__).parent / "data" / "pandoc" / "table_widths.lua"


def _layout_args(project: Project, md: str) -> list[str]:
    """pandoc options for a typeset document: images found in the project (and the original LaTeX folder),
    wide tables fitted to the page, and numbered sections when the text refers to them (\\ref{sec:x})."""
    import os

    paths = os.pathsep.join(str(p) for p in (project.path, project.path / "material" / "latex-source"))
    args = [f"--resource-path={paths}", f"--lua-filter={TABLE_FILTER}"]
    labels = set(re.findall(r"^# .*\{[^}]*#([^\s}]+)", md, re.M))
    if labels & set(re.findall(r"\\ref\{([^}]+)\}", md)):
        args.append("--number-sections")
    return args


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
        figures = project.path / "figures"
        if figures.is_dir():  # images the manuscript shows (converted TikZ drawings...) go with the .tex
            shutil.copytree(figures, (main.parent if main else work) / "figures", dirs_exist_ok=True)
        if main is None:  # generic article (pandoc template) with natbib + references.bib
            md = manuscript_markdown(project, only_approved, abstract_in_meta=True, layout=True)
            extra = ["--standalone", "--natbib", "--wrap=preserve", f"--lua-filter={TABLE_FILTER}"]
            if "--number-sections" in _layout_args(project, md):
                extra.append("--number-sections")
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

    body_md = manuscript_markdown(project, only_approved, abstract_in_meta=True, layout=True).split("\n---\n", 1)[1]
    body = _tex(body_md, cite_flag, "--wrap=preserve", f"--lua-filter={TABLE_FILTER}")
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
    if fmt in ("docx", "pdf"):
        md = manuscript_markdown(project, only_approved, abstract_in_meta=True, layout=True, refs_block=True)
        layout = _layout_args(project, md)
    if fmt == "docx":
        target = base.with_suffix(".docx")
        pypandoc.convert_text(for_word(md, project.lang), "docx", format="markdown", outputfile=str(target),
                              extra_args=cite_args + layout + _reference_doc(project))
        return target
    if fmt == "pdf":
        engine = next((e for e in ("tectonic", "xelatex", "lualatex") if shutil.which(e)), None)
        if not engine:
            raise ExportError("nenhum motor TeX encontrado (instale o tectonic) — exporte em .docx")
        target = base.with_suffix(".pdf")
        pypandoc.convert_text(md, "pdf", format="markdown", outputfile=str(target),
                              extra_args=cite_args + layout + [f"--pdf-engine={engine}"])
        return target
    raise ExportError(f"formato desconhecido: {fmt}")
