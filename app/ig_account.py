"""Conexao com a conta do Instagram: o token fica criptografado no banco e e renovado sozinho."""
from __future__ import annotations

from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from app.config import Settings
from app.instagram import FACEBOOK_HOST, HOST, InstagramClient, InstagramError
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


def uses_facebook_login(settings: Settings) -> bool:
    return bool(settings.facebook_app_id and settings.facebook_app_secret)


def connect_facebook(db, settings: Settings, token: str, http: httpx.Client | None = None) -> str:
    """Login do Facebook: troca o token por um de longa duracao e guarda o token da Pagina ligada ao Instagram."""
    token = token.strip()
    if len(token) < 20:
        raise InstagramError("Esse token parece curto demais. Copie o token inteiro do Explorador da Graph API.")
    client, version = _http(http), settings.instagram_api_version
    long_token = InstagramClient.facebook_long_lived_token(
        token, settings.facebook_app_id, settings.facebook_app_secret, client, version)
    accounts = InstagramClient.facebook_instagram_accounts(long_token, client, version)
    if not accounts:
        raise InstagramError(
            "Não achei nenhuma Página do Facebook com um Instagram profissional ligado. Ligue o Instagram da HRBio "
            "à Página e gere o token de novo, marcando a Página na autorização.")
    acc = accounts[0]
    now = utcnow()
    put(db, "ig_token", seal(settings.secret_key, acc["page_token"]))
    put(db, "ig_user_id", acc["ig_user_id"])
    put(db, "ig_username", acc["username"])
    put(db, "ig_mode", "facebook")
    put(db, "ig_page_name", acc["page_name"])
    put(db, "ig_token_expires", "")  # token de Pagina nao vence: nao ha o que renovar
    put(db, "ig_token_refreshed", now.isoformat())
    return acc["username"]


def connect(db, settings: Settings, token: str, http: httpx.Client | None = None) -> str:
    """Valida o token na Meta e guarda. Devolve o @ da conta."""
    if uses_facebook_login(settings):
        return connect_facebook(db, settings, token, http)
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
    put(db, "ig_mode", "instagram")
    put(db, "ig_page_name", "")
    put(db, "ig_token_expires", (now + TOKEN_LIFETIME).isoformat())
    put(db, "ig_token_refreshed", now.isoformat())
    return username


def disconnect(db) -> None:
    for key in ("ig_token", "ig_user_id", "ig_username", "ig_token_expires", "ig_token_refreshed", "ig_auto_publish",
                "ig_mode", "ig_page_name"):
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
    host = FACEBOOK_HOST if get(db, "ig_mode") == "facebook" else HOST
    return InstagramClient(token, get(db, "ig_user_id"), _http(http), settings.instagram_api_version, host=host, **kw)


def status(db, now: datetime | None = None) -> dict:
    now = now or utcnow()
    expires = datetime.fromisoformat(get(db, "ig_token_expires")) if get(db, "ig_token_expires") else None
    return {
        "connected": is_connected(db),
        "username": get(db, "ig_username"),
        "facebook_mode": get(db, "ig_mode") == "facebook",
        "page_name": get(db, "ig_page_name"),
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
