from __future__ import annotations

import os
import threading
from datetime import date
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from app.collector import run_default
from app.config import Settings, load_settings
from app.db import init_db, make_engine, make_session_factory
from app.models import STATUSES, CollectRun, Item, User, utcnow
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
            last_run=last_run, flash=request.session.pop("flash", None),
        )

    @app.get("/items/{item_id}")
    def detail(request: Request, item_id: int, user: User = Depends(current_user), db=Depends(get_db)):
        item = db.get(Item, item_id)
        if not item:
            raise HTTPException(404, "Item não encontrado")
        return render(request, "detail.html", user=user, item=item)

    @app.post("/items/{item_id}/status")
    def set_status(
        request: Request,
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
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/"  # evita redirecionamento para outro site
        return RedirectResponse(next_url, status_code=303)

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
