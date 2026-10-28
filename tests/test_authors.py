"""Authors and affiliations: collected from LaTeX / ORCID, filled into exports, left out when double-blind."""

import re
import zipfile

import httpx
import pytest

from caneta import authors, export, latex_figures, manuscript_import
from caneta import project as store

PLAIN = r"""\documentclass{article}
\title{T}
\author{Ana Silva\thanks{Universidade Federal de Sergipe, Aracaju, Brasil. \texttt{ana@ufs.br}} \and
Bruno Lima \\ Universidad de Chile, Santiago \\ \email{bruno@uchile.cl} \\ \orcid{0000-0002-1825-0097}}
\begin{document}\maketitle
\section{Intro}
Texto.
\end{document}
"""
AUTHBLK = r"""\documentclass{article}
\usepackage{authblk}
\author[1,2]{Carla Souza}
\author[2]{Diego Rojas}
\affil[1]{Departamento de Física, UFBA, Salvador, Brasil}
\affil[2]{Instituto X, UNAM, México}
\begin{document}
\section{A}
\end{document}
"""
ELS = r"""\documentclass{elsarticle}
\begin{document}
\begin{frontmatter}
\author[a]{Eva Gomes\corref{cor1}}
\ead{eva@usp.br}
\affiliation[a]{organization={Universidade de São Paulo}, city={São Paulo}, country={Brasil}}
\end{frontmatter}
\section{A}
\end{document}
"""


def test_from_latex_plain_thanks_lines_email_orcid():
    a = authors.from_latex(PLAIN)
    assert [x["name"] for x in a] == ["Ana Silva", "Bruno Lima"]
    assert a[0]["affiliation"] == "Universidade Federal de Sergipe, Aracaju, Brasil" and a[0]["email"] == "ana@ufs.br"
    assert a[1]["affiliation"] == "Universidad de Chile, Santiago" and a[1]["email"] == "bruno@uchile.cl"
    assert a[1]["orcid"] == "0000-0002-1825-0097"


def test_from_latex_authblk_and_elsarticle():
    a = authors.from_latex(AUTHBLK)
    assert a[0]["affiliation"] == "Departamento de Física, UFBA, Salvador, Brasil; Instituto X, UNAM, México"
    assert a[1]["affiliation"] == "Instituto X, UNAM, México"
    e = authors.from_latex(ELS)
    assert e == [{"name": "Eva Gomes", "affiliation": "Universidade de São Paulo, São Paulo, Brasil",
                  "email": "eva@usp.br", "orcid": "", "corresponding": True}]


def test_orcid_checksum_and_lookup():
    assert authors.valid_orcid("https://orcid.org/0000-0002-1825-0097") == "0000-0002-1825-0097"
    assert authors.valid_orcid("0000-0002-1825-0098") is None
    record = {"person": {"name": {"given-names": {"value": "Josiah"}, "family-name": {"value": "Carberry"}},
                         "emails": {"email": [{"email": "j@brown.edu"}]}},
              "activities-summary": {"employments": {"affiliation-group": [{"summaries": [{"employment-summary": {
                  "department-name": "Psychoceramics", "organization": {"name": "Brown University",
                                                                         "address": {"city": "Providence", "country": "US"}}}}]}]}}}
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=record)

    found = authors.from_orcid("0000-0002-1825-0097", httpx.Client(transport=httpx.MockTransport(handler)))
    assert found["name"] == "Josiah Carberry" and found["email"] == "j@brown.edu"
    assert found["affiliation"] == "Psychoceramics, Brown University, Providence, US"
    assert seen == ["https://pub.orcid.org/v3.0/0000-0002-1825-0097/record"]
    with pytest.raises(ValueError):
        authors.from_orcid("1234")


def _project():
    proj = store.create("Artigo", lang="pt_BR", mode="A", structure="empty")
    proj.add_section("Introdução", None, "Texto da introdução.\n")
    proj.data["authors"] = [
        {"name": "Ana Silva", "affiliation": "UFS, Aracaju; UNAM, México", "email": "ana@ufs.br",
         "orcid": "0000-0002-1825-0097", "corresponding": True},
        {"name": "Bruno Lima", "affiliation": "UNAM, México", "email": "", "orcid": "", "corresponding": False},
    ]
    proj.save()
    return proj


def _docx_text(path):
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml))


def test_word_has_numbered_affiliations_and_corresponding_email():
    proj = _project()
    text = _docx_text(export.export(proj, "docx"))
    assert "Ana Silva" in text and "UFS, Aracaju" in text and "UNAM, México" in text
    assert text.count("UNAM, México") == 1  # shared affiliation, one number
    assert "Autor(a) correspondente: ana@ufs.br" in text
    assert "orcid.org/0000-0002-1825-0097" in text
    core = zipfile.ZipFile(export.export(proj, "docx")).read("docProps/core.xml").decode("utf-8")
    assert "Ana Silva" in core


def test_double_blind_removes_authors_and_gives_a_title_page():
    proj = _project()
    proj.data["blind"] = True
    proj.save()
    path = export.export(proj, "docx")
    text = _docx_text(path)
    core = zipfile.ZipFile(path).read("docProps/core.xml").decode("utf-8")
    assert "Ana Silva" not in text and "ana@ufs.br" not in text and "Ana Silva" not in core
    md = export.export(proj, "md").read_text(encoding="utf-8")
    assert "Ana Silva" not in md
    page = _docx_text(export.export(proj, "titlepage"))
    assert "Ana Silva" in page and "Bruno Lima" in page and "UNAM, México" in page and "ana@ufs.br" in page


def test_blind_leaks_are_reported():
    proj = _project()
    sid = proj.sections[0].id
    proj.write_section(sid, "Como mostrou Ana Silva em trabalho anterior (contato: ana@ufs.br).\n", "teste")
    assert {w for _, w in authors.leaks(proj)} == {"Ana Silva", "ana@ufs.br"}


def test_latex_export_fills_plain_author_with_thanks():
    proj = _project()
    z = zipfile.ZipFile(export.export(proj, "tex"))
    tex = z.read("main.tex").decode("utf-8")
    assert "Ana Silva\\thanks{UFS, Aracaju. UNAM, México. Autor(a) correspondente: \\texttt{ana@ufs.br}" in tex
    assert "Bruno Lima\\thanks{UNAM, México}" in tex


@pytest.mark.skipif(not latex_figures.engine(), reason="no TeX engine on this machine")
def test_pdf_shows_authors_and_affiliations():
    import pymupdf

    proj = _project()
    text = "".join(p.get_text() for p in pymupdf.open(export.export(proj, "pdf")))
    assert "Ana Silva" in text and "UFS, Aracaju" in text and "ana@ufs.br" in text


def test_latex_import_collects_authors(tmp_path):
    src = tmp_path / "artigo.tex"
    src.write_text(PLAIN, encoding="utf-8")
    proj = manuscript_import.import_article(src)
    assert [a["name"] for a in authors.get(proj)] == ["Ana Silva", "Bruno Lima"]
    assert authors.get(proj)[1]["orcid"] == "0000-0002-1825-0097"


def test_old_projects_with_plain_names_still_work():
    proj = store.create("Velho", lang="pt_BR", mode="A", structure="empty")
    proj.data["authors"] = ["Lucas"]
    proj.save()
    assert authors.get(proj)[0]["name"] == "Lucas"
    assert "Lucas" in export.manuscript_markdown(proj)
