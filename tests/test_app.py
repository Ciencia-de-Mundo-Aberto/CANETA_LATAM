"""End-to-end through the web API (FastAPI TestClient) with a fake LLM provider."""

import re
import time

import pytest
from fastapi.testclient import TestClient

from caneta import config
from caneta import project as store
from caneta.app import create_app
from caneta.llm import registry


@pytest.fixture
def client():
    app = create_app(token="tok")
    c = TestClient(app)
    c.get("/auth", params={"t": "tok"}, follow_redirects=False)
    return c


def wait_job(client, html: str) -> str:
    job_id = re.search(r"/jobs/([0-9a-f]+)", html).group(1)
    for _ in range(100):
        r = client.get(f"/jobs/{job_id}")
        if r.headers.get("HX-Redirect"):
            return r.headers["HX-Redirect"]
        if "notice err" in r.text:
            raise AssertionError(r.text)
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_session_guard():
    c = TestClient(create_app(token="tok"))
    assert c.get("/").status_code == 401
    assert c.get("/auth", params={"t": "wrong"}).status_code == 401


def test_setup_then_full_section_cycle(client, fake, monkeypatch):
    cfg = config.load()
    cfg.setup_done = False
    config.save(cfg)
    assert client.get("/", follow_redirects=False).headers["location"] == "/setup"
    assert "NVIDIA NIM" in client.get("/setup").text
    r = client.post("/setup/save", data={"lang": "pt_BR", "provider_id": "none"})
    assert r.headers["HX-Redirect"] == "/" and config.load().setup_done

    provider = fake(default={"parecer": "revisar", "findings": [
        {"excerpt": "coloniais comuns", "rotulo": "Clareza", "prioridade": "média", "descricao": "Vago.",
         "edit": {"find": "coloniais comuns", "replace": "coloniais frequentes"}}]})
    monkeypatch.setattr(registry, "build", lambda *a, **k: provider)

    r = client.post("/projects/new", data={"title": "Meu artigo", "lang": "pt_BR", "structure": "imrad"})
    slug = r.headers["HX-Redirect"].split("/")[2]
    proj = store.load(slug)
    intro = proj.sections[1]
    page = client.get(f"/p/{slug}/s/{intro.id}")
    assert page.status_code == 200 and "Citações abertas" in page.text

    assert "salvo" in client.post(f"/p/{slug}/s/{intro.id}/save",
                                  data={"text": "Hidroides são cnidários coloniais comuns."}).text
    nxt = wait_job(client, client.post(f"/p/{slug}/s/{intro.id}/review", data={}).text)
    assert nxt.startswith(f"/p/{slug}/s/{intro.id}")
    page = client.get(f"/p/{slug}/s/{intro.id}").text
    gid = re.search(r'id="g-(G1-\d+)"', page).group(1)
    card = client.post(f"/p/{slug}/s/{intro.id}/decide", data={"number": 1, "group": gid, "action": "accept"}).text
    assert "aceito" in card
    assert "coloniais frequentes" in store.load(slug).read_section(intro.id)

    # proposal flow: polish → diff page → accept
    provider.default = {"texto": "Hidroides são cnidários coloniais frequentes no litoral.", "notas": "ok"}
    nxt = wait_job(client, client.post(f"/p/{slug}/s/{intro.id}/polish", data={"extra": ""}).text)
    assert nxt.startswith("/proposals/")
    assert "<ins>" in client.get(nxt).text
    client.post(nxt + "/accept")
    assert "litoral" in store.load(slug).read_section(intro.id)

    r = client.post(f"/p/{slug}/s/{intro.id}/approve", data={})
    assert r.headers["HX-Redirect"].startswith(f"/p/{slug}/s/")
    assert store.load(slug).section(intro.id).state == "approved"

    for path in ("references", "journal", "export", "log"):
        assert client.get(f"/p/{slug}/{path}").status_code == 200
    md = client.get(f"/p/{slug}/export/md")
    assert md.status_code == 200 and "litoral" in md.text
    ck = client.get(f"/p/{slug}/checkpoint")
    assert ck.status_code == 200 and ck.content[:2] == b"PK"
    assert "ai_call" in client.get(f"/p/{slug}/log").text


def test_search_flow_with_stubbed_mcp(client, monkeypatch):
    from caneta import discovery

    papers = [{"ref": "10.9/z", "title": "Hydroids of Sergipe", "authors": ["Mendonça, Luana"], "journal": "PAZ",
               "year": 2022, "doi": "10.9/z", "abstract": "We recorded 30 species.", "diamond": True,
               "found_in": ["scielo"]}]
    monkeypatch.setattr(discovery, "search", lambda proj, q, **kw: {"returned": 1, "errors": [], "papers": papers})
    r = client.post("/projects/new", data={"title": "Busca", "lang": "pt_BR", "structure": "imrad"})
    slug = r.headers["HX-Redirect"].split("/")[2]
    nxt = wait_job(client, client.post(f"/p/{slug}/search", data={"query": "hidroides OR hydroids"}).text)
    page = client.get(nxt).text
    assert "Hydroids of Sergipe" in page
    sid = nxt.rsplit("/", 1)[1]
    client.post(f"/p/{slug}/references/add", data={"search_id": sid, "picks": [0]})
    assert store.load(slug).references()[0]["id"] == "mendonca2022hydroids"


