from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from app import autocard, ig_account, notify, publicurls
from app.artservice import generate_art
from app.backup import BackupError, make_backup
from app.brand_assets import AssetError, BrandAssets
from app.collector import run_default
from app.editorial import MIN_GAP_DAYS, build_agenda, conflicts, suggest_date, taken_dates, today_br
from app.drafts import DraftContent, build_full_caption, run_draft_job, start_draft
from app.config import Settings, load_settings
from app.db import init_db, make_engine, make_session_factory
from app.models import (
    DRAFT_APPROVED, DRAFT_ERROR, DRAFT_GENERATING, DRAFT_PUBLISHED, DRAFT_READY, DRAFT_SCHEDULED, STATUSES, CollectRun, Draft, Item, User, utcnow,
)
from app.instagram import InstagramError
from app.publisher import is_stuck, mark_published_by_hand, publish_draft, release_uncertain
from app.relevance import THEMES
from app.scheduler import Scheduler
from app.security import LoginThrottle, hash_password, new_csrf_token, verify_password

BASE_DIR = Path(__file__).parent
_DUMMY_HASH = hash_password("senha-qualquer-para-igualar-o-tempo")
PAGE_SIZE = 20
DEFAULT_MIN_RELEVANCE = 40

SOURCE_LABELS = {"doe_rs": "Diário Oficial RS", "consema": "CONSEMA", "fepam": "FEPAM"}
STATUS_LABELS = {"new": "Novas", "post": "Quero postar", "later": "Depois", "ignored": "Ignoradas"}
TAB_LABELS = {"new": "Novas", "post": "Postar", "later": "Depois", "ignored": "Ignoradas"}


def ensure_admin(session_factory, settings: Settings) -> None:
    """Cria o primeiro usuario a partir de ADMIN_USERNAME/ADMIN_PASSWORD (hospedagem sem terminal). Nunca troca senha."""
    if not settings.admin_username or not settings.admin_password:
        return
    if len(settings.admin_password) < 8:
        raise RuntimeError("ADMIN_PASSWORD precisa ter pelo menos 8 caracteres.")
    with session_factory() as db:
        if not db.scalar(select(User).where(User.username == settings.admin_username)):
            db.add(User(username=settings.admin_username, password_hash=hash_password(settings.admin_password)))
            db.commit()


