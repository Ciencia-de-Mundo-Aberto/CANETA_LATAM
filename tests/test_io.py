import zipfile

import pytest

from caneta import checkpoint, discovery, export, ingest, journals, manuscript_import
from caneta import project as store

ARTICLE_MD = """# Hidroides do Nordeste

Texto de abertura curto.

## Introdução

Os hidroides são comuns [@silva2023hidroides].

## Materiais e Métodos

Coletamos em 10 estações.

### Área de estudo

Sergipe.

## Resultados

Encontramos 30 espécies.
"""


def test_import_markdown_article_splits_sections(tmp_path):
    f = tmp_path / "artigo.md"
    f.write_text(ARTICLE_MD, encoding="utf-8")
    proj = manuscript_import.import_article(f)
    assert proj.title == "Hidroides do Nordeste" and proj.mode == "A"
    titles = [s.title for s in proj.sections]
    assert titles[1:] == ["Introdução", "Materiais e Métodos", "Resultados"]
    methods = next(s for s in proj.sections if s.kind == "methods")
    assert "### Área de estudo" in proj.read_section(methods.id)


def test_import_docx_via_pandoc(tmp_path):
    import pypandoc

    docx = tmp_path / "artigo.docx"
    pypandoc.convert_text(ARTICLE_MD, "docx", format="markdown", outputfile=str(docx))
    proj = manuscript_import.import_article(docx)
    assert [s.kind for s in proj.sections if s.kind != "abstract"][:3] == ["intro", "methods", "results"]


def test_checkpoint_roundtrip_and_tamper_detection(tmp_path, sample_refs):
    proj = store.create("Checkpoint teste", mode="B")
    sec = proj.sections[1]
    proj.write_section(sec.id, "Texto da introdução.", "autor")
    proj.save_references(sample_refs)
    out = checkpoint.export(proj, tmp_path / "x.caneta")
    other = checkpoint.import_(out)
    assert other.slug != proj.slug and other.title == proj.title
    assert other.read_section(sec.id) == "Texto da introdução."
    assert other.references()[0]["id"] == "silva2023hidroides"
    # tampered member → rejected
    bad = tmp_path / "bad.caneta"
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(bad, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith(".md") and "sections/" in item.filename:
                data = b"alterado"
            zout.writestr(item, data)
    with pytest.raises(checkpoint.CheckpointError):
        checkpoint.import_(bad)


def test_malicious_zip_is_rejected(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../../escape.txt", "x")
    with pytest.raises(ingest.IngestError):
        ingest.extract_zip(evil, tmp_path / "out")


def test_material_zip_and_bib_become_references(tmp_path):
    proj = store.create("Material", mode="B")
    bib = "@article{costa2020,\n title={Hidroides epifíticos},\n author={Costa, João},\n year={2020},\n journal={Biota}\n}\n"
    z = tmp_path / "material.zip"
    with zipfile.ZipFile(z, "w") as zz:
        zz.writestr("resultados/tabela.csv", "especie,n\nObelia,12\n")
        zz.writestr("refs.bib", bib)
    summary = ingest.add_material(proj, [z])
    assert "costa2020" in summary["references"]
    ref = next(r for r in proj.references() if r["id"] == "costa2020")
    assert ref["caneta"]["verified"] is False


def test_export_docx_and_md_render_citations(sample_refs):
    proj = store.create("Exportação", mode="B")
    proj.write_section(proj.sections[1].id, "Hidroides são comuns [@silva2023hidroides].", "autor")
    proj.save_references(sample_refs)
    md = export.export(proj, "md")
    assert "[@silva2023hidroides]" in md.read_text(encoding="utf-8")
    docx = export.export(proj, "docx")
    with zipfile.ZipFile(docx) as z:
        body = z.read("word/document.xml").decode("utf-8")
    assert "Silva" in body and "2023" in body  # citeproc rendered the citation
    bib = export.export(proj, "bib").read_text(encoding="utf-8")
    assert "silva2023hidroides" in bib


def test_journal_matching_ranks_by_scope():
    eng = journals.match("concreto estruturas engenharia civil materiais de construção cimento durabilidade", k=5)
    assert eng and any(j["area"] == "Engenharias" or "Construc" in j["journal"] for j in eng)
    edu = journals.match("ensino de ciências formação de professores educação básica", k=5)
    assert any(j["area"] in ("Ciencias_Humanas", "Multidisciplinar") for j in edu)
    assert journals.profile(edu[0])["name"]


def test_discovery_add_papers_builds_csl_from_mcp_data(monkeypatch):
    proj = store.create("Busca", mode="B")
    papers = [{"ref": "10.1/x", "title": "Hydroids from Bahia", "authors": ["Marques, Antonio Carlos", "Pérez, C."],
               "journal": "Biota Neotropica", "year": 2021, "doi": "10.1/x", "abstract": "We found 12 species.",
               "diamond": True, "okf_path": None, "fulltext": "not-requested"}]
    keys = discovery.add_papers(proj, papers)
    assert keys == ["marques2021hydroids"]
    again = discovery.add_papers(proj, papers)  # dedup by DOI
    assert again == keys and len(proj.references()) == 1
    ref = proj.references()[0]
    assert ref["author"][0] == {"family": "Marques", "given": "Antonio Carlos"}
    assert ref["issued"] == {"date-parts": [[2021]]}


def test_journal_matching_ignores_markup_and_open_access_boilerplate():
    from caneta import journals

    # every indexed journal is Diamond OA: "acesso aberto e gratuito" must not pull in a music journal
    text = ("O acesso aberto e gratuito ao conteúdo científico depende da comunicação científica, "
            "da bibliometria e da produção científica [@silva2023] (Seção [2](#sec:x){reference-type=\"ref\" "
            "reference=\"sec:x\"}).")
    assert "type" not in journals.tokens(text) and "silva" not in journals.tokens(text)
    top = journals.match(text, k=5)
    assert all("Vortex" not in r["journal"] for r in top)
    assert any(r["area"] == "Ciencias_Sociais_Aplicadas" for r in top[:3])


def test_liinc_is_indexed_with_its_templates():
    from caneta import journals

    row = next(r for r in journals.load_index() if r["issn"] == "1808-3536")
    assert row["journal"] == "Liinc em Revista" and row["qualis_estrato"] == "A1"
    files = [t["file"] for t in journals.template_sources(row["id"])]
    assert "LIINC_Template_2025_v2.docx" in files
    top = journals.match("Comunicação científica, acesso aberto, cientometria e políticas de informação na "
                         "América Latina: produção e circulação do conhecimento.", k=5)
    assert any(r["journal"] == "Liinc em Revista" for r in top)


def test_fetch_template_retries_a_refused_download(tmp_path, monkeypatch):
    import httpx

    from caneta import journals

    calls = []

    class Flaky:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            calls.append(url)
            if len(calls) == 1:
                raise httpx.ConnectError("refused")
            return httpx.Response(200, content=b"PK\x03\x04docx")

    monkeypatch.setattr(httpx, "Client", Flaky)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(journals, "template_sources", lambda jid: [{"file": "t.docx", "url": "https://x/t.docx"}])
    saved = journals.fetch_template("any", tmp_path)
    assert [p.name for p in saved] == ["t.docx"] and len(calls) == 2