def test_one_task_per_section_and_stale_findings(client, fake, monkeypatch):
    import threading

    from caneta import jobs

    gate = threading.Event()
    provider = fake(default={"parecer": "revisar", "findings": [
        {"excerpt": "cerca de R$ 1 bilhão", "rotulo": "Clareza", "prioridade": "média", "descricao": "Vago."}]})
    slow = provider.complete

    def blocked(*a, **k):
        gate.wait(5)
        return slow(*a, **k)

    monkeypatch.setattr(provider, "complete", blocked)
    monkeypatch.setattr(registry, "build", lambda *a, **k: provider)
    slug = client.post("/projects/new", data={"title": "Trava", "lang": "pt_BR", "structure": "imrad"}
                       ).headers["HX-Redirect"].split("/")[2]
    sid = store.load(slug).sections[1].id
    client.post(f"/p/{slug}/s/{sid}/save", data={"text": "O acordo custa cerca de R$ 1 bilhão por ano."})

    first = client.post(f"/p/{slug}/s/{sid}/review", data={}).text
    job_id = re.search(r"/jobs/([0-9a-f]+)", first).group(1)
    # while the review runs: a second review and a polish return the same running job, nothing new starts
    for path in ("review", "polish"):
        assert f"/jobs/{job_id}" in client.post(f"/p/{slug}/s/{sid}/{path}", data={}).text
    page = client.get(f"/p/{slug}/s/{sid}").text
    assert 'class="job running"' in page and 'id="busy-note"' in page
    gate.set()
    wait_job(client, first)
    assert jobs.running(f"{slug}/{sid}") is None

    # the text changes after the review (e.g. a polish): the finding is flagged instead of failing silently
    client.post(f"/p/{slug}/s/{sid}/save", data={"text": "O acordo de 2026 consolida o modelo."})
    page = client.get(f"/p/{slug}/s/{sid}").text
    gid = re.search(r'id="g-(G1-\d+)"', page).group(1)
    assert "Este trecho não está mais no texto" in page and "Já corrigi" in page
    card = client.post(f"/p/{slug}/s/{sid}/decide", data={"number": 1, "group": gid, "action": "edit",
                                                         "replace": "x"}).text
    assert "notice err" in card
    card = client.post(f"/p/{slug}/s/{sid}/decide", data={"number": 1, "group": gid, "action": "resolve"}).text
    assert "resolvido" in card


def test_review_card_buttons_post_to_the_section_url(client, fake, monkeypatch):
    """The cards' buttons must work from the rendered page (they once posted to /p//s//decide → 404)."""
    provider = fake(default={"parecer": "revisar", "findings": [
        {"excerpt": "cerca de um bilhão", "rotulo": "Clareza", "prioridade": "média", "descricao": "Vago."}]})
    monkeypatch.setattr(registry, "build", lambda *a, **k: provider)
    slug = client.post("/projects/new", data={"title": "Botões", "lang": "pt_BR", "structure": "imrad"}
                       ).headers["HX-Redirect"].split("/")[2]
    sid = store.load(slug).sections[1].id
    client.post(f"/p/{slug}/s/{sid}/save", data={"text": "O acordo custa cerca de um bilhão por ano."})
    wait_job(client, client.post(f"/p/{slug}/s/{sid}/review", data={}).text)
    page = client.get(f"/p/{slug}/s/{sid}").text
    posts = set(re.findall(r'hx-post="([^"]*/decide)"', page))
    assert posts == {f"/p/{slug}/s/{sid}/decide"}
    gid = re.search(r'id="g-(G1-\d+)"', page).group(1)
    card = client.post(posts.pop(), data={"number": 1, "group": gid, "action": "edit",
                                         "replace": "cerca de R$ 1 bilhão"})
    assert card.status_code == 200 and "editado" in card.text
    assert "R$ 1 bilhão por ano" in store.load(slug).read_section(sid)
    assert "o revisor não propôs um texto" in page  # the buttons explain themselves


