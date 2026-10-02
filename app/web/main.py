from __future__ import annotations

import os
import threading
from datetime import date, datetime
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from app.art.render import render_set
from app.brand_assets import AssetError, BrandAssets
from app.collector import run_default
from app.editorial import MIN_GAP_DAYS, build_agenda, conflicts, suggest_date, taken_dates, today_br
from app.drafts import DraftContent, build_full_caption, run_draft_job, start_draft
from app.config import Settings, load_settings
from app.db import init_db, make_engine, make_session_factory
from app.models import (
    DRAFT_APPROVED, DRAFT_ERROR, DRAFT_GENERATING, DRAFT_PUBLISHED, DRAFT_READY, DRAFT_SCHEDULED, STATUSES, CollectRun, Draft, Item, User, utcnow,
)
from app.relevance import THEMES
from app.security import new_csrf_token, verify_password

BASE_DIR = Path(__file__).parent
PAGE_SIZE = 20
DEFAULT_MIN_RELEVANCE = 40

SOURCE_LABELS = {"doe_rs": "Diário Oficial RS", "consema": "CONSEMA", "fepam": "FEPAM"}
STATUS_LABELS = {"new": "Novas", "post": "Quero postar", "later": "Depois", "ignored": "Ignoradas"}
TAB_LABELS = {"new": "Novas", "post": "Postar", "later": "Depois", "ignored": "Ignoradas"}


