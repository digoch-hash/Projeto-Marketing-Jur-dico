"""Conexao com a conta do Instagram: o token fica criptografado no banco e e renovado sozinho."""
from __future__ import annotations

from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from app.config import Settings
from app.instagram import InstagramClient, InstagramError
from app.models import Setting, utcnow
from app.secrets_box import SecretBoxError, seal, unseal

TOKEN_LIFETIME = timedelta(days=60)
REFRESH_WHEN_LEFT = timedelta(days=25)  # renova quando faltar menos que isso
REFRESH_MIN_AGE = timedelta(hours=24)  # a Meta so renova token com mais de 24 h


def get(db, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row else default


def put(db, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


_shared: httpx.Client | None = None


def _http(http: httpx.Client | None) -> httpx.Client:
    """Um cliente HTTP compartilhado (evita abrir conexoes novas a cada chamada)."""
    global _shared
    if http is not None:
        return http
    if _shared is None:
        _shared = httpx.Client(timeout=30.0)
    return _shared


def connect(db, settings: Settings, token: str, http: httpx.Client | None = None) -> str:
    """Valida o token na Meta e guarda. Devolve o @ da conta."""
    token = token.strip()
    if len(token) < 20:
        raise InstagramError("Esse token parece curto demais. Copie o token inteiro do painel da Meta.")
    info = InstagramClient(token, "", _http(http), settings.instagram_api_version).me()
    user_id, username = str(info.get("user_id") or info.get("id") or ""), info.get("username", "")
    if not user_id:
        raise InstagramError("O Instagram não informou qual é a conta. Gere o token de novo.")
    now = utcnow()
    put(db, "ig_token", seal(settings.secret_key, token))
    put(db, "ig_user_id", user_id)
    put(db, "ig_username", username)
    put(db, "ig_token_expires", (now + TOKEN_LIFETIME).isoformat())
    put(db, "ig_token_refreshed", now.isoformat())
    return username


def disconnect(db) -> None:
    for key in ("ig_token", "ig_user_id", "ig_username", "ig_token_expires", "ig_token_refreshed", "ig_auto_publish"):
        row = db.get(Setting, key)
        if row:
            db.delete(row)
    db.commit()


def is_connected(db) -> bool:
    return bool(get(db, "ig_token") and get(db, "ig_user_id"))


def load_client(db, settings: Settings, http: httpx.Client | None = None, **kw) -> InstagramClient:
    if not is_connected(db):
        raise InstagramError("O Instagram ainda não está conectado. Conecte na tela Instagram.")
    try:
        token = unseal(settings.secret_key, get(db, "ig_token"))
    except SecretBoxError as exc:
        raise InstagramError(str(exc), token_invalid=True) from exc
    return InstagramClient(token, get(db, "ig_user_id"), _http(http), settings.instagram_api_version, **kw)


def status(db, now: datetime | None = None) -> dict:
    now = now or utcnow()
    expires = datetime.fromisoformat(get(db, "ig_token_expires")) if get(db, "ig_token_expires") else None
    return {
        "connected": is_connected(db),
        "username": get(db, "ig_username"),
        "expires": expires,
        "days_left": (expires - now).days if expires else None,
        "auto_publish": get(db, "ig_auto_publish") == "1",
        "publish_story": get(db, "ig_publish_story", "1") == "1",
    }


def refresh_if_needed(db, settings: Settings, http: httpx.Client | None = None, now: datetime | None = None) -> bool:
    """Renova o token quando falta pouco para vencer. True se renovou."""
    now = now or utcnow()
    if not is_connected(db) or not get(db, "ig_token_expires"):
        return False
    expires = datetime.fromisoformat(get(db, "ig_token_expires"))
    refreshed = datetime.fromisoformat(get(db, "ig_token_refreshed") or "2000-01-01T00:00:00")
    if expires - now > REFRESH_WHEN_LEFT or now - refreshed < REFRESH_MIN_AGE:
        return False
    token = unseal(settings.secret_key, get(db, "ig_token"))
    new_token, seconds = InstagramClient.refresh_token(token, _http(http))
    put(db, "ig_token", seal(settings.secret_key, new_token))
    put(db, "ig_token_expires", (now + timedelta(seconds=seconds)).isoformat())
    put(db, "ig_token_refreshed", now.isoformat())
    return True
