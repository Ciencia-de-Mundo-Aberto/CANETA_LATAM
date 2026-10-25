import zipfile

from caneta import export, manuscript_import
from caneta import project as store

MAIN_TEX = r"""\documentclass{article}
\usepackage{natbib}
\title{Hidroides do Nordeste}
\author{Ana Souza \and Bruno Lima}
\begin{document}
\maketitle
\begin{abstract}
Estudamos hidroides em Sergipe.
\end{abstract}
\section{Introdução}
Os hidroides são comuns \citep{silva2023}.
\section{Métodos}
\begin{equation}
S = \sum_i p_i \label{eq:riqueza}
\end{equation}
\input{resultados}
\bibliography{refs}
\end{document}
"""
RESULTS_TEX = r"""\section{Resultados}
Pela Eq.~\ref{eq:riqueza}, encontramos 30 espécies.
"""
BIB = """@article{silva2023,
  author = {Silva, Maria},
  title = {Hidroides de Sergipe},
  journal = {Revista Brasileira de Zoologia},
  year = {2023}
}
"""


def _latex_zip(tmp_path):
    z = tmp_path / "projeto.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("paper/main.tex", MAIN_TEX)
        f.writestr("paper/resultados.tex", RESULTS_TEX)
        f.writestr("paper/refs.bib", BIB)
    return z


def test_import_latex_zip_project(tmp_path):
    proj = manuscript_import.import_article(_latex_zip(tmp_path))
    assert proj.title == "Hidroides do Nordeste"
    assert proj.data["authors"] == ["Ana Souza", "Bruno Lima"]
    kinds = [s.kind for s in proj.sections]
    assert kinds[0] == "abstract" and "Sergipe" in proj.read_section(proj.sections[0].id)
    titles = [s.title for s in proj.sections]
    assert "Resultados" in titles  # came from \input{resultados}
    results = proj.read_section(next(s for s in proj.sections if s.title == "Resultados").id)
    assert "\\ref{eq:riqueza}" in results
    intro = proj.read_section(next(s for s in proj.sections if s.title == "Introdução").id)
    assert "@silva2023" in intro
    assert [r["id"] for r in proj.references()] == ["silva2023"]
    assert (proj.path / "material" / "latex-source" / "paper" / "main.tex").is_file()


def test_import_docx_reads_title_and_abstract(tmp_path):
    import pypandoc

    md = "---\ntitle: Hidroides do Nordeste\nabstract: Resumo curto.\n---\n\n# Introdução\n\nTexto.\n"
    docx = tmp_path / "a.docx"
    pypandoc.convert_text(md, "docx", format="markdown", outputfile=str(docx))
    proj = manuscript_import.import_article(docx)
    assert proj.title == "Hidroides do Nordeste"
    assert proj.sections[0].kind == "abstract"
    assert "Resumo curto" in proj.read_section(proj.sections[0].id)


def test_for_word_numbers_equations_and_refs():
    md = "$$\\begin{equation}\nS = p \\label{eq:a}\n\\end{equation}$$\n\nVer Eq. \\ref{eq:a} e \\eqref{eq:a}; Fig. \\ref{fig:x}."
    out = export.for_word(md)
    assert "\\label" not in out and "\\begin{equation}" not in out
    assert "(1)$$" in out and "Ver Eq. 1 e (1)" in out and "Fig. fig:x" in out


def test_docx_export_has_native_math(tmp_path):
    proj = store.create("Equações", mode="B")
    proj.write_section(proj.sections[1].id,
                       "$$\\begin{equation}\nS = \\sum_i p_i \\label{eq:s}\n\\end{equation}$$\n\nPela Eq. \\ref{eq:s}.",
                       "autor")
    with zipfile.ZipFile(export.export(proj, "docx")) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert "<m:oMath" in xml and "Pela Eq. 1" in xml.replace("</w:t>", "").replace("<w:t xml:space=\"preserve\">", "")


def test_latex_export_without_journal_template(sample_refs):
    proj = store.create("Hidroides", mode="B")
    proj.write_section(proj.sections[1].id, "Hidroides são comuns [@silva2023hidroides].", "autor")
    proj.save_references(sample_refs)
    target = export.export(proj, "tex")
    assert target.name.endswith("-latex.zip")
    with zipfile.ZipFile(target) as z:
        names = z.namelist()
        tex = z.read("main.tex").decode("utf-8")
    assert "references.bib" in names
    assert "\\citep{silva2023hidroides}" in tex and "\\bibliography{references" in tex
    assert "\\refname" not in tex  # empty sections are skipped, none becomes the bibliography title


