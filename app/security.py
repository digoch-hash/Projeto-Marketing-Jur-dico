"""Hash de senha (scrypt, so biblioteca padrao) e token CSRF."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return "scrypt${}${}".format(base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, dk_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


class LoginThrottle:
    """Trava tentativas de senha: apos `max_failures` erros na janela, bloqueia por `lock` segundos."""

    def __init__(self, max_failures: int = 5, window: float = 900, lock: float = 900, clock=None):
        import time

        self.max_failures, self.window, self.lock = max_failures, window, lock
        self.clock = clock or time.monotonic
        self._fails: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def blocked_for(self, *keys: str) -> int:
        """Segundos restantes de bloqueio (0 se livre)."""
        now = self.clock()
        left = max((self._locked_until.get(k, 0) - now for k in keys), default=0)
        return int(left) + 1 if left > 0 else 0

    def failure(self, key: str, limit: int | None = None) -> None:
        now = self.clock()
        recent = [t for t in self._fails.get(key, []) if now - t < self.window] + [now]
        self._fails[key] = recent
        if len(recent) >= (limit or self.max_failures):
            self._locked_until[key] = now + self.lock
            self._fails[key] = []

    def success(self, key: str) -> None:
        self._fails.pop(key, None)
        self._locked_until.pop(key, None)
