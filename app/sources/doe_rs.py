"""Diario Oficial do Estado do RS.

O site e um app Angular; os dados vem de uma API REST publica (endereco definido em
https://www.diariooficial.rs.gov.br/environments/environment.json).
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta

import httpx

from app.relevance import quick_score
from app.sources.base import RawItem, SourceError, html_to_text

API = "https://doe-backend.pro.rs.gov.br"
SITE = "https://www.diariooficial.rs.gov.br"

# Tipos que sempre merecem ler o texto completo, mesmo sem palavra-chave no trecho.
ALWAYS_FETCH_TYPES = {"decreto", "decretos", "lei", "leis", "resoluções", "resolucoes"}
POLITE_DELAY = 0.15


TITLE_MAX = 160
_BLOCK_END = re.compile(r"</p>|</div>|<br\s*/?>|</h\d>|</li>", re.I)


def split_title(html_content: str) -> tuple[str, str]:
    """(titulo, texto_sem_o_titulo). O titulo e o primeiro bloco com texto; se o ato vier num
    paragrafo so, corta numa palavra inteira."""
    full = html_to_text(html_content)
    first = next((t for t in (html_to_text(b) for b in _BLOCK_END.split(html_content)) if len(t) >= 8), full)
    if len(first) <= TITLE_MAX:
        title = first.rstrip(" -–")
        rest = full[len(first):] if full.startswith(first) else full
        return title, rest.lstrip(" .-–:")
    cut = first[:TITLE_MAX].rsplit(" ", 1)[0].rstrip(" ,;:-–")
    return cut + "…", full


class DoeRsSource:
    name = "doe_rs"

    def __init__(self, client: httpx.Client, delay: float = POLITE_DELAY):
        self.client = client
        self.delay = delay

    def _get_json(self, path: str, **params):
        try:
            resp = self.client.get(f"{API}{path}", params=params)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"DOE-RS {path}: {exc}") from exc

    def _day(self, day: date) -> list[RawItem]:
        data = self._get_json(
            "/public/destaques/", pagina=1, tamanhoPagina=1000, data=day.isoformat()
        )
        out: list[RawItem] = []
        for entry in data.get("destaques") or []:
            snippet = entry.get("conteudo") or ""
            tipo = (entry.get("tipoMateria") or "").strip()
            wants_full = quick_score(snippet) > 0 or tipo.lower() in ALWAYS_FETCH_TYPES
            if not wants_full:
                continue
            item = self._full(entry["id"], fallback_type=tipo, fallback_day=day)
            if item:
                out.append(item)
        return out

    def _full(self, materia_id: int, fallback_type: str, fallback_day: date) -> RawItem | None:
        time.sleep(self.delay)
        try:
            m = self._get_json(f"/public/materias/{materia_id}")
        except SourceError:
            return None
        text = html_to_text(m.get("conteudo") or "")
        if not text:
            return None
        published = fallback_day
        if m.get("dataPublicacao"):
            try:
                published = datetime.strptime(m["dataPublicacao"], "%d-%m-%Y").date()
            except ValueError:
                pass
        title, body = split_title(m.get("conteudo") or "")
        return RawItem(
            source=self.name,
            external_id=str(materia_id),
            title=title,
            url=f"{SITE}/materia?id={materia_id}",
            published_at=published,
            summary=body or text,
            doc_type=m.get("nomeTipoMateria") or fallback_type,
            issuer=m.get("nomeEntidade") or "",
        )

    def fetch(self, since: date) -> list[RawItem]:
        today = date.today()
        items: list[RawItem] = []
        day = since
        failures = 0
        days = 0
        while day <= today:
            if day.weekday() < 5 or day == today:  # o DOE sai em dias uteis (e extras)
                days += 1
                try:
                    items.extend(self._day(day))
                except SourceError:
                    failures += 1
            day += timedelta(days=1)
        if days and failures == days:
            raise SourceError("DOE-RS: nenhum dia pode ser lido (API fora do ar ou mudou)")
        return items
