from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol

import httpx

SUMMARY_MAX = 2000


@dataclass
class RawItem:
    source: str
    external_id: str
    title: str
    url: str
    published_at: date | None = None
    summary: str = ""
    doc_type: str = ""
    issuer: str = ""

    def __post_init__(self) -> None:
        self.title = self.title.strip()[:500]
        self.summary = self.summary.strip()[:SUMMARY_MAX]


class Source(Protocol):
    name: str

    def fetch(self, since: date) -> list[RawItem]:
        """Itens publicados a partir de `since` (inclusive)."""
        ...


class SourceError(RuntimeError):
    """Falha ao ler uma fonte (rede, formato inesperado)."""


def make_client(user_agent: str, timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": user_agent, "Accept-Language": "pt-BR,pt;q=0.9"},
        timeout=timeout,
        follow_redirects=True,
    )


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def html_to_text(raw: str) -> str:
    text = re.sub(r"</(p|div|br|li|tr)>|<br\s*/?>", " ", raw, flags=re.I)
    text = _TAG.sub(" ", text)
    return _WS.sub(" ", html.unescape(text)).strip()
