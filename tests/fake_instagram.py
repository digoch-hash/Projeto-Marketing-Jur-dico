"""Instagram de mentira: registra cada chamada e responde como a API da Meta."""
from __future__ import annotations

from urllib.parse import parse_qs

import httpx


class FakeInstagram:
    def __init__(self):
        self.calls: list[dict] = []
        self.status_sequence: dict[str, list[str]] = {}  # container -> status_codes a devolver, em ordem
        self.fail_on: dict[str, httpx.Response | Exception] = {}  # "media", "media_publish", "story", "status"
        self._n = 0
        self.published: list[str] = []

    def client(self) -> httpx.Client:
        def safe(req):
            try:
                return self.handle(req)
            except _Respond as r:
                return r.response

        return httpx.Client(transport=httpx.MockTransport(safe))

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        params = dict(req.url.params)
        if req.method == "POST":
            params = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        self.calls.append({"method": req.method, "path": path, "params": params})
        token = params.get("access_token")
        if token and token.startswith("TOKEN-VENCIDO"):
            return httpx.Response(400, json={"error": {"message": "Error validating access token", "code": 190}})

        if path.endswith("/refresh_access_token"):
            return httpx.Response(200, json={"access_token": "NOVO-TOKEN-" + "x" * 20, "token_type": "bearer", "expires_in": 5184000})
        if path.endswith("/me"):
            return httpx.Response(200, json={"user_id": "1784140000", "username": "hrbioambiental"})
        if path.endswith("/media_publish"):
            self._maybe_fail("media_publish")
            mid = self._id("MEDIA")
            self.published.append(params["creation_id"])
            return httpx.Response(200, json={"id": mid})
        if path.endswith("/media") and req.method == "POST":
            kind = "story" if params.get("media_type") == "STORIES" else "media"
            self._maybe_fail(kind)
            return httpx.Response(200, json={"id": self._id("CONT")})
        if params.get("fields") == "status_code":
            self._maybe_fail("status")
            cid = path.rsplit("/", 1)[-1]
            seq = self.status_sequence.get(cid)
            return httpx.Response(200, json={"status_code": seq.pop(0) if seq else "FINISHED"})
        if params.get("fields") == "permalink":
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/p/ABC123/"})
        return httpx.Response(404, json={"error": {"message": "rota desconhecida", "code": 100}})

    def _maybe_fail(self, key: str):
        err = self.fail_on.get(key)
        if isinstance(err, Exception):
            raise err
        if err is not None:
            raise _Respond(err)

    # ---- consultas para os testes
    def posts(self, suffix: str):
        return [c for c in self.calls if c["path"].endswith(suffix) and c["method"] == "POST"]


class _Respond(Exception):
    def __init__(self, response):
        self.response = response
