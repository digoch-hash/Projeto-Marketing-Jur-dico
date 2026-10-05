"""Links publicos, assinados e temporarios, para o Instagram baixar as imagens do post.

O Instagram precisa buscar cada imagem por um endereco publico. Em vez de abrir a pasta de artes, cada
arquivo ganha um link que so funciona para aquele arquivo, por tempo limitado.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time

NAME = re.compile(r"^(slide_\d{2}|story)\.jpg$")
DEFAULT_TTL = 6 * 3600


def _sig(secret: str, item_id: int, name: str, exp: int) -> str:
    mac = hmac.new(secret.encode(), f"pub:{int(item_id)}:{name}:{int(exp)}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:32]


def sign(secret: str, base_url: str, item_id: int, name: str, ttl: int = DEFAULT_TTL, now: float | None = None) -> str:
    if not NAME.match(name):
        raise ValueError("nome de arquivo invalido")
    exp = int((now if now is not None else time.time()) + ttl)
    return f"{base_url.rstrip('/')}/pub/{int(item_id)}/{exp}/{_sig(secret, item_id, name, exp)}/{name}"


def verify(secret: str, item_id: int, exp: int, sig: str, name: str, now: float | None = None) -> bool:
    if not NAME.match(name) or exp < (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(sig, _sig(secret, item_id, name, exp))
