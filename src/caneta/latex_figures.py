"""LaTeX figures that pandoc cannot read (TikZ, pgfplots) → images, so an imported article keeps them.

pandoc drops a figure drawn with TikZ: its caption, its label and the drawing disappear, and every
"Figura \\ref{fig:x}" becomes "??". Before conversion, CANETA:
  1. inlines \\input/\\include files (figures often live in their own file);
  2. compiles each TikZ/pgfplots figure alone (standalone class + the article's own packages and
     TikZ settings) with the TeX engine on the machine, and turns the PDF into a PNG;
  3. puts back an ordinary figure — \\includegraphics + the same caption + the same label — that pandoc
     reads as an image with an id, so \\ref{fig:x} keeps working in Word, LaTeX and PDF.
Without a TeX engine the figure is kept as a visible placeholder (caption + label), never silently lost."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

INPUT = re.compile(r"^(?P<pre>[^%\n]*?)\\(?:input|include)\s*\{(?P<name>[^}]+)\}", re.M)
FIGURE = re.compile(r"\\begin\{(figure\*?)\}(\[[^\]]*\])?(.*?)\\end\{\1\}", re.S)
DRAWING = re.compile(r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}", re.S)
NEEDS_RENDER = ("\\begin{tikzpicture}", "\\begin{axis}", "\\tikz")
# preamble lines worth carrying into the standalone document that draws a figure
KEEP = re.compile(r"^\s*\\(usepackage|RequirePackage|usetikzlibrary|usepgfplotslibrary|pgfplotsset|tikzset|"
                  r"definecolor|colorlet|newcommand|renewcommand|DeclareMathOperator)\b")
SKIP_PACKAGES = re.compile(r"\{[^}]*\b(geometry|hyperref|natbib|biblatex|fancyhdr|titlesec|caption|subcaption|"
                           r"lineno|setspace|cleveref|authblk)\b")
ENGINES = ("pdflatex", "lualatex", "xelatex", "tectonic")


def engine() -> str | None:
    return next((e for e in ENGINES if shutil.which(e)), None)


def inline_inputs(tex: str, folder: Path, depth: int = 0) -> str:
    """Replace \\input{x} / \\include{x} with the file's content (recursively, commented lines skipped)."""
    if depth > 10:
        return tex

    def repl(m: re.Match) -> str:
        name = m.group("name").strip()
        for cand in (folder / name, folder / f"{name}.tex"):
            if cand.is_file():
                body = cand.read_text(encoding="utf-8", errors="replace")
                return m.group("pre") + inline_inputs(body, cand.parent, depth + 1)
        return m.group(0)

    return INPUT.sub(repl, tex)


def command_arg(text: str, command: str) -> str | None:
    """Argument of the first \\command{...} (balanced braces), or None."""
    m = re.search(rf"\\{command}\*?\s*(\[[^\]]*\])?\s*\{{", text)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.end(): i - 1]


def _preamble(tex: str) -> str:
    head = tex.split("\\begin{document}", 1)[0]
    lines = [ln for ln in head.splitlines() if KEEP.match(ln) and not SKIP_PACKAGES.search(ln)]
    if not any("{tikz}" in ln for ln in lines):
        lines.insert(0, "\\usepackage{tikz}")
    return "\n".join(lines)


def render(drawing: str, preamble: str, out_png: Path, tex_engine: str, dpi: int = 220) -> bool:
    """Compile one drawing with the standalone class and save it as PNG; False when it does not compile."""
    work = Path(tempfile.mkdtemp(prefix="caneta-fig-"))
    try:
        doc = (f"\\documentclass[border=6pt]{{standalone}}\n{preamble}\n\\begin{{document}}\n"
               f"{drawing}\n\\end{{document}}\n")
        (work / "fig.tex").write_text(doc, encoding="utf-8")
        cmd = [tex_engine, "fig.tex"] if tex_engine == "tectonic" else \
            [tex_engine, "-interaction=nonstopmode", "-halt-on-error", "fig.tex"]
        try:
            subprocess.run(cmd, cwd=work, capture_output=True, timeout=180,
                           creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
        except (OSError, subprocess.TimeoutExpired):
            return False
        pdf = work / "fig.pdf"
        if not pdf.is_file():
            return False
        try:  # PyMuPDF, already installed with latam-oa-mcp
            import pymupdf as fitz
        except ImportError:
            import fitz

        with fitz.open(pdf) as d:
            d[0].get_pixmap(dpi=dpi).save(str(out_png))
        return out_png.is_file()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def convert_figures(tex: str, out_dir: Path, log=None, document: str | None = None) -> tuple[str, list[dict]]:
    """Return (tex with every TikZ figure replaced by an \\includegraphics figure, report). Images go to
    out_dir as <label>.png (paths in the tex are absolute, so pandoc finds them). When tex is only a piece
    of an article, document is the whole article, whose preamble (packages, TikZ libraries) is used."""
    say = log or (lambda *_: None)
    report: list[dict] = []
    tex_engine = engine()
    preamble = _preamble(document or tex)
    out_dir.mkdir(parents=True, exist_ok=True)

    def repl(m: re.Match) -> str:
        env, body = m.group(1), m.group(3)
        if not any(k in body for k in NEEDS_RENDER):
            return m.group(0)  # \includegraphics figures: pandoc reads them as they are
        caption = command_arg(body, "caption") or ""
        label = command_arg(body, "label") or f"fig:{len(report) + 1}"
        name = re.sub(r"[^\w-]+", "_", label).strip("_") or f"fig{len(report) + 1}"
        drawing = "\n".join(DRAWING.findall(body)) or body
        png = out_dir / f"{name}.png"
        ok = bool(tex_engine) and render(drawing, preamble, png, tex_engine)
        report.append({"label": label, "image": png.name if ok else None, "caption": caption})
        if ok:
            say(f"  figura {label}: desenho LaTeX convertido em imagem ({png.name})")
            path = png.resolve().as_posix()
            return (f"\\begin{{{env}}}\n\\centering\n\\includegraphics[width=\\linewidth]{{{path}}}\n"
                    f"\\caption{{{caption}}}\n\\label{{{label}}}\n\\end{{{env}}}")
        why = "nenhum motor TeX instalado" if not tex_engine else "o desenho não compilou sozinho"
        say(f"  figura {label}: não convertida ({why}); fica um aviso no texto")
        return (f"\n\n[FIGURA NÃO CONVERTIDA ({label}): {caption} — o desenho LaTeX (TikZ) não pôde ser "
                f"convertido em imagem ({why}). Insira a imagem aqui.]\n\n")

    return FIGURE.sub(repl, tex), report
