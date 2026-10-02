"""FEPAM: comunicados e noticias publicados no site da fundacao.

As portarias da FEPAM saem no Diario Oficial e sao coletadas por `doe_rs`.
"""
from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.sources.base import RawItem, SourceError

BASE = "https://www.fepam.rs.gov.br"
PAGES = (f"{BASE}/comunicados", f"{BASE}/inicial")


def parse(html_text: str) -> list[RawItem]:
    soup = BeautifulSoup(html_text, "html.parser")
    out: dict[str, RawItem] = {}
    for li in soup.select("ul.media-list li.media"):
        link = li.select_one("h3.media-heading a[href]")
        if not link:
            continue
        title = " ".join(link.get_text(" ").split())
        href = link["href"]
        slug = href.strip("/").split("/")[-1]
        published = None
        t = li.find("time", attrs={"datetime": True})
        if t:
            try:
                published = datetime.fromisoformat(t["datetime"][:19]).date()
            except ValueError:
                published = None
        out[slug] = RawItem(
            source="fepam",
            external_id=slug,
            title=title,
            url=urljoin(BASE, href),
            published_at=published,
            summary=(link.get("title") or "").strip(),
            doc_type="Comunicado",
            issuer="FEPAM - Fundação Estadual de Proteção Ambiental",
        )
    return list(out.values())


class FepamSource:
    name = "fepam"

    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch(self, since: date) -> list[RawItem]:
        found: dict[str, RawItem] = {}
        errors = []
        for url in PAGES:
            try:
                resp = self.client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                errors.append(str(exc))
                continue
            for item in parse(resp.text):
                found.setdefault(item.external_id, item)
        if not found and errors:
            raise SourceError(f"FEPAM: {errors[0]}")
        return [i for i in found.values() if i.published_at is None or i.published_at >= since]
