import pytest

from caneta import project as store
from caneta import writing
from caneta.llm.base import NoLLM, NullProvider
from caneta.review import rounds

INTRO = ("Os hidroides são cnidários coloniais comuns em substratos artificiais. "
         "A diversidade desse grupo no Nordeste ainda é pouco conhecida [@silva2023hidroides].\n")


def _project(sample_refs):
    proj = store.create("Hidroides", mode="B", structure="empty")
    sec = proj.add_section("Introdução", "intro", INTRO)
    proj.save_references(sample_refs)
    return proj, sec


def test_review_round_anchors_groups_and_decisions(fake, sample_refs):
    proj, sec = _project(sample_refs)
    provider = fake(default={"parecer": "revisar", "findings": [
        {"excerpt": "pouco conhecida", "rotulo": "Imprecisão", "prioridade": "alta",
         "descricao": "Vago.", "acao_sugerida": "Especifique.", "confianca": "alta",
         "edit": {"find": "pouco conhecida", "replace": "pouco documentada em inventários"}},
        {"excerpt": "trecho que não existe no texto", "rotulo": "Erro", "prioridade": "crítica",
         "descricao": "Inventado.", "edit": None},
    ]})
    data = rounds.run_round(proj, sec.id, provider)
    assert proj.section(sec.id).state == "revising"
    # 3 roles for an intro (editor, metodologo is skipped, referencias) → 2 roles × 1 anchored finding, grouped
    groups = data["groups"]
    assert len(groups) == 1 and groups[0]["support"] == 2
    assert all("não existe" not in g["excerpt"] for g in groups)
    # the references reviewer saw the cited abstract
    assert any("Registramos 45 espécies" in p for p in provider.prompts)
    g = rounds.decide(proj, sec.id, data["round"], groups[0]["id"], "accept")
    assert g["state"] == "aceito"
    assert "pouco documentada em inventários" in proj.read_section(sec.id)
    with pytest.raises(rounds.ReviewError):
        rounds.decide(proj, sec.id, data["round"], groups[0]["id"], "reject")  # no reason
    rounds.decide(proj, sec.id, data["round"], groups[0]["id"], "reject", reason="discordo")
    assert rounds.latest(proj, sec.id)["groups"][0]["state"] == "rejeitado"


def test_no_ai_review_uses_grounding_only(sample_refs):
    proj, sec = _project(sample_refs)
    proj.write_section(sec.id, INTRO + "\nEstudos mostram aumento de 40% [@naoexiste].\n", "autor")
    data = rounds.run_round(proj, sec.id, NullProvider())
    rules = {g["roles"][0] for g in data["groups"]}
    assert rules == {"fundamentacao"} and data["groups"]
    warnings = rounds.approve(proj, sec.id)
    assert proj.section(sec.id).state == "approved"
    assert warnings  # unknown key + open groups are warnings, not blocks


def test_polish_strips_invented_keys_and_reports_dropped(fake, sample_refs):
    proj, sec = _project(sample_refs)
    provider = fake(default={"texto": "Os hidroides são comuns [@inventado2025].", "notas": "ok"})
    prop = writing.polish(proj, sec.id, provider)
    assert "inventado2025" not in prop.proposed
    assert any("inventadas" in w for w in prop.warnings)
    assert any("silva2023hidroides" in w for w in prop.warnings)  # citation dropped by the rewrite
    assert proj.read_section(sec.id) == INTRO  # proposals never write
    assert proj.events()[-1]["event"] == "ai_call"


def test_interactive_edit_only_applies_exact_unique_finds(fake, sample_refs):
    proj, sec = _project(sample_refs)
    provider = fake(default={"edits": [
        {"find": "cnidários coloniais", "replace": "cnidários coloniais sésseis", "justificativa": "precisão"},
        {"find": "não está no texto", "replace": "x", "justificativa": "?"},
    ]})
    prop = writing.interactive_edit(proj, sec.id, provider, "seja mais preciso")
    assert "sésseis" in prop.proposed and len(prop.edits) == 1
    assert any("descartada" in w for w in prop.warnings)


def test_enrich_restricts_prompt_to_selected_refs(fake, sample_refs):
    proj, sec = _project(sample_refs)
    provider = fake(default={"texto": INTRO, "notas": ""})
    writing.enrich_intro(proj, sec.id, provider, ["silva2023hidroides"])
    prompt = provider.prompts[-1]
    assert "[@silva2023hidroides]" in prompt and "SOMENTE estas chaves" in prompt


def test_writing_requires_llm(sample_refs):
    proj, sec = _project(sample_refs)
    with pytest.raises(NoLLM):
        writing.polish(proj, sec.id, NullProvider())


def test_diff_html_marks_changes():
    html = writing.diff_html("a b c", "a x c")
    assert "<del>b</del>" in html and "<ins>x</ins>" in html