def test_agree_and_fix_with_ai(client, fake, monkeypatch):
    """'Concordo (corrigir com IA)': the AI rewrites the flagged passage, the author sees the diff and accepts,
    and the finding is marked edited."""
    cfg = config.load()
    cfg.provider = "ollama"
    config.save(cfg)
    provider = fake(
        replies={"Corrija o problema apontado pela revisão": {
            "edits": [{"find": "cerca de um bilhão", "replace": "cerca de R$ 1 bilhão (acordos 2026-2028)",
                       "justificativa": "diz a que se refere o valor"}], "notas": "ok"}},
        default={"parecer": "revisar", "findings": [
            {"excerpt": "cerca de um bilhão", "rotulo": "Clareza", "prioridade": "média", "descricao": "Vago.",
             "acao_sugerida": "Diga a que se refere o valor."}]})
    monkeypatch.setattr(registry, "build", lambda *a, **k: provider)
    slug = client.post("/projects/new", data={"title": "IA corrige", "lang": "pt_BR", "structure": "imrad"}
                       ).headers["HX-Redirect"].split("/")[2]
    sid = store.load(slug).sections[1].id
    client.post(f"/p/{slug}/s/{sid}/save", data={"text": "O acordo custa cerca de um bilhão por ano."})
    wait_job(client, client.post(f"/p/{slug}/s/{sid}/review", data={}).text)
    page = client.get(f"/p/{slug}/s/{sid}").text
    assert "Concordo (corrigir com IA)" in page
    url = re.search(r'hx-post="([^"]*/fix)"', page).group(1)
    gid = re.search(r'id="g-(G1-\d+)"', page).group(1)

    nxt = wait_job(client, client.post(url, data={"number": 1, "group": gid}).text)
    assert nxt.startswith("/proposals/")
    prop_page = client.get(nxt).text
    assert f"Correção do apontamento {gid}" in prop_page and "<ins>" in prop_page
    assert "Diga a que se refere o valor" in provider.prompts[-1]  # the finding goes to the model
    client.post(nxt + "/accept")
    assert "R$ 1 bilhão (acordos 2026-2028)" in store.load(slug).read_section(sid)
    assert re.search(rf'id="g-{gid}"[^>]*>.*?editado', client.get(f"/p/{slug}/s/{sid}").text, re.S)


def test_ai_fix_button_hidden_without_ai(client, fake, monkeypatch):
    provider = fake(default={"parecer": "revisar", "findings": [
        {"excerpt": "um bilhão", "rotulo": "Clareza", "prioridade": "média", "descricao": "Vago."}]})
    monkeypatch.setattr(registry, "build", lambda *a, **k: provider)
    slug = client.post("/projects/new", data={"title": "Sem IA", "lang": "pt_BR", "structure": "imrad"}
                       ).headers["HX-Redirect"].split("/")[2]
    sid = store.load(slug).sections[1].id
    client.post(f"/p/{slug}/s/{sid}/save", data={"text": "Custa um bilhão."})
    wait_job(client, client.post(f"/p/{slug}/s/{sid}/review", data={}).text)
    page = client.get(f"/p/{slug}/s/{sid}").text
    assert "Concordo (vou corrigir)" in page and "corrigir com IA" not in page


def test_authors_page_add_save_reorder_blind(client):
    proj = store.create("Com autores", lang="pt_BR", mode="A", structure="empty")
    proj.add_section("Introdução", None, "Texto de Ana Silva.\n")
    base = f"/p/{proj.slug}/authors"
    page = client.get(base)
    assert page.status_code == 200 and 'name="name"' in page.text
    r = client.post(base, data={"name": ["Ana Silva"], "affiliation": ["UFS"], "email": ["ana@ufs.br"],
                                "orcid": ["https://orcid.org/0000-0002-1825-0097"], "action": "add"})
    assert r.text.count('name="name"') == 2  # the blank row just added
    r = client.post(base, data={"name": ["Ana Silva", "Bruno Lima"], "affiliation": ["UFS", "UNAM"],
                                "email": ["ana@ufs.br", ""], "orcid": ["0000-0002-1825-0097", ""],
                                "corresponding": "0", "action": "up:1"})
    saved = store.load(proj.slug).data["authors"]
    assert [a["name"] for a in saved] == ["Bruno Lima", "Ana Silva"] and saved[1]["corresponding"]
    assert saved[1]["orcid"] == "0000-0002-1825-0097"
    r = client.post(base, data={"name": ["Bruno Lima", "Ana Silva"], "affiliation": ["UNAM", "UFS"],
                                "email": ["", "ana@ufs.br"], "orcid": ["", ""], "blind": "1", "action": "save"})
    assert store.load(proj.slug).data["blind"] and "Ana Silva" in r.text and "Introdução" in r.text  # leak shown
    assert "folha de rosto" in client.get(f"/p/{proj.slug}/export").text
    r = client.post(base, data={"name": ["Bruno Lima"], "affiliation": [""], "email": [""], "orcid": ["123"],
                                "action": "orcid:0"})
    assert "ORCID inválido" in r.text
