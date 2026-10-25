"""About page, optional OCR fallback and meaning-based ranking (with a fake embedding model)."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from caneta import discovery, extras, ingest, journals, semantic
from caneta import project as store
from caneta.app import create_app

TOPICS = {  # a tiny "multilingual" fake model: words of any language map to the same topic axis
    0: ("hidroid", "hydroid", "hidrozo", "zoolog", "biolog"),
    1: ("concret", "hormig", "estrutur", "construc", "cement", "cimento"),
    2: ("ensino", "teacher", "educa", "docente", "professor"),
}


def fake_embed(texts):
    out = []
    for t in texts:
        low = t.lower()
        v = np.array([sum(low.count(w) for w in words) for words in TOPICS.values()], dtype="float32") + 0.01
        out.append(v / np.linalg.norm(v))
    return np.array(out)


@pytest.fixture
def fake_semantic(monkeypatch):
    monkeypatch.setattr(semantic, "enabled", lambda: True)
    monkeypatch.setattr(semantic, "embed", fake_embed)
    journals._journal_vectors.cache_clear()
    yield
    journals._journal_vectors.cache_clear()


def test_about_page_shows_project_and_manifesto():
    c = TestClient(create_app(token="tok"))
    c.get("/auth", params={"t": "tok"})
    html = c.get("/sobre").text
    assert "Sobre a CANETA" in html and "Aprendizado de Mundo Aberto" in html
    assert "Manifesto da CANETA" in html and "Dinheiro público, conhecimento público" in html


def test_hybrid_ranking_finds_biology_journal_for_a_cross_language_query(fake_semantic):
    ranked = journals.match("Hydroids colonizing harbour substrates", k=5)
    assert ranked and all("semantic" in r for r in ranked)
    assert ranked == sorted(ranked, key=lambda r: -r["score"])
    assert "Revista de Biología Tropical" in [r["journal"] for r in ranked]


def test_search_results_reranked_by_closeness_to_the_intro(fake_semantic):
    proj = store.create("Hidroides do litoral", mode="B")
    intro = next(s for s in proj.sections if s.kind == "intro")
    proj.write_section(intro.id, "Hidroides e fauna marinha em portos.", "autor")
    papers = [{"title": "Concrete durability", "abstract": "cement"}, {"title": "Hydroids of Brazil", "abstract": ""}]
    out = discovery.rerank(proj, papers)
    assert out[0]["title"] == "Hydroids of Brazil" and out[0]["semantic"] > out[1]["semantic"]


def test_rerank_is_a_noop_without_the_feature():
    proj = store.create("X", mode="B")
    papers = [{"title": "a"}, {"title": "b"}]
    assert discovery.rerank(proj, papers) == papers


def test_scanned_pdf_uses_ocr_only_when_installed(tmp_path, monkeypatch):
    pdf = tmp_path / "old.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    calls = []

    def fake_run(src, out, mode, pages, timeout):
        calls.append(mode)
        out.write_text("" if mode == "layout" else "Texto reconhecido " * 60, encoding="utf-8")
        return True

    monkeypatch.setattr(ingest, "_run_converter", fake_run)
    monkeypatch.setattr(extras, "ocr_available", lambda: False)
    assert ingest.convert_pdf(pdf) is None and calls == ["layout"]  # scanned, no OCR: left as PDF
    monkeypatch.setattr(extras, "ocr_available", lambda: True)
    calls.clear()
    out, method = ingest.convert_pdf(pdf)
    assert method == "pdf-ocr" and calls == ["layout", "ocr"]


def test_born_digital_pdf_never_needs_ocr(tmp_path, monkeypatch):
    pdf = tmp_path / "new.pdf"
    pdf.write_bytes(b"%PDF-1.7 fake")
    calls = []

    def fake_run(src, out, mode, pages, timeout):
        calls.append(mode)
        out.write_text("Texto nativo do artigo. " * 60, encoding="utf-8")
        return True

    monkeypatch.setattr(ingest, "_run_converter", fake_run)
    monkeypatch.setattr(extras, "ocr_available", lambda: True)
    assert ingest.convert_pdf(pdf)[1] == "pdf-layout" and calls == ["layout"]