def create_app(settings: Settings | None = None, session_factory=None) -> FastAPI:
    settings = settings or load_settings()
    if session_factory is None:
        engine = make_engine(settings.database_url)
        init_db(engine)
        session_factory = make_session_factory(engine)

    if settings.cookie_secure and settings.secret_key in ("", "dev-only-change-me", "troque-esta-chave"):
        raise RuntimeError("Em produção (COOKIE_SECURE=1) defina um SECRET_KEY secreto e único.")
    ensure_admin(session_factory, settings)
    scheduler = Scheduler(session_factory, settings) if settings.scheduler_enabled else None

    @asynccontextmanager
    async def lifespan(_app):
        if scheduler:
            scheduler.start()
        yield
        if scheduler:
            scheduler.stop()

    app = FastAPI(title="HRBio · Radar de Normas", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.scheduler = scheduler
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        max_age=60 * 60 * 24 * 30,
        same_site="lax",
        https_only=settings.cookie_secure,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response

    throttle = LoginThrottle()
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
        name = username.strip().lower()
        ip = request.client.host if request.client else "?"
        wait = throttle.blocked_for(f"u:{name}", f"ip:{ip}")
        if wait:
            return render(request, "login.html", error=f"Muitas tentativas. Tente de novo em {wait // 60 + 1} min.")
        user = db.scalar(select(User).where(func.lower(User.username) == name))
        ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)  # mesmo custo com ou sem usuario
        if not user or not ok:
            throttle.failure(f"u:{name}")
            throttle.failure(f"ip:{ip}", limit=30)
            return render(request, "login.html", error="Usuário ou senha incorretos.")
        throttle.success(f"u:{name}")
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
        hot = db.scalar(
            select(func.count()).select_from(Item).where(Item.status == "new", Item.relevance >= settings.alert_min_relevance)
        ) or 0
        last_runs = db.scalars(select(CollectRun).order_by(CollectRun.id.desc()).limit(6)).all()
        last_run = {r.source: r for r in reversed(last_runs)}
        return render(
            request, "index.html",
            user=user, items=items, total=total, status=status, source=source, theme=theme,
            min_rel=min_rel, page=page, pages=max(1, -(-total // PAGE_SIZE)), counts=counts,
            last_run=last_run, hot=hot, alert_min=settings.alert_min_relevance,
            monitor_text=("todo dia" if settings.collect_every_days == 1 else f"a cada {settings.collect_every_days} dias"),
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
            ig=ig_context(db), stuck=bool(draft and is_stuck(draft)),
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
        images = generate_art(assets, item, draft, content, photo)
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
        return render(
            request, "marca.html", user=user, photos=assets.list_photos(),
            custom={k: assets.is_custom_logo(k) for k in ("escuro", "claro")},
        )

    @app.post("/marca/logo")
    async def brand_logo(request: Request, user: User = Depends(current_user)):
        form = await request.form()
        check_csrf(request, str(form.get("csrf", "")))
        upload = form.get("logo")
        try:
            if not hasattr(upload, "read"):
                raise AssetError("Escolha um arquivo de logo.")
            assets.save_logo(await upload.read(), str(form.get("tipo", "")))
            request.session["flash"] = "Logo atualizado."
        except AssetError as exc:
            request.session["flash"] = str(exc)
        return RedirectResponse("/marca", status_code=303)

    @app.post("/marca/logo/{kind}/restaurar")
    def brand_logo_reset(request: Request, kind: str, csrf: str = Form(""), user: User = Depends(current_user)):
        check_csrf(request, csrf)
        assets.reset_logo(kind)
        request.session["flash"] = "Voltou para o logo padrão."
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

    @app.get("/marca/logo/{kind}")
    def brand_logo_file(kind: str, user: User = Depends(current_user)):
        path = assets.logo_file(kind)
        if not path:
            raise HTTPException(404, "Sem logo")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------- imagens para o Instagram
    @app.get("/pub/{item_id}/{exp}/{sig}/{name}")
    def public_art(item_id: int, exp: int, sig: str, name: str):
        """Sem login, de proposito: o Instagram baixa a imagem daqui. So funciona com o link assinado e dentro da validade."""
        if not publicurls.verify(settings.secret_key, item_id, exp, sig, name):
            raise HTTPException(404, "Link inválido ou vencido")
        path = assets.art_jpg_path(item_id, name)
        if not path:
            raise HTTPException(404, "Imagem não encontrada")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # ----------------------------------------------------------------- instagram
    def ig_context(db) -> dict:
        info = ig_account.status(db)
        info["public_url_ok"] = settings.public_base_url.startswith("https://")
        info["publish_hour"] = settings.publish_hour
        return info

    @app.get("/instagram")
    def instagram_page(request: Request, user: User = Depends(current_user), db=Depends(get_db)):
        return render(request, "instagram.html", user=user, ig=ig_context(db), public_url=settings.public_base_url)

    @app.post("/instagram/conectar")
    async def instagram_connect(request: Request, user: User = Depends(current_user), db=Depends(get_db)):
        form = await request.form()
        check_csrf(request, str(form.get("csrf", "")))
        try:
            username = ig_account.connect(db, settings, str(form.get("token", "")))
            request.session["flash"] = f"Conectado como @{username}. Deixei a publicação automática DESLIGADA: teste antes de ligar."
        except InstagramError as exc:
            request.session["flash"] = f"Não foi possível conectar: {exc}"
        return RedirectResponse("/instagram", status_code=303)

    @app.post("/instagram/config")
    async def instagram_config(request: Request, user: User = Depends(current_user), db=Depends(get_db)):
        form = await request.form()
        check_csrf(request, str(form.get("csrf", "")))
        if not ig_account.is_connected(db):
            raise HTTPException(400, "Conecte o Instagram primeiro")
        ig_account.put(db, "ig_auto_publish", "1" if form.get("auto_publish") else "0")
        ig_account.put(db, "ig_publish_story", "1" if form.get("publish_story") else "0")
        request.session["flash"] = "Configurações salvas."
        return RedirectResponse("/instagram", status_code=303)

    @app.post("/instagram/desconectar")
    def instagram_disconnect(request: Request, csrf: str = Form(""), user: User = Depends(current_user), db=Depends(get_db)):
        check_csrf(request, csrf)
        ig_account.disconnect(db)
        request.session["flash"] = "Instagram desconectado. O token foi apagado do sistema."
        return RedirectResponse("/instagram", status_code=303)

    @app.post("/items/{item_id}/draft/publish-now")
    def draft_publish_now(
        request: Request,
        background: BackgroundTasks,
        item_id: int,
        csrf: str = Form(""),
        user: User = Depends(current_user),
        db=Depends(get_db),
    ):
        check_csrf(request, csrf)
        _, draft = load_draft_page(db, item_id)
        if not draft or draft.status not in (DRAFT_APPROVED, DRAFT_SCHEDULED):
            raise HTTPException(400, "Só dá para publicar um conteúdo aprovado")
        if not ig_account.is_connected(db):
            request.session["flash"] = "Conecte o Instagram antes (tela Instagram)."
        elif draft.publish_state in ("publishing", "uncertain"):
            request.session["flash"] = "Já há uma publicação em andamento ou a confirmar para este item."
        else:
            background.add_task(publish_draft, session_factory, settings, item_id)
            request.session["flash"] = "Publicando no Instagram… leva cerca de 1 minuto. Atualize a página."
        return RedirectResponse(f"/items/{item_id}/draft", status_code=303)

    @app.post("/items/{item_id}/draft/publish-resolve")
    def draft_publish_resolve(
        request: Request,
        item_id: int,
        action: str = Form(...),
        csrf: str = Form(""),
        user: User = Depends(current_user),
        db=Depends(get_db),
    ):
        """Depois de uma publicacao 'a confirmar': o usuario diz se o post saiu ou nao."""
        check_csrf(request, csrf)
        load_draft_page(db, item_id)
        if action == "published":
            mark_published_by_hand(db, item_id)
            request.session["flash"] = "Marcado como publicado."
        elif action == "retry":
            release_uncertain(db, item_id)
            request.session["flash"] = "Liberado. Você pode publicar de novo."
        else:
            raise HTTPException(400, "Ação inválida")
        return RedirectResponse(f"/items/{item_id}/draft", status_code=303)

    # --------------------------------------------------------------------- conta
    @app.get("/conta")
    def account_page(request: Request, user: User = Depends(current_user), db=Depends(get_db)):
        return render(
            request, "conta.html", user=user, users=db.scalars(select(User).order_by(User.username)).all(),
            email_ok=notify.is_configured(settings), emails=notify.recipients(settings), alert_min=settings.alert_min_relevance,
        )

    @app.post("/conta/email-teste")
    def account_test_email(request: Request, csrf: str = Form(""), user: User = Depends(current_user)):
        check_csrf(request, csrf)
        if not notify.is_configured(settings):
            request.session["flash"] = "O e-mail de aviso não está configurado (SMTP_HOST, SMTP_USER, SMTP_PASSWORD e ALERT_EMAILS)."
        else:
            try:
                notify.send_email(settings, "HRBio Radar: e-mail de teste", "Se você recebeu isto, o aviso por e-mail está funcionando.")
                request.session["flash"] = f"E-mail de teste enviado para {', '.join(notify.recipients(settings))}."
            except Exception as exc:  # noqa: BLE001 - mostra o motivo (senha errada, servidor...) em vez de erro 500
                request.session["flash"] = f"Não consegui enviar: {exc}"
        return RedirectResponse("/conta", status_code=303)

    @app.get("/conta/backup")
    def account_backup(request: Request, user: User = Depends(current_user)):
        try:
            data, name = make_backup(settings)
        except BackupError as exc:
            request.session["flash"] = str(exc)
            return RedirectResponse("/conta", status_code=303)
        return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    @app.post("/conta/senha")
    async def account_password(request: Request, user: User = Depends(current_user), db=Depends(get_db)):
        form = await request.form()
        check_csrf(request, str(form.get("csrf", "")))
        new = str(form.get("nova", ""))
        if not verify_password(str(form.get("atual", "")), user.password_hash):
            request.session["flash"] = "A senha atual está errada."
        elif len(new) < 8:
            request.session["flash"] = "A nova senha precisa ter pelo menos 8 caracteres."
        else:
            user.password_hash = hash_password(new)
            db.commit()
            request.session["flash"] = "Senha alterada."
        return RedirectResponse("/conta", status_code=303)

    @app.post("/conta/usuario")
    async def account_add_user(request: Request, user: User = Depends(current_user), db=Depends(get_db)):
        form = await request.form()
        check_csrf(request, str(form.get("csrf", "")))
        name, pw = str(form.get("usuario", "")).strip(), str(form.get("senha", ""))
        if not name or len(name) > 64:
            request.session["flash"] = "Informe um nome de usuário."
        elif len(pw) < 8:
            request.session["flash"] = "A senha precisa ter pelo menos 8 caracteres."
        elif db.scalar(select(User).where(func.lower(User.username) == name.lower())):
            request.session["flash"] = "Esse usuário já existe."
        else:
            db.add(User(username=name, password_hash=hash_password(pw)))
            db.commit()
            request.session["flash"] = f"Usuário {name} criado."
        return RedirectResponse("/conta", status_code=303)

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
            gap_text=("só evita dois posts em dias seguidos" if MIN_GAP_DAYS >= 2 else "só evita dois posts no mesmo dia"),
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
            autocard.run_after_collect(session_factory, settings)
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
