"""Typeset exports: cross-references, figures, tables, references heading and appendices."""

import re
import shutil
import zipfile

import pytest

from caneta import export, latex_figures, manuscript_import, writing

MAIN = r"""\documentclass{article}
\usepackage{tikz}
\usetikzlibrary{positioning}
\usepackage{natbib}
\title{Fluxos}
\begin{document}
\maketitle
\begin{abstract}
Resumo curto.
\end{abstract}
\section{Introdução}
\label{sec:intro}
Ver a Seção~\ref{sec:metodo}, a Figura~\ref{fig:fluxo} e a Tabela~\ref{tab:dados} \citep{silva2023}.
\section{Método}
\label{sec:metodo}
\subsection{Coleta}
\input{fig_fluxo}
\begin{table}[htbp]
\caption{Dados coletados.}
\label{tab:dados}
\begin{tabular}{ll}
A & um texto bem comprido que passaria da margem se a coluna não tivesse nenhuma largura definida \\
B & outro texto \\
\end{tabular}
\end{table}
\end{document}
"""
FIG = r"""\begin{figure}[htbp]
\centering
\begin{tikzpicture}
\node[draw] (a) {Autor};
\node[draw, right=of a] (b) {Revista};
\draw[->] (a) -- (b);
\end{tikzpicture}
\caption{O fluxo do manuscrito.}
\label{fig:fluxo}
\end{figure}
"""
BIB = "@article{silva2023, author={Silva, Ana}, title={Hidroides}, journal={Rev}, year={2023}}\n"
needs_tex = pytest.mark.skipif(not latex_figures.engine(), reason="no TeX engine on this machine")


def _zip(tmp_path):
    z = tmp_path / "artigo.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("main.tex", MAIN)
        f.writestr("fig_fluxo.tex", FIG)
        f.writestr("refs.bib", BIB)
    return z


def _text(docx) -> str:
    xml = zipfile.ZipFile(docx).read("word/document.xml").decode("utf-8")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml)), xml


@needs_tex
def test_latex_import_keeps_labels_figures_and_table_labels(tmp_path):
    proj = manuscript_import.import_article(_zip(tmp_path))
    labels = {s.title: s.label for s in proj.sections}
    assert labels["Introdução"] == "sec:intro" and labels["Método"] == "sec:metodo"
    assert (proj.path / "figures" / "fig_fluxo.png").is_file()
    metodo = proj.read_section(next(s.id for s in proj.sections if s.title == "Método"))
    assert "](figures/fig_fluxo.png){#fig:fluxo" in metodo and 'width="100%"' in metodo
    assert "{#tab:dados}" in metodo and "## Coleta" in metodo


@needs_tex
def test_word_export_resolves_every_cross_reference(tmp_path):
    proj = manuscript_import.import_article(_zip(tmp_path))
    proj.add_section("Anexos", None, "Material extra.\n\n## A.1 Detalhes\n\nTexto.\n")
    text, xml = _text(export.export(proj, "docx"))
    assert "a Seção 2," in text and "a Figura 1" in text and "a Tabela 1" in text
    assert not re.search(r"\b(sec|fig|tab):", text)
    assert "Figura 1: O fluxo do manuscrito." in text and "Tabela 1: Dados coletados." in text
    assert xml.count("<w:drawing") == 1
    assert '<w:tblW w:type="pct"' in xml  # the wide table fits the page
    refs, annex = text.find("Referências"), text.find("Anexos")
    assert 0 < refs < text.find("Silva, A. (2023)") < annex  # references (with the entries), then the appendix


@needs_tex
def test_pdf_export_has_no_unresolved_references(tmp_path):
    import pymupdf

    proj = manuscript_import.import_article(_zip(tmp_path))
    text = "".join(p.get_text() for p in pymupdf.open(export.export(proj, "pdf")))
    assert "??" not in text and "Seção 2" in text and "Figura 1" in text and "Tabela 1" in text


@needs_tex
def test_old_projects_get_labels_and_figures_back_from_the_latex_source(tmp_path):
    """Projects imported before CANETA kept labels/figures: recovered from material/latex-source on export."""
    proj = manuscript_import.import_article(_zip(tmp_path))
    data = proj.data
    for s in data["sections"]:  # as an old import stored them: no labels, no figure, no table label
        s.pop("label", None)
    proj.save()
    sid = next(s.id for s in proj.sections if s.title == "Método")
    old = re.sub(r"!\[.*?\]\(figures/[^)]*\)\{[^}]*\}\n?", "", proj.read_section(sid), flags=re.S)
    proj.write_section(sid, old.replace(" {#tab:dados}", ""), "teste")
    shutil.rmtree(proj.path / "figures")
    md = export.manuscript_markdown(proj, abstract_in_meta=True, layout=True, refs_block=True)
    assert "# Método {#sec:metodo}" in md and "{#fig:fluxo" in md and "{#tab:dados}" in md
    text, _ = _text(export.export(proj, "docx"))
    assert "a Seção 2," in text and "a Figura 1" in text and "a Tabela 1" in text


def test_proposals_warn_when_structure_is_lost():
    before = "## A formação\n\nTexto (Seção \\ref{sec:x}).\n\n![F.](figures/f.png){#fig:f}\n"
    lost = writing._structure_lost(before, "Texto reescrito.")
    assert any("A formação" in w for w in lost) and "\\ref{sec:x}" in lost and "figura fig:f" in lost
    assert writing._structure_lost(before, before) == []
