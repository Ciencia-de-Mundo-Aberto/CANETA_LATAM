import pytest

from caneta import grounding
from caneta import project as store
from caneta.sections import StateError, guess_kind


def test_create_from_scratch_has_imrad_sections():
    proj = store.create("Hidroides do Nordeste", lang="pt_BR", mode="B")
    kinds = [s.kind for s in proj.sections]
    assert kinds == ["abstract", "intro", "methods", "results", "discussion", "conclusion"]
    assert proj.slug == "hidroides-do-nordeste"
    assert store.list_projects()[0].slug == proj.slug


def test_snapshots_approval_lock_and_delete_keep_history():
    proj = store.create("Teste", mode="B", structure="empty")
    sec = proj.add_section("Introdução")
    assert sec.kind == "intro"
    proj.write_section(sec.id, "versão 1", "autor")
    proj.write_section(sec.id, "versão 2", "autor")
    assert proj.read_section(sec.id) == "versão 2"
    assert len(proj.history(sec.id)) == 1  # snapshot of version 1
    proj.set_state(sec.id, "approved")
    with pytest.raises(StateError):
        proj.write_section(sec.id, "versão 3", "autor")
    proj.set_state(sec.id, "revising")
    proj.restore(sec.id, proj.history(sec.id)[-1]["file"])
    assert proj.read_section(sec.id) == "versão 1"
    proj.delete_section(sec.id)
    assert not proj.sections
    assert (proj.path / "history" / sec.id).is_dir()


def test_move_and_custom_sections():
    proj = store.create("Teste", mode="B", structure="empty")
    a = proj.add_section("Agradecimentos")
    b = proj.add_section("Resultados e Discussão")
    assert a.kind == "custom" and b.kind == "results"
    proj.move_section(b.id, -1)
    assert [s.id for s in proj.sections] == [b.id, a.id]


def test_guess_kind_multilingual():
    assert guess_kind("Materiales y Métodos") == "methods"
    assert guess_kind("Considerações finais") == "conclusion"
    assert guess_kind("Abstract") == "abstract"


def test_strip_unknown_keys_keeps_known_and_emails():
    text = "Como mostrado [@silva2023; @inventado2024], ver também @fake2020. Contato: a@b.com."
    out, removed = grounding.strip_unknown_keys(text, {"silva2023"})
    assert "[@silva2023]" in out and "inventado2024" not in out and "fake2020" not in out
    assert "a@b.com" in out
    assert removed == ["fake2020", "inventado2024"]


def test_citations_preserved_detects_dropped_keys():
    assert grounding.citations_preserved("A [@x] e [@y].", "A [@x].") == ["y"]


def test_check_section_flags():
    text = "Estudos mostram que a diversidade é maior no verão. Isso é citado [@desconhecido].\n\nE termina sem ponto"
    findings = grounding.check_section(text, "intro", known_keys=set())
    rules = {f["rule"] for f in findings}
    assert {"1", "3", "6"} <= rules


def test_own_work_sentences_need_no_citation():
    text = ("Este trabalho descreve a fauna de hidroides. Neste estudo, coletamos em 10 estações. "
            "We found 30 species. Estudos mostram que a riqueza aumenta no verão.")
    flagged = grounding.unsupported_claims(text)
    assert flagged == ["Estudos mostram que a riqueza aumenta no verão."]


def test_numbers_against_fulltext():
    para = "Silva encontrou 45 espécies [@silva], enquanto nós encontramos 30. Outro registrou 99 espécies [@silva]."
    out = grounding.check_numbers_against_fulltext(para, {"silva": "Registramos 45 espécies de hidroides."})
    assert len(out) == 1 and "99" in out[0]["descricao"]
