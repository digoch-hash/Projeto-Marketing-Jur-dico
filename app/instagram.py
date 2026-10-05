"""Cliente da Instagram API (login do Instagram): publicar carrossel/story e renovar o token.

Endpoints conforme a documentacao da Meta (Content Publishing): POST /{ig-id}/media, POST /{ig-id}/media_publish,
GET /{container-id}?fields=status_code. As imagens precisam ser JPEG e estar em enderecos publicos.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

HOST = "https://graph.instagram.com"
MAX_CAROUSEL = 10
CAPTION_MAX = 2200
HASHTAGS_MAX = 30


class InstagramError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, token_invalid: bool = False):
        super().__init__(message)
        self.code = code
        self.token_invalid = token_invalid


class UncertainPublish(InstagramError):
    """A chamada final de publicacao nao teve resposta clara: o post PODE ter ido ao ar."""


@dataclass
class Published:
    media_id: str
    permalink: str | None = None


def _error_from(resp: httpx.Response) -> InstagramError:
    try:
        err = resp.json().get("error", {})
    except ValueError:
        err = {}
    code = err.get("code")
    message = err.get("error_user_msg") or err.get("message") or f"Erro {resp.status_code} do Instagram"
    return InstagramError(message, code=code, token_invalid=(code == 190 or resp.status_code == 401))


def check_caption(caption: str) -> None:
    if len(caption) > CAPTION_MAX:
        raise InstagramError(f"A legenda tem {len(caption)} caracteres; o Instagram aceita até {CAPTION_MAX}.")
    if caption.count("#") > HASHTAGS_MAX:
        raise InstagramError(f"A legenda tem mais de {HASHTAGS_MAX} hashtags, o limite do Instagram.")


class InstagramClient:
    def __init__(self, token: str, ig_user_id: str, http: httpx.Client, version: str = "v23.0", sleep=time.sleep):
        self.token, self.ig_user_id, self.http, self.version, self.sleep = token, ig_user_id, http, version, sleep

    # ---------------------------------------------------------------- baixo nivel
    def _url(self, path: str) -> str:
        return f"{HOST}/{self.version}/{path.lstrip('/')}"

    def _request(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self.token
        try:
            if method == "POST":
                resp = self.http.post(self._url(path), data=params)
            else:
                resp = self.http.get(self._url(path), params=params)
        except httpx.HTTPError as exc:
            raise InstagramError(f"Não consegui falar com o Instagram: {exc}") from exc
        if resp.status_code >= 400:
            raise _error_from(resp)
        try:
            return resp.json()
        except ValueError as exc:
            raise InstagramError("Resposta inesperada do Instagram.") from exc

    # ------------------------------------------------------------------- conta
    def me(self) -> dict:
        return self._request("GET", "me", fields="user_id,username")

    def permalink(self, media_id: str) -> str | None:
        try:
            return self._request("GET", media_id, fields="permalink").get("permalink")
        except InstagramError:
            return None  # o link e so um extra; nao derruba uma publicacao que deu certo

    # --------------------------------------------------------------- containers
    def create_image_container(self, image_url: str, *, caption: str | None = None,
                               carousel_item: bool = False, story: bool = False) -> str:
        params: dict = {"image_url": image_url}
        if carousel_item:
            params["is_carousel_item"] = "true"
        if story:
            params["media_type"] = "STORIES"
        if caption and not carousel_item and not story:
            params["caption"] = caption
        return self._need_id(self._request("POST", f"{self.ig_user_id}/media", **params))

    def create_carousel_container(self, children: list[str], caption: str) -> str:
        if not 2 <= len(children) <= MAX_CAROUSEL:
            raise InstagramError(f"Um carrossel precisa de 2 a {MAX_CAROUSEL} imagens (tem {len(children)}).")
        return self._need_id(self._request(
            "POST", f"{self.ig_user_id}/media",
            media_type="CAROUSEL", children=",".join(children), caption=caption,
        ))

    @staticmethod
    def _need_id(data: dict) -> str:
        if not data.get("id"):
            raise InstagramError("O Instagram não devolveu o código do rascunho do post.")
        return str(data["id"])

    def wait_ready(self, container_id: str, timeout: float = 180, interval: float = 3) -> None:
        waited = 0.0
        while True:
            status = self._request("GET", container_id, fields="status_code").get("status_code")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise InstagramError(f"O Instagram não conseguiu processar as imagens (status {status}).")
            if waited >= timeout:
                raise InstagramError("O Instagram demorou demais para processar as imagens. Tente de novo.")
            self.sleep(interval)
            waited += interval

    def publish(self, container_id: str) -> str:
        """Ultimo passo. Se aqui a resposta se perder, o post pode ter saido: por isso `UncertainPublish`."""
        try:
            data = self._request("POST", f"{self.ig_user_id}/media_publish", creation_id=container_id)
        except InstagramError as exc:
            if exc.code is None and "Não consegui falar" in str(exc):  # timeout/queda de rede
                raise UncertainPublish(str(exc)) from exc
            raise
        if not data.get("id"):
            raise UncertainPublish("O Instagram não confirmou a publicação.")
        return str(data["id"])

    # ------------------------------------------------------------- fluxos prontos
    def publish_carousel(self, image_urls: list[str], caption: str) -> Published:
        check_caption(caption)
        if len(image_urls) == 1:
            container = self.create_image_container(image_urls[0], caption=caption)
        else:
            children = [self.create_image_container(u, carousel_item=True) for u in image_urls]
            for child in children:
                self.wait_ready(child)
            container = self.create_carousel_container(children, caption)
        self.wait_ready(container)
        media_id = self.publish(container)
        return Published(media_id, self.permalink(media_id))

    def publish_story(self, image_url: str) -> Published:
        container = self.create_image_container(image_url, story=True)
        self.wait_ready(container)
        return Published(self.publish(container))

    # ------------------------------------------------------------------- token
    @staticmethod
    def refresh_token(token: str, http: httpx.Client) -> tuple[str, int]:
        """Renova um token de longa duracao. Devolve (novo_token, segundos_de_validade)."""
        try:
            resp = http.get(f"{HOST}/refresh_access_token", params={"grant_type": "ig_refresh_token", "access_token": token})
        except httpx.HTTPError as exc:
            raise InstagramError(f"Não consegui falar com o Instagram: {exc}") from exc
        if resp.status_code >= 400:
            raise _error_from(resp)
        data = resp.json()
        return data["access_token"], int(data.get("expires_in", 60 * 24 * 3600))
