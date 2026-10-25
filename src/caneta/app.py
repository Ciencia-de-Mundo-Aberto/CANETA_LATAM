"""CANETA web app (FastAPI + Jinja2 + htmx), served only on 127.0.0.1."""

from __future__ import annotations

import secrets
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import (
    __version__,
    checkpoint,
    config,
    discovery,
    export,
    extras,
    i18n,
    ingest,
    jobs,
    journals,
    manuscript_import,
    references,
    semantic,
    writing,
)
from . import project as store
from .llm import registry
from .llm.base import NoLLM
from .review import rounds
from .review.schemas import LABELS, PRIORITIES, ROLES
from .sections import COMPARE_KINDS, ENRICH_KINDS, KINDS, STATES

HERE = Path(__file__).parent
COOKIE = "caneta_session"
PROPOSALS: dict[str, dict] = {}
SEARCHES: dict[str, list[dict]] = {}


def create_app(token: str | None = None) -> FastAPI:
    app = FastAPI(title="CANETA", version=__version__, docs_url=None, redoc_url=None)
    app.state.token = token
    app.mount("/static", StaticFiles(directory=HERE / "web" / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "web" / "templates")

    # ------------------------------------------------------------------ session guard (other sites can't drive the app)
    @app.middleware("http")
    async def guard(request: Request, call_next):
        tok = app.state.token
        path = request.url.path
        if tok and not path.startswith("/static") and path != "/auth":
            if not secrets.compare_digest(request.cookies.get(COOKIE, ""), tok):
                return HTMLResponse("<h1>CANETA</h1><p>Abra a CANETA pelo comando <code>caneta</code> "
                                    "(ele abre o navegador com a sessão correta).</p>", status_code=401)
        return await call_next(request)

    @app.get("/auth")
    def auth(t: str = ""):
        if not app.state.token or not secrets.compare_digest(t, app.state.token):
            return HTMLResponse("sessão inválida", status_code=401)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, t, httponly=True, samesite="strict")
        return resp

    def render(request: Request, name: str, **ctx):
        cfg = config.load()
        ctx.setdefault("cfg", cfg)
        ctx.update(t=i18n.translator(cfg.lang), version=__version__, request=request, KINDS=KINDS, STATES=STATES,
                   PROVIDERS=registry.PROVIDERS, asset_v=_asset_version())
        return templates.TemplateResponse(request, name, ctx)

    def to(url: str) -> Response:
        """Redirect that also works for htmx requests."""
        resp = Response(status_code=204)
        resp.headers["HX-Redirect"] = url
        return resp

    def provider():
        return registry.build()

    def autosave(proj, sid: str, text: str | None) -> None:
        """Action buttons send the editor content: save it first so nothing typed is lost."""
        if text is not None and proj.section(sid).state != "approved" and text != proj.read_section(sid):
            proj.write_section(sid, text, "edicao do autor")

    def job_response(request: Request, job: jobs.Job):
        return render(request, "partials/job.html", job=job)

    def section_key(slug: str, sid: str) -> str:
        return f"{slug}/{sid}"

    # ------------------------------------------------------------------ home / setup
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        cfg = config.load()
        if not cfg.setup_done:
            return RedirectResponse("/setup", status_code=303)
        return render(request, "home.html", projects=store.list_projects())

    @app.get("/sobre", response_class=HTMLResponse)
    def about(request: Request):
        lang = config.load().lang
        return render(request, "about.html", about_html=_markdown_html(f"about_{lang}.md"),
                      manifesto_html=_markdown_html("MANIFESTO.md"))

    # ------------------------------------------------------------------ optional local features
    @app.post("/extras/ocr", response_class=HTMLResponse)
    def extras_ocr(request: Request):
        def work(job):
            job.say(f"Instalando o OCR ({extras.OCR_SIZE}); só é preciso para PDFs digitalizados…")
            extras.install_ocr(job.say)
            job.next_url = "/setup?ok=ocr#recursos"

        return job_response(request, jobs.start("Ativar OCR", work))

    @app.post("/extras/semantic", response_class=HTMLResponse)
    def extras_semantic(request: Request):
        def work(job):
            job.say(f"Ativando a busca por significado ({extras.SEMANTIC_SIZE} no total)…")
            semantic.enable(job.say)
            job.next_url = "/setup?ok=semantic#recursos"

        return job_response(request, jobs.start("Ativar busca por significado", work))

    @app.post("/extras/semantic/off")
    def extras_semantic_off():
        semantic.disable()
        return to("/setup?off=semantic#recursos")

    @app.get("/setup", response_class=HTMLResponse)
    def setup(request: Request):
        return render(request, "setup.html", clis=registry.detect_clis(), ocr_available=extras.ocr_available(),
                      semantic_ready=semantic.ready(), semantic_on=semantic.enabled(),
                      OCR_SIZE=extras.OCR_SIZE, SEMANTIC_SIZE=extras.SEMANTIC_SIZE,
                      has_key={p: bool(config.get_secret(p)) for p, s in registry.PROVIDERS.items() if s["needs_key"]})

    @app.post("/setup/lang")
    def setup_lang(lang: str = Form(...)):
        cfg = config.load()
        cfg.lang = lang if lang in config.LANGS else "pt_BR"
        config.save(cfg)
        return to("/setup")

    @app.post("/setup/test", response_class=HTMLResponse)
    def setup_test(request: Request, provider_id: str = Form(...), model: str = Form(""), api_key: str = Form(""),
                   base_url: str = Form("")):
        key = api_key or config.get_secret(provider_id)
        prov = registry.build(provider_id, model=model or None, base_url=base_url or None, api_key=key)
        result = registry.test_connection(prov)
        return render(request, "partials/test_result.html", result=result)

    @app.post("/setup/models", response_class=HTMLResponse)
    def setup_models(request: Request, provider_id: str = Form(...), api_key: str = Form(""), base_url: str = Form(""),
                     test: str = Form("")):
        key = api_key or config.get_secret(provider_id)
        try:
            models = registry.list_models(provider_id, api_key=key, base_url=base_url or None)
        except Exception as exc:  # noqa: BLE001
            return render(request, "partials/test_result.html", result={"ok": False, "message": str(exc)})
        tested = registry.test_models(provider_id, models[:8], api_key=key) if test else []
        return render(request, "partials/models.html", models=models, tested=tested, provider_id=provider_id)

    @app.post("/setup/save")
    def setup_save(lang: str = Form("pt_BR"), provider_id: str = Form("none"), model: str = Form(""),
                   api_key: str = Form(""), base_url: str = Form(""), languagetool: str = Form("off"),
                   projects_dir: str = Form("")):
        cfg = config.load()
        cfg.lang = lang if lang in config.LANGS else "pt_BR"
        cfg.provider = provider_id if provider_id in registry.PROVIDERS else "none"
        cfg.model = model or registry.PROVIDERS[cfg.provider]["default_model"]
        cfg.base_url = base_url
        cfg.languagetool = languagetool if languagetool in ("off", "public", "local") else "off"
        if projects_dir.strip():
            cfg.projects_dir = projects_dir.strip()
        cfg.setup_done = True
        if api_key.strip():
            config.set_secret(cfg.provider, api_key.strip())
        config.save(cfg)
        return to("/")

    # ------------------------------------------------------------------ projects
    @app.post("/projects/new")
    def project_new(title: str = Form(...), lang: str = Form("pt_BR"), structure: str = Form("imrad")):
        proj = store.create(title, lang=lang, mode="B", structure=structure)
        return to(f"/p/{proj.slug}")

    @app.post("/projects/import")
    async def project_import(file: UploadFile = File(...), lang: str = Form("pt_BR")):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / Path(file.filename or "artigo.docx").name
            path.write_bytes(await file.read())
            try:
                proj = manuscript_import.import_article(path, lang=lang)
            except (manuscript_import.ImportError_, ingest.IngestError) as exc:
                return HTMLResponse(f"<p class='error'>{exc}</p>", status_code=400)
        return to(f"/p/{proj.slug}")

    @app.post("/projects/checkpoint")
    async def project_checkpoint(file: UploadFile = File(...)):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "in.caneta"
            path.write_bytes(await file.read())
            proj = checkpoint.import_(path)
        return to(f"/p/{proj.slug}")

    @app.get("/p/{slug}")
    def project_open(slug: str):
        proj = store.load(slug)
        sec = proj.next_open_section() or (proj.sections[0] if proj.sections else None)
        return RedirectResponse(f"/p/{slug}/s/{sec.id}" if sec else f"/p/{slug}/references", status_code=303)

    # ------------------------------------------------------------------ section workspace
    @app.get("/p/{slug}/s/{sid}", response_class=HTMLResponse)
    def workspace(request: Request, slug: str, sid: str):
        proj = store.load(slug)
        sec = proj.section(sid)
        text = proj.read_section(sid)
        latest = rounds.latest(proj, sid)
        if latest:
            rounds.mark_stale(text, latest["groups"])
        refs = proj.references()
        return render(request, "workspace.html", proj=proj, sec=sec, text=text, latest=latest,
                      running=jobs.running(section_key(slug, sid)),
                      history=proj.history(sid)[:30], refs=refs, cited=set(references.cited_keys(proj.read_section(sid))),
                      roles=ROLES, enrich=sec.kind in ENRICH_KINDS, compare=sec.kind in COMPARE_KINDS,
                      fulltext_keys=[r["id"] for r in refs if (r.get("caneta") or {}).get("fulltext") in
                                     ("pdf-layout", "pdf-plain-text", "pdf-ocr")],
                      next_sec=proj.next_open_section(), ref_label=references.label, LABELS=LABELS,
                      PRIORITIES=PRIORITIES)

    @app.post("/p/{slug}/s/{sid}/save", response_class=HTMLResponse)
    def section_save(request: Request, slug: str, sid: str, text: str = Form("")):
        proj = store.load(slug)
        try:
            proj.write_section(sid, text, "edicao do autor")
        except Exception as exc:  # noqa: BLE001
            return HTMLResponse(f"<span class='err'>{exc}</span>")
        t = i18n.translator(config.load().lang)
        return HTMLResponse(f"<span class='ok'>{t('saved')}</span>")

    @app.post("/p/{slug}/sections/add")
    def section_add(slug: str, title: str = Form(...), kind: str = Form("")):
        proj = store.load(slug)
        sec = proj.add_section(title, kind or None)
        return to(f"/p/{slug}/s/{sec.id}")

    @app.post("/p/{slug}/s/{sid}/meta")
    def section_meta(slug: str, sid: str, title: str = Form(""), kind: str = Form("")):
        store.load(slug).update_section_meta(sid, title, kind)
        return to(f"/p/{slug}/s/{sid}")

    @app.post("/p/{slug}/s/{sid}/move")
    def section_move(slug: str, sid: str, delta: int = Form(...)):
        store.load(slug).move_section(sid, delta)
        return to(f"/p/{slug}/s/{sid}")

    @app.post("/p/{slug}/s/{sid}/delete")
    def section_delete(slug: str, sid: str):
        store.load(slug).delete_section(sid)
        return to(f"/p/{slug}")

    @app.post("/p/{slug}/s/{sid}/approve", response_class=HTMLResponse)
    def section_approve(request: Request, slug: str, sid: str, text: str = Form(None)):
        proj = store.load(slug)
        autosave(proj, sid, text)
        rounds.approve(proj, sid)
        nxt = proj.next_open_section()
        return to(f"/p/{slug}/s/{nxt.id}" if nxt else f"/p/{slug}/journal")

    @app.post("/p/{slug}/s/{sid}/reopen")
    def section_reopen(slug: str, sid: str):
        proj = store.load(slug)
        proj.snapshot(sid, "reaberta")
        proj.set_state(sid, "revising")
        return to(f"/p/{slug}/s/{sid}")

    @app.post("/p/{slug}/s/{sid}/restore")
    def section_restore(slug: str, sid: str, file: str = Form(...)):
        store.load(slug).restore(sid, file)
        return to(f"/p/{slug}/s/{sid}")

    @app.get("/p/{slug}/s/{sid}/history/{file}", response_class=HTMLResponse)
    def section_history_view(request: Request, slug: str, sid: str, file: str):
        proj = store.load(slug)
        old = (proj.path / "history" / sid / Path(file).name).read_text(encoding="utf-8")
        return render(request, "partials/history_view.html", diff=writing.diff_html(old, proj.read_section(sid)),
                      file=Path(file).name, slug=slug, sid=sid)

    # ------------------------------------------------------------------ review
    @app.post("/p/{slug}/s/{sid}/review", response_class=HTMLResponse)
    def section_review(request: Request, slug: str, sid: str, adversary: str = Form(""), text: str = Form(None)):
        busy = jobs.running(section_key(slug, sid))
        if busy:  # one task per section: a second click shows the running one
            return job_response(request, busy)
        proj = store.load(slug)
        autosave(proj, sid, text)

        def work(job):
            data = rounds.run_round(proj, sid, provider(), include_adversary=bool(adversary), on_log=job.say)
            # the query string forces a real reload (a hash-only change would just scroll)
            job.next_url = f"/p/{slug}/s/{sid}?rodada={data['round']}#revisao"

        return job_response(request, jobs.start("Revisão da seção", work, key=section_key(slug, sid)))

    @app.post("/p/{slug}/s/{sid}/decide", response_class=HTMLResponse)
    def section_decide(request: Request, slug: str, sid: str, number: int = Form(...), group: str = Form(...),
                       action: str = Form(...), replace: str = Form(None), reason: str = Form(None)):
        proj = store.load(slug)
        error = None
        try:
            g = rounds.decide(proj, sid, number, group, action, replace, reason)
        except rounds.ReviewError as exc:
            error = str(exc)
            g = next(x for x in rounds.load_round(proj, sid, number)["groups"] if x["id"] == group)
        rounds.mark_stale(proj.read_section(sid), [g])
        changed_text = action in ("accept", "edit") and not error and bool(g.get("edit") or action == "edit")
        resp = render(request, "partials/group_card.html", g=g, number=number, slug=slug, sid=sid, error=error,
                      roles=ROLES, refresh=changed_text)
        if changed_text:
            resp.headers["HX-Refresh"] = "true"  # the editor must show the corrected text
        return resp

    # ------------------------------------------------------------------ AI writing proposals
    def _proposal_job(request, slug, sid, title, fn, *args, text: str | None = None):
        busy = jobs.running(section_key(slug, sid))
        if busy:
            return job_response(request, busy)
        proj = store.load(slug)
        autosave(proj, sid, text)

        def work(job):
            prop = fn(proj, sid, provider(), *args, on_log=job.say)
            pid = secrets.token_hex(6)
            PROPOSALS[pid] = {"slug": slug, **prop.as_dict()}
            job.next_url = f"/proposals/{pid}"

        return job_response(request, jobs.start(title, work, key=section_key(slug, sid)))

    @app.post("/p/{slug}/s/{sid}/polish", response_class=HTMLResponse)
    def section_polish(request: Request, slug: str, sid: str, extra: str = Form(""), text: str = Form(None)):
        return _proposal_job(request, slug, sid, "Formatar e polir", writing.polish, extra, text=text)

    @app.post("/p/{slug}/s/{sid}/edit", response_class=HTMLResponse)
    def section_edit(request: Request, slug: str, sid: str, instruction: str = Form(...), selection: str = Form(""),
                     text: str = Form(None)):
        return _proposal_job(request, slug, sid, "Edição pontual", writing.interactive_edit, instruction, selection,
                             text=text)

    @app.post("/p/{slug}/s/{sid}/enrich", response_class=HTMLResponse)
    def section_enrich(request: Request, slug: str, sid: str, keys: list[str] = Form([]), text: str = Form(None)):
        return _proposal_job(request, slug, sid, "Citações na introdução", writing.enrich_intro, keys, text=text)

    @app.post("/p/{slug}/s/{sid}/compare", response_class=HTMLResponse)
    def section_compare(request: Request, slug: str, sid: str, keys: list[str] = Form([]), text: str = Form(None)):
        return _proposal_job(request, slug, sid, "Comparação com a literatura", writing.compare_results, keys,
                             text=text)

    @app.get("/proposals/{pid}", response_class=HTMLResponse)
    def proposal_view(request: Request, pid: str):
        prop = PROPOSALS.get(pid)
        if not prop:
            return RedirectResponse("/", status_code=303)
        proj = store.load(prop["slug"])
        return render(request, "proposal.html", pid=pid, prop=prop, proj=proj, sec=proj.section(prop["section_id"]),
                      diff=writing.diff_html(prop["original"], prop["proposed"]))

    @app.post("/proposals/{pid}/accept")
    def proposal_accept(pid: str, text: str = Form(None)):
        prop = PROPOSALS.pop(pid, None)
        if not prop:
            return to("/")
        proj = store.load(prop["slug"])
        sid = prop["section_id"]
        proj.write_section(sid, text if text is not None else prop["proposed"], f"ia {prop['kind']} aceita")
        new_state = {"polish": "polished", "enrich": "enriched", "compare": "enriched"}.get(prop["kind"])
        if new_state and proj.section(sid).state in ("draft", "polished"):
            proj.set_state(sid, new_state)
        return to(f"/p/{prop['slug']}/s/{sid}")

    @app.post("/proposals/{pid}/discard")
    def proposal_discard(pid: str):
        prop = PROPOSALS.pop(pid, None)
        return to(f"/p/{prop['slug']}/s/{prop['section_id']}" if prop else "/")

    # ------------------------------------------------------------------ references, material, MCP
    @app.get("/p/{slug}/references", response_class=HTMLResponse)
    def references_page(request: Request, slug: str, q: str = ""):
        proj = store.load(slug)
        intro = next((proj.read_section(s.id) for s in proj.sections if s.kind == "intro"), "")
        return render(request, "references.html", proj=proj, refs=proj.references(), ref_label=references.label,
                      ocr_available=extras.ocr_available(), semantic_on=semantic.enabled(),
                      material=ingest.list_material(proj), q=q or proj.data.get("last_query", ""),
                      suggested=_suggest_query(proj, intro))

    @app.post("/p/{slug}/search", response_class=HTMLResponse)
    def references_search(request: Request, slug: str, query: str = Form(...), year_from: str = Form(""),
                          sources: list[str] = Form(["scielo", "doaj"])):
        proj = store.load(slug)
        proj.data["last_query"] = query
        proj.save()

        def work(job):
            job.say("Buscando em SciELO e DOAJ (somente Diamond Open Access)…")
            res = discovery.search(proj, query, sources=sources or None,
                                   year_from=int(year_from) if year_from.isdigit() else None)
            sid = secrets.token_hex(6)
            SEARCHES[sid] = res.get("papers", [])
            job.say(f"{res.get('returned', 0)} artigo(s) encontrados; erros: {res.get('errors') or 'nenhum'}")
            job.next_url = f"/p/{slug}/search/{sid}"

        return job_response(request, jobs.start("Busca de literatura aberta", work))

    @app.get("/p/{slug}/search/{sid}", response_class=HTMLResponse)
    def references_results(request: Request, slug: str, sid: str):
        proj = store.load(slug)
        return render(request, "search_results.html", proj=proj, papers=SEARCHES.get(sid, []), search_id=sid)

    @app.post("/p/{slug}/references/add")
    def references_add(slug: str, search_id: str = Form(...), picks: list[int] = Form([])):
        proj = store.load(slug)
        papers = SEARCHES.get(search_id, [])
        discovery.add_papers(proj, [papers[i] for i in picks if 0 <= i < len(papers)])
        return to(f"/p/{slug}/references")

    @app.post("/p/{slug}/references/fulltext", response_class=HTMLResponse)
    def references_fulltext(request: Request, slug: str, keys: list[str] = Form([]), ocr: str = Form("")):
        proj = store.load(slug)
        use_ocr = bool(ocr) and extras.ocr_available()

        def work(job):
            job.say(f"Baixando e convertendo {len(keys)} artigo(s) (≈5–20 s cada)…"
                    + (" Com OCR para PDFs digitalizados (≈10–15 s por página)." if use_ocr else ""))
            res = discovery.fetch_fulltext(proj, keys, ocr=use_ocr)
            job.say(f"Texto completo obtido: {res.get('with_fulltext', 0)}; sem PDF: {res.get('no_pdf_found', 0)}")
            job.next_url = f"/p/{slug}/references"

        return job_response(request, jobs.start("Texto completo", work))

    @app.post("/p/{slug}/references/delete")
    def references_delete(slug: str, key: str = Form(...)):
        proj = store.load(slug)
        proj.save_references([r for r in proj.references() if r["id"] != key])
        return to(f"/p/{slug}/references")

    @app.post("/p/{slug}/material")
    async def material_upload(slug: str, files: list[UploadFile] = File(...)):
        proj = store.load(slug)
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for f in files:
                p = Path(tmp) / Path(f.filename or "arquivo").name
                p.write_bytes(await f.read())
                paths.append(p)
            ingest.add_material(proj, paths)
        return to(f"/p/{slug}/references")

    # ------------------------------------------------------------------ journal
    @app.get("/p/{slug}/journal", response_class=HTMLResponse)
    def journal_page(request: Request, slug: str):
        proj = store.load(slug)
        text = proj.title + "\n" + "\n".join(proj.read_section(s.id) for s in proj.sections
                                             if s.kind in ("abstract", "intro", "conclusion"))
        return render(request, "journal.html", proj=proj, ranked=journals.match(text, k=5),
                      all_journals=sorted(journals.load_index(), key=lambda r: r["journal"]))

    @app.post("/p/{slug}/journal", response_class=HTMLResponse)
    def journal_choose(request: Request, slug: str, journal_id: str = Form(...)):
        proj = store.load(slug)
        row = journals.get(journal_id)
        if not row:
            return to(f"/p/{slug}/journal")
        proj.data["journal"] = journals.profile(row)
        proj.save()
        proj.log("journal_chosen", journal=row["journal"])

        def work(job):
            job.say("Baixando o template do site da revista…")
            files = journals.fetch_template(journal_id, proj.path / "templates")
            job.say(f"{len(files)} arquivo(s) de template salvos." if files else
                    "Não foi possível baixar o template; a exportação usa o estilo padrão.")
            job.next_url = f"/p/{slug}/export"

        return job_response(request, jobs.start("Template da revista", work))

    # ------------------------------------------------------------------ export / checkpoint / log
    @app.get("/p/{slug}/export", response_class=HTMLResponse)
    def export_page(request: Request, slug: str):
        proj = store.load(slug)
        return render(request, "export.html", proj=proj, has_tex=bool(shutil.which("tectonic") or shutil.which("xelatex")),
                      templates=[p.name for p in (proj.path / "templates").glob("*")] if (proj.path / "templates").is_dir()
                      else [])

    @app.post("/p/{slug}/csl")
    def export_csl(slug: str, csl: str = Form("apa")):
        proj = store.load(slug)
        proj.data["csl"] = csl if csl in export.STYLES else "apa"
        proj.save()
        return to(f"/p/{slug}/export")

    @app.get("/p/{slug}/export/{fmt}")
    def export_file(slug: str, fmt: str, approved: int = 0):
        proj = store.load(slug)
        try:
            path = export.export(proj, fmt, only_approved=bool(approved))
        except export.ExportError as exc:
            return HTMLResponse(f"<p>{exc}</p>", status_code=400)
        return FileResponse(path, filename=path.name)

    @app.get("/p/{slug}/checkpoint")
    def checkpoint_file(slug: str, material: int = 1, pdfs: int = 0):
        proj = store.load(slug)
        out = proj.path.parent / ".checkpoints"
        out.mkdir(exist_ok=True)
        path = checkpoint.export(proj, out / f"{slug}.caneta", bool(material), bool(pdfs))
        return FileResponse(path, filename=path.name, media_type="application/zip")

    @app.get("/p/{slug}/log", response_class=HTMLResponse)
    def log_page(request: Request, slug: str):
        proj = store.load(slug)
        return render(request, "log.html", proj=proj, events=list(reversed(proj.events(500))))

    # ------------------------------------------------------------------ jobs
    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_status(request: Request, job_id: str):
        job = jobs.get(job_id)
        if job is None:
            return HTMLResponse("")
        if job.status == "done" and job.next_url:
            return to(job.next_url)
        return job_response(request, job)

    @app.exception_handler(NoLLM)
    def no_llm(request: Request, exc: NoLLM):
        return HTMLResponse(f"<div class='notice'>{exc}</div>", status_code=200)

    @app.exception_handler(store.ProjectError)
    def project_error(request: Request, exc: store.ProjectError):
        return HTMLResponse(f"<div class='notice err'>{exc}</div>", status_code=404)

    return app


def _markdown_html(name: str) -> str:
    """Render a bundled Markdown text (About page, MANIFESTO) with the embedded pandoc."""
    candidates = [HERE / "content" / name, HERE.parents[1] / name]  # wheel, then the source checkout
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        return ""
    return _render_md(str(path), path.stat().st_mtime)


@lru_cache(maxsize=16)
def _render_md(path: str, _mtime: float) -> str:
    import pypandoc

    return pypandoc.convert_file(path, "html", format="markdown", extra_args=["--wrap=none"])


def _asset_version() -> str:
    """Cache-busting token for caneta.css/js (changes whenever the files change)."""
    static = HERE / "web" / "static"
    return str(int(max((static / f).stat().st_mtime for f in ("caneta.css", "caneta.js"))))


def _suggest_query(proj: store.Project, intro: str) -> str:
    """A starting query from the title (the author edits it; use OR between synonyms)."""
    from .journals import tokens

    words = []
    for w in tokens(proj.title + " " + intro[:400]):
        if w not in words:
            words.append(w)
    return " ".join(words[:5])

