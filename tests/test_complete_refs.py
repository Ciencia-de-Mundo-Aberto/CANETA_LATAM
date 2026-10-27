"""Completing references that have no abstract (e.g. from an imported .bib) from open sources."""

import httpx
import pytest

from caneta import discovery, writing
from caneta import project as store

PAGE = """<html><head><meta name="description" content="A Elsevier lança o LeapSpace, plataforma de IA."></head>
<body><nav>menu menu menu menu menu menu menu menu menu menu menu menu menu</nav>
<p>O LeapSpace reúne milhões de artigos e livros do acervo da editora em uma plataforma de pesquisa com IA.</p>
<script>var x = 1;</script></body></html>"""


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "api.openalex.org/works/doi:10.1/openalex" in url:
        return httpx.Response(200, json={"abstract_inverted_index": {"Artigos": [0], "idênticos": [1], "citados": [2]}})
    if "api.openalex.org" in url:
        return httpx.Response(200, json={"abstract_inverted_index": None})
    if "api.crossref.org/works/10.1/crossref" in url:
        return httpx.Response(200, json={"message": {"abstract": "<jats:title>Abstract</jats:title><jats:p>Resumo vindo "
                                                                 "do <jats:italic>Crossref</jats:italic>.</jats:p>"}})
    if "api.crossref.org" in url:
        return httpx.Response(200, json={"message": {}})
    if "example.org/leapspace" in url:
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html; charset=utf-8"})
    return httpx.Response(404)


@pytest.fixture
def offline(monkeypatch):
    real = httpx.Client

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setattr(discovery, "fetch_fulltext", lambda *a, **k: {"papers": []})


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from caneta.app import create_app

    c = TestClient(create_app(token="tok"))
    c.get("/auth", params={"t": "tok"}, follow_redirects=False)
    return c


def _project():
    proj = store.create("Completar", mode="B")
    proj.save_references([
        {"id": "a", "type": "article-journal", "title": "Artigos idênticos", "DOI": "10.1/openalex",
         "caneta": {"source": "upload"}},
        {"id": "b", "type": "article-journal", "title": "Outro", "DOI": "10.1/crossref", "caneta": {"source": "upload"}},
        {"id": "c", "type": "webpage", "title": "LeapSpace", "URL": "https://example.org/leapspace",
         "caneta": {"source": "upload"}},
        {"id": "d", "type": "article-journal", "title": "Leiden Manifesto", "DOI": "10.1/nada",
         "caneta": {"source": "upload"}},
    ])
    return proj


def test_complete_references_from_open_sources(offline):
    proj = _project()
    stats = discovery.complete_references(proj, fulltext=True)
    refs = {r["id"]: r for r in proj.references()}
    assert refs["a"]["abstract"] == "Artigos idênticos citados" and refs["a"]["caneta"]["abstract_source"] == "openalex"
    assert refs["b"]["abstract"] == "Resumo vindo do Crossref." and refs["b"]["caneta"]["abstract_source"] == "crossref"
    page = refs["c"]["caneta"]["page_text"]
    assert "lança o LeapSpace" in page and "milhões de artigos" in page and "menu" not in page and "var x" not in page
    assert stats["missing"] == ["d"]
    assert discovery.basis_kind(proj, refs["d"]) == "Só o título"
    assert discovery.basis_kind(proj, refs["c"]) == "Conteúdo da página"


def test_enrich_completes_and_sends_the_best_basis(offline, fake):
    proj = _project()
    sid = proj.sections[1].id
    proj.write_section(sid, "O Manifesto de Leiden critica métricas. A Elsevier lançou o LeapSpace.", "autor")
    provider = fake(default={"texto": "O Manifesto de Leiden critica métricas [@d]. A Elsevier lançou o LeapSpace [@c].",
                             "notas": "d citado pelo título"})
    writing.enrich_intro(proj, sid, provider, ["a", "c", "d"])
    prompt = provider.prompts[-1]
    assert "Resumo: Artigos idênticos citados" in prompt
    assert "Conteúdo da página: A Elsevier lança o LeapSpace" in prompt
    assert "Só o título" in prompt and "título deixa claro" in prompt


def test_references_page_offers_to_complete(offline, client):
    proj = _project()
    page = client.get(f"/p/{proj.slug}/references").text
    assert "4 referência(s) só com o título" in page and "Completar resumos" in page
    discovery.complete_references(proj)
    page = client.get(f"/p/{proj.slug}/references").text
    assert "1 referência(s) só com o título" in page and "(openalex)" in page and "conteúdo da página" in page


def test_link_written_in_another_bib_field(offline):
    """A .bib @misc keeps its link in howpublished, which pandoc turns into 'publisher'."""
    proj = store.create("Link", mode="B")
    proj.save_references([{"id": "x", "type": "", "title": "LeapSpace",
                           "publisher": "Disponível em: https://example.org/leapspace.", "caneta": {"source": "upload"}}])
    assert discovery.ref_url(proj.references()[0]) == "https://example.org/leapspace"
    assert discovery.complete_references(proj)["page"] == 1
