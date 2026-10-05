"""Guarda segredos (token do Instagram) criptografados no banco, com chave derivada do SECRET_KEY."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class SecretBoxError(RuntimeError):
    pass


def _fernet(secret_key: str) -> Fernet:
    digest = hashlib.sha256(("hrbio-radar:secrets:" + secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def seal(secret_key: str, plaintext: str) -> str:
    return _fernet(secret_key).encrypt(plaintext.encode()).decode()


def unseal(secret_key: str, sealed: str) -> str:
    try:
        return _fernet(secret_key).decrypt(sealed.encode()).decode()
    except InvalidToken as exc:
        raise SecretBoxError("Não consegui abrir o token guardado (o SECRET_KEY mudou?). Conecte o Instagram de novo.") from exc
