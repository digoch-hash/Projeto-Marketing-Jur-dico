"""CONSEMA: resolucoes listadas em https://www.sema.rs.gov.br/resolucoes (pagina estatica)."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.sources.base import RawItem, SourceError

BASE = "https://www.sema.rs.gov.br"
URL = f"{BASE}/resolucoes"

_NUM = re.compile(r"Resolu[çc][ãa]o\s+(?:CONSEMA\s+)?(?:n[ºo°.]*\s*)?(\d{1,4})\s*[/ ]\s*(\d{4})", re.I)
# Os PDFs ficam em /upload/arquivos/AAAAMM/DDHHMMSS-nome.pdf; o DD e o dia do envio.
_UPLOAD = re.compile(r"/upload/arquivos/(\d{4})(\d{2})/(\d{2})\d{6}-")


def published_from_upload(path: str) -> date | None:
    m = _UPLOAD.search(path)
    if not m:
        return None
    year, month, day = (int(g) for g in m.groups())
    for d in (day, 1):
        try:
            return date(year, month, d)
        except ValueError:
            continue
    return None


def parse(html_text: str) -> list[RawItem]:
    soup = BeautifulSoup(html_text, "html.parser")
    body = soup.select_one("div.artigo__texto") or soup
    items: dict[str, RawItem] = {}
    for p in body.find_all("p"):
        # alguns itens vem em Unicode decomposto (c + cedilha); normaliza antes de casar
        text = unicodedata.normalize("NFC", " ".join(p.get_text(" ").split()))
        m = _NUM.match(text)
        if not m:  # tambem descarta "Resolucao compilada ..." e paragrafos soltos
            continue
        number, year = m.group(1), m.group(2)
        key = f"{int(number)}/{year}"
        if key in items:
            continue
        link = next((a["href"] for a in p.find_all("a", href=True) if ".pdf" in a["href"].lower()), None)
        ementa = text[m.end():].lstrip(" :-–").strip()
        # remove o trecho "(Resolucao compilada ...)" quando vem colado na ementa
        ementa = re.sub(r"\(\s*Resolu[çc][ãa]o compilada.*?\)\s*\.?\s*$", "", ementa, flags=re.I).strip()
        pdf_url = urljoin(BASE, link) if link else URL
        items[key] = RawItem(
            source="consema",
            external_id=key,
            title=f"Resolução CONSEMA {key}",
            url=pdf_url,
            published_at=published_from_upload(link or ""),
            summary=ementa,
            doc_type="Resolução CONSEMA",
            issuer="CONSEMA - Conselho Estadual do Meio Ambiente (RS)",
        )
    return list(items.values())


class ConsemaSource:
    name = "consema"

    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch(self, since: date) -> list[RawItem]:
        try:
            resp = self.client.get(URL)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceError(f"CONSEMA: {exc}") from exc
        items = parse(resp.text)
        if not items:
            raise SourceError("CONSEMA: nenhuma resolucao encontrada (o layout da pagina mudou?)")
        # Sem data de upload valida, assume o ano da resolucao.
        return [
            i for i in items
            if (i.published_at or date(int(i.external_id.split("/")[1]), 1, 1)) >= since
        ]