def test_latex_template_with_class_commands(sample_refs):
    """Journal classes with their own title/abstract commands and a cover page instead of \\maketitle."""
    proj = store.create("Hidroides", mode="B")
    proj.write_section(proj.sections[0].id, "Resumo nosso.", "autor")
    proj.write_section(proj.sections[1].id, "Texto [@silva2023hidroides].", "autor")
    proj.data["authors"] = ["Ana Souza"]
    proj.save()
    proj.save_references(sample_refs)
    tpl = proj.path / "templates"
    tpl.mkdir(exist_ok=True)
    (tpl / "paper.tex").write_text(
        "\\documentclass{cen}\n\\englishTitle{Example}\n\\portugueseTitle{Exemplo}\n"
        "\\author{\\authorinfo[0000]{Charles Babbage}{I}}\n\\englishAbstract{Example abstract.}\n"
        "\\titleformat{\\section}{\\bfseries}\n\\begin{document}\n\\coverpage\n"
        "\\begin{abstract}[spanish]\nResumen de ejemplo.\n\\end{abstract}\n"
        "\\section{Intro}\nExample body.\n\\bibliography{refs}\n\\end{document}\n", encoding="utf-8")
    (tpl / "cen.cls").write_text("% class\n", encoding="utf-8")
    with zipfile.ZipFile(export.export(proj, "tex")) as z:
        tex = z.read("paper.tex").decode("utf-8")
        assert "cen.cls" in z.namelist()
    assert "\\englishTitle{Hidroides}" in tex and "\\portugueseTitle{Hidroides}" in tex
    assert "\\englishAbstract{Resumo nosso.}" in tex and "Resumen de ejemplo" not in tex
    assert "\\begin{abstract}[spanish]\nResumo nosso." in tex
    assert "\\authorinfo[0000]{Charles Babbage}" in tex  # structured authors are left alone
    assert "\\titleformat{\\section}" in tex and "\\coverpage" in tex
    assert "Example body" not in tex and "\\citep{silva2023hidroides}" in tex
    assert "\\bibliographystyle" not in tex  # the class sets it


def test_latex_export_uses_journal_template(sample_refs):
    proj = store.create("Hidroides do Nordeste", mode="B")
    proj.write_section(proj.sections[1].id, "Hidroides são comuns [@silva2023hidroides].", "autor")
    proj.save_references(sample_refs)
    tpl = proj.path / "templates"
    tpl.mkdir(exist_ok=True)
    with zipfile.ZipFile(tpl / "revista-latex.zip", "w") as z:
        z.writestr("template/revista.cls", "% class\n")
        z.writestr("template/template.tex",
                   "\\documentclass{revista}\n\\title{Título de exemplo}\n\\begin{document}\n\\maketitle\n"
                   "\\section{Exemplo}\nTexto de exemplo da revista.\n\\bibliographystyle{revista}\n"
                   "\\bibliography{exemplo}\n\\end{document}\n")
    with zipfile.ZipFile(export.export(proj, "tex")) as z:
        names = z.namelist()
        tex = z.read("template/template.tex").decode("utf-8")
    assert "template/revista.cls" in names and "template/references.bib" in names
    assert "\\documentclass{revista}" in tex and "\\title{Hidroides do Nordeste}" in tex
    assert "Texto de exemplo" not in tex and "\\citep{silva2023hidroides}" in tex
    assert "\\bibliographystyle{revista}" in tex and "\\bibliography{references}" in tex


def test_import_route_accepts_latex_zip_and_exports(tmp_path):
    from fastapi.testclient import TestClient

    from caneta.app import create_app

    c = TestClient(create_app(token="tok"))
    c.get("/auth", params={"t": "tok"}, follow_redirects=False)
    data = _latex_zip(tmp_path).read_bytes()
    r = c.post("/projects/import", files={"file": ("projeto.zip", data, "application/zip")}, data={"lang": "pt_BR"})
    slug = r.headers["HX-Redirect"].split("/")[2]
    assert store.load(slug).title == "Hidroides do Nordeste"
    r = c.get(f"/p/{slug}/export/tex")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    bad = c.post("/projects/import", files={"file": ("x.zip", b"not a zip", "application/zip")}, data={"lang": "pt_BR"})
    assert bad.status_code == 400


def test_numbered_section_refs_come_back_as_ref():
    from caneta.manuscript_import import PANDOC_REF

    md = ('Seções [2](#sec:contexto){reference-type="ref" reference="sec:contexto"} e '
          '[\\[eq:s\\]](#eq:s){reference-type="ref" reference="eq:s"}.')
    out = PANDOC_REF.sub(lambda m: f"\\{m.group(1)}{{{m.group(2)}}}", md)
    assert out == "Seções \\ref{sec:contexto} e \\ref{eq:s}."