def create_app(settings: Settings | None = None, session_factory=None) -> FastAPI:
    settings = settings or load_settings()
    if session_factory is None:
        engine = make_engine(settings.database_url)
        init_db(engine)
        session_factory = make_session_factory(engine)

    app = FastAPI(title="HRBio · Radar de Normas", docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        max_age=60 * 60 * 24 * 30,
        same_site="lax",
        https_only=os.getenv("COOKIE_SECURE", "0") == "1",  # use COOKIE_SECURE=1 em producao (HTTPS)
    )
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    templates.env.globals.update(
        SOURCE_LABELS=SOURCE_LABELS, STATUS_LABELS=STATUS_LABELS, TAB_LABELS=TAB_LABELS, THEMES=list(THEMES)
    )
    templates.env.filters["brdate"] = lambda d: d.strftime("%d/%m/%Y") if d else "—"

    collect_lock = threading.Lock()
    assets = BrandAssets(settings.data_dir)

    def get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    def current_user(request: Request, db=Depends(get_db)) -> User:
        uid = request.session.get("uid")
        user = db.get(User, uid) if uid else None
        if not user:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        return user

    def csrf_token(request: Request) -> str:
        if "csrf" not in request.session:
            request.session["csrf"] = new_csrf_token()
        return request.session["csrf"]

    def check_csrf(request: Request, token: str) -> None:
        if not token or token != request.session.get("csrf"):
            raise HTTPException(status_code=403, detail="Sessão expirada. Recarregue a página.")

    def render(request: Request, name: str, **ctx):
        ctx.update(csrf=csrf_token(request), user=ctx.get("user"))
        ctx.setdefault("flash", request.session.pop("flash", None))
        return templates.TemplateResponse(request, name, ctx)

    # ------------------------------------------------------------------ login
    @app.get("/login")
    def login_form(request: Request):
        return render(request, "login.html", error=None)

    @app.post("/login")
    def login(request: Request, username: str = Form(...), password: str = Form(...), db=Depends(get_db)):
        user = db.scalar(select(User).where(User.username == username.strip()))
        if not user or not verify_password(password, user.password_hash):
            return render(request, "login.html", error="Usuário ou senha incorretos.")
        request.session.clear()
        request.session["uid"] = user.id
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")):
        check_csrf(request, csrf)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ------------------------------------------------------------------ lista
    @app.get("/")
    def index(
        request: Request,
        status: str = "new",
        source: str = "",
        theme: str = "",
        min_rel: int = DEFAULT_MIN_RELEVANCE,
        page: int = 1,
        user: User = Depends(current_user),
        db=Depends(get_db),
    ):
        if status not in STATUSES:
            status = "new"
        page = max(1, page)
        q = select(Item).where(Item.status == status, Item.relevance >= min_rel)
        if source:
            q = q.where(Item.source == source)
        if theme:
            q = q.where(Item.themes.contains(theme))
        total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
        items = db.scalars(
            q.order_by(
                func.coalesce(Item.published_at, func.date(Item.first_seen_at)).desc(),
                Item.relevance.desc(),
                Item.id.desc(),
            )
            .limit(PAGE_SIZE)
            .offset((page - 1) * PAGE_SIZE)
        ).all()
        counts = dict(
            db.execute(
                select(Item.status, func.count()).where(Item.relevance >= min_rel).group_by(Item.status)
            ).all()
        )
        last_runs = db.scalars(select(CollectRun).order_by(CollectRun.id.desc()).limit(6)).all()
        last_run = {r.source: r for r in reversed(last_runs)}
        return render(
            request, "index.html",
            user=user, items=items, total=total, status=status, source=source, theme=theme,
            min_rel=min_rel, page=page, pages=max(1, -(-total // PAGE_SIZE)), counts=counts,
            last_run=last_run,
            drafts={d.item_id: d for d in db.scalars(select(Draft).where(Draft.item_id.in_([i.id for i in items])))},
        )

    @app.get("/items/{item_id}")
    def detail(request: Request, item_id: int, user: User = Depends(current_user), db=Depends(get_db)):
        item = db.get(Item, item_id)
        if not item:
            raise HTTPException(404, "Item não encontrado")
        draft = db.scalar(select(Draft).where(Draft.item_id == item_id))
        return render(request, "detail.html", user=user, item=item, draft=draft)

    @app.post("/items/{item_id}/status")
    def set_status(
        request: Request,
        background: BackgroundTasks,
        item_id: int,
        new_status: str = Form(...),
        next_url: str = Form("/"),
        csrf: str = Form(""),
        user: User = Depends(current_user),
        db=Depends(get_db),
    ):
        check_csrf(request, csrf)
        item = db.get(Item, item_id)
        if not item or new_status not in STATUSES:
            raise HTTPException(400, "Pedido inválido")
        item.status = new_status
        item.status_changed_at = utcnow()
        db.commit()
        if new_status == "post" and settings.anthropic_api_key and not db.scalar(
            select(Draft.id).where(Draft.item_id == item_id)
        ):
            if start_draft(db, item):
                background.add_task(run_draft_job, session_factory, settings, item_id)
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/"  # evita redirecionamento para outro site
        return RedirectResponse(next_url, status_code=303)

    # --------------------------------------------------------------- rascunhos
    def load_draft_page(db, item_id: int):
        item = db.get(Item, item_id)
        if not item:
            raise HTTPException(404, "Item não encontrado")
        draft = db.scalar(select(Draft).where(Draft.item_id == item_id))
        return item, draft

    @app.get("/items/{item_id}/draft")
    def draft_page(request: Request, item_id: int, user: User = Depends(current_user), db=Depends(get_db)):
        item, draft = load_draft_page(db, item_id)
        content = None
        if draft and draft.content:
            content = DraftContent.model_validate_json(draft.content)
        return render(
            request, "draft.html", user=user, item=item, draft=draft, content=content,
            full_caption=build_full_caption(item, content) if content else "",
            has_key=bool(settings.anthropic_api_key),
            DRAFT_GENERATING=DRAFT_GENERATING, DRAFT_READY=DRAFT_READY,
            DRAFT_APPROVED=DRAFT_APPROVED, DRAFT_ERROR=DRAFT_ERROR,
            DRAFT_SCHEDULED=DRAFT_SCHEDULED, DRAFT_PUBLISHED=DRAFT_PUBLISHED,
            suggested=suggest_date(taken_dates(db, exclude_item_id=item_id), today_br()),
            today=today_br(),
            art_files=assets.list_art(item_id), photos=assets.list_photos(),
            has_logo=assets.logo_path.is_file(),
        )

    @app.post("/items/{item_id}/draft/generate")
    def draft_generate(
        request: Request,
        background: BackgroundTasks,
        item_id: int,
        csrf: str = Form(""),
        user: User = Depends(current_user),
        db=Depends(get_db),
    ):
        check_csrf(request, csrf)
        item, current = load_draft_page(db, item_id)
        if current and current.status == DRAFT_PUBLISHED:
            request.session["flash"] = "Este post já foi publicado; não dá para gerar de novo."
        elif not settings.anthropic_api_key:
            request.session["flash"] = "Falta configurar ANTHROPIC_API_KEY no servidor para gerar rascunhos."
        elif start_draft(db, item):
            background.add_task(run_draft_job, session_factory, settings, item_id)
        else:
            request.session["flash"] = "Já existe uma geração em andamento para este item."
        return RedirectResponse(f"/items/{item_id}/draft", status_code=303)

    @app.post("/items/{item_id}/draft/save")
    async def draft_save(
        request: Request, item_id: int, user: User = Depends(current_user), db=Depends(get_db)
    ):
        form = await request.form()
        check_csrf(request, str(form.get("csrf", "")))
        _, draft = load_draft_page(db, item_id)
        if not draft or not draft.content:
            raise HTTPException(400, "Não há rascunho para salvar")
        content = DraftContent.model_validate_json(draft.content)
        content.headline = str(form.get("headline", content.headline)).strip()
        content.caption = str(form.get("caption", content.caption)).strip()
        content.whatsapp_status = str(form.get("whatsapp_status", content.whatsapp_status)).strip()
        content.hashtags = [t.strip("# ") for t in str(form.get("hashtags", "")).replace(",", " ").split() if t.strip("# ")]
        for i, slide in enumerate(content.carousel):
            slide.title = str(form.get(f"slide_title_{i}", slide.title)).strip()
            slide.body = str(form.get(f"slide_body_{i}", slide.body)).strip()
        content.reel.hook = str(form.get("reel_hook", content.reel.hook)).strip()
        content.reel.cta = str(form.get("reel_cta", content.reel.cta)).strip()
        for i, scene in enumerate(content.reel.scenes):
            scene.narration = str(form.get(f"scene_narration_{i}", scene.narration)).strip()
            scene.on_screen_text = str(form.get(f"scene_text_{i}", scene.on_screen_text)).strip()
        draft.content = content.model_dump_json()
        flash = "Alterações salvas."
        if draft.status in (DRAFT_APPROVED, DRAFT_SCHEDULED):  # editou depois de aprovar: aprovar e agendar de novo
            draft.status, draft.approved_at, draft.scheduled_for = DRAFT_READY, None, None
            flash += " A aprovação e o agendamento foram cancelados: aprove de novo."
        assets.clear_art(item_id)  # as artes antigas nao refletem mais o texto editado
        draft.updated_at = utcnow()
        db.commit()
        request.session["flash"] = flash
        return RedirectResponse(f"/items/{item_id}/draft", status_code=303)

    @app.post("/items/{item_id}/draft/approve")
    def draft_approve(
        request: Request,
        item_id: int,
        approve: str = Form("1"),
        csrf: str = Form(""),
        user: User = Depends(current_user),
        db=Depends(get_db),
    ):
        check_csrf(request, csrf)
        _, draft = load_draft_page(db, item_id)
        if not draft or draft.status not in (DRAFT_READY, DRAFT_APPROVED, DRAFT_SCHEDULED):
            raise HTTPException(400, "Só dá para aprovar um rascunho pronto")
        if approve == "1":
            if draft.status == DRAFT_READY:
                draft.status, draft.approved_at = DRAFT_APPROVED, utcnow()
        else:
            draft.status, draft.approved_at, draft.scheduled_for = DRAFT_READY, None, None
        db.commit()
        return RedirectResponse(f"/items/{item_id}/draft", status_code=303)

    # -------------------------------------------------------------------- artes
    @app.post("/items/{item_id}/draft/art")
    def draft_art(
        request: Request,
        item_id: int,
        photo: str = Form("auto"),
        csrf: str = Form(""),
        user: User = Depends(current_user),
        db=Depends(get_db),
    ):
        check_csrf(request, csrf)
        item, draft = load_draft_page(db, item_id)
        if not draft or not draft.content:
            raise HTTPException(400, "Gere o rascunho antes das artes")
        content = DraftContent.model_validate_json(draft.content)
        chosen = assets.pick_photo(item_id) if photo == "auto" else (photo if assets.photo_path(photo) else None)
        draft.photo = None if photo == "auto" else chosen
        images = render_set(
            slides=[(sl.title, sl.body) for sl in content.carousel],
            headline=content.headline,
            status_text=content.whatsapp_status,
            norm_label=item.title,
            photo=assets.load_photo(chosen),
            logo=assets.load_logo(),
            seed=item_id,
        )
        assets.save_art(item_id, images)
        db.commit()
        request.session["flash"] = f"{len(images)} artes geradas."
        return RedirectResponse(f"/items/{item_id}/draft#artes", status_code=303)

    @app.get("/items/{item_id}/art/{name}")
    def art_file(item_id: int, name: str, user: User = Depends(current_user)):
        path = assets.art_path(item_id, name)
        if not path:
            raise HTTPException(404, "Arte não encontrada")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/items/{item_id}/art.zip")
    def art_zip(item_id: int, user: User = Depends(current_user)):
        if not assets.list_art(item_id):
            raise HTTPException(404, "Gere as artes primeiro")
        return Response(
            assets.art_zip(item_id), media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="artes-{item_id}.zip"'},
        )

    # --------------------------------------------------------------------- marca
    @app.get("/marca")
    def brand_page(request: Request, user: User = Depends(current_user)):
        return render(request, "marca.html", user=user, photos=assets.list_photos(), has_logo=assets.logo_path.is_file())

    @app.post("/marca/logo")
    async def brand_logo(request: Request, user: User = Depends(current_user)):
        form = await request.form()
        check_csrf(request, str(form.get("csrf", "")))
        upload = form.get("logo")
        try:
            if not hasattr(upload, "read"):
                raise AssetError("Escolha um arquivo de logo.")
            assets.save_logo(await upload.read())
            request.session["flash"] = "Logo atualizado."
        except AssetError as exc:
            request.session["flash"] = str(exc)
        return RedirectResponse("/marca", status_code=303)

    @app.post("/marca/fotos")
    async def brand_photos(request: Request, user: User = Depends(current_user)):
        form = await request.form()
        check_csrf(request, str(form.get("csrf", "")))
        ok, errors = 0, []
        for upload in form.getlist("fotos"):
            if not hasattr(upload, "read") or not getattr(upload, "filename", ""):
                continue
            try:
                assets.save_photo(await upload.read())
                ok += 1
            except AssetError as exc:
                errors.append(f"{upload.filename}: {exc}")
        request.session["flash"] = " ".join([f"{ok} foto(s) enviada(s)."] + errors)
        return RedirectResponse("/marca", status_code=303)

    @app.post("/marca/fotos/{name}/excluir")
    def brand_photo_delete(request: Request, name: str, csrf: str = Form(""), user: User = Depends(current_user)):
        check_csrf(request, csrf)
        assets.delete_photo(name)
        request.session["flash"] = "Foto removida."
        return RedirectResponse("/marca", status_code=303)

    @app.get("/marca/fotos/{name}")
    def brand_photo_file(name: str, thumb: int = 0, user: User = Depends(current_user)):
        path = assets.photo_path(name, thumb=bool(thumb))
        if not path:
            raise HTTPException(404, "Foto não encontrada")
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/marca/logo")
    def brand_logo_file(user: User = Depends(current_user)):
        if not assets.logo_path.is_file():
            raise HTTPException(404, "Sem logo")
        return FileResponse(assets.logo_path, media_type="image/png")

    # --------------------------------------------------------------- calendario
    @app.get("/calendario")
    def calendar_page(request: Request, user: User = Depends(current_user), db=Depends(get_db)):
        today = today_br()
        taken = taken_dates(db)
        queue = db.execute(
            select(Draft, Item).join(Item, Item.id == Draft.item_id)
            .where(Draft.status == DRAFT_APPROVED).order_by(Draft.approved_at)
        ).all()
        published = db.execute(
            select(Draft, Item).join(Item, Item.id == Draft.item_id)
            .where(Draft.status == DRAFT_PUBLISHED).order_by(Draft.published_at.desc()).limit(10)
        ).all()
        overdue = db.execute(
            select(Draft, Item).join(Item, Item.id == Draft.item_id)
            .where(Draft.status == DRAFT_SCHEDULED, Draft.scheduled_for < today).order_by(Draft.scheduled_for)
        ).all()
        # cada item da fila recebe uma data sugerida diferente (a anterior passa a contar como ocupada)
        suggestions, simulated = {}, set(taken)
        for draft, _ in queue:
            day = suggest_date(simulated, today)
            suggestions[draft.item_id] = day
            simulated.add(day)
        return render(
            request, "calendario.html", user=user, today=today, agenda=build_agenda(db, today),
            queue=queue, published=published, overdue=overdue, suggestions=suggestions,
        )

    def _parse_day(value: str) -> date | None:
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None

    @app.post("/items/{item_id}/draft/schedule")
    def draft_schedule(
        request: Request,
        item_id: int,
        day: str = Form(""),
        next_url: str = Form(""),
        csrf: str = Form(""),
        user: User = Depends(current_user),
        db=Depends(get_db),
    ):
        check_csrf(request, csrf)
        _, draft = load_draft_page(db, item_id)
        if not draft or draft.status not in (DRAFT_APPROVED, DRAFT_SCHEDULED):
            raise HTTPException(400, "Aprove o rascunho antes de agendar")
        today = today_br()
        others = taken_dates(db, exclude_item_id=item_id)
        chosen = _parse_day(day) if day.strip() else suggest_date(others, today)
        if chosen is None:
            request.session["flash"] = "Data inválida."
        elif chosen < today:
            request.session["flash"] = "Essa data já passou. Escolha hoje ou uma data futura."
        else:
            draft.status, draft.scheduled_for = DRAFT_SCHEDULED, chosen
            db.commit()
            msg = f"Agendado para {chosen.strftime('%d/%m/%Y')}."
            if conflicts(chosen, others):
                msg += f" Atenção: fica a menos de {MIN_GAP_DAYS} dias de outro post (fora do ritmo dia sim, dia não)."
            request.session["flash"] = msg
        target = next_url if next_url.startswith("/") and not next_url.startswith("//") else f"/items/{item_id}/draft"
        return RedirectResponse(target, status_code=303)

    @app.post("/items/{item_id}/draft/unschedule")
    def draft_unschedule(
        request: Request,
        item_id: int,
        next_url: str = Form(""),
        csrf: str = Form(""),
        user: User = Depends(current_user),
        db=Depends(get_db),
    ):
        check_csrf(request, csrf)
        _, draft = load_draft_page(db, item_id)
        if not draft or draft.status != DRAFT_SCHEDULED:
            raise HTTPException(400, "Este rascunho não está agendado")
        draft.status, draft.scheduled_for = DRAFT_APPROVED, None
        db.commit()
        target = next_url if next_url.startswith("/") and not next_url.startswith("//") else f"/items/{item_id}/draft"
        return RedirectResponse(target, status_code=303)

    @app.post("/items/{item_id}/draft/published")
    def draft_published(
        request: Request,
        item_id: int,
        next_url: str = Form(""),
        csrf: str = Form(""),
        user: User = Depends(current_user),
        db=Depends(get_db),
    ):
        check_csrf(request, csrf)
        _, draft = load_draft_page(db, item_id)
        if not draft or draft.status not in (DRAFT_APPROVED, DRAFT_SCHEDULED):
            raise HTTPException(400, "Só dá para marcar como publicado um conteúdo aprovado")
        draft.status, draft.scheduled_for = DRAFT_PUBLISHED, None
        draft.published_at = utcnow()
        db.commit()
        request.session["flash"] = "Marcado como publicado."
        target = next_url if next_url.startswith("/") and not next_url.startswith("//") else f"/items/{item_id}/draft"
        return RedirectResponse(target, status_code=303)

    # ----------------------------------------------------------------- coleta
    def _collect_job():
        try:
            with session_factory() as db:
                run_default(db, settings)
        finally:
            collect_lock.release()

    @app.post("/collect")
    def collect_now(
        request: Request,
        background: BackgroundTasks,
        csrf: str = Form(""),
        user: User = Depends(current_user),
    ):
        check_csrf(request, csrf)
        if collect_lock.acquire(blocking=False):
            background.add_task(_collect_job)
            request.session["flash"] = "Coleta iniciada. Leva até 2 minutos; atualize a página depois."
        else:
            request.session["flash"] = "Já existe uma coleta em andamento."
        return RedirectResponse("/", status_code=303)

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "date": date.today().isoformat()}

    return app


def app_factory() -> FastAPI:  # uvicorn --factory app.web.main:app_factory
    return create_app()
