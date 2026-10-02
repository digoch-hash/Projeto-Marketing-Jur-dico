"""Busca o texto completo do ato na fonte oficial, para gerar o rascunho em cima dele."""
from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from app.models import Item
from app.sources.base import html_to_text

DOE_API = "https://doe-backend.pro.rs.gov.br"
PDF_MAX_BYTES = 20 * 1024 * 1024
TEXT_MAX_CHARS = 400_000


class FullTextError(RuntimeError):
    pass


@dataclass
class Material:
    """O que entregamos ao Claude: texto, ou um PDF (que ele le nativamente)."""

    text: str = ""
    pdf_b64: str = ""

    @property
    def is_pdf(self) -> bool:
        return bool(self.pdf_b64)


def _get(client: httpx.Client, url: str) -> httpx.Response:
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return resp
    except httpx.HTTPError as exc:
        raise FullTextError(f"Não consegui abrir a fonte oficial: {exc}") from exc


def _from_doe(item: Item, client: httpx.Client) -> Material:
    data = _get(client, f"{DOE_API}/public/materias/{item.external_id}").json()
    text = html_to_text(data.get("conteudo") or "")
    if not text:
        raise FullTextError("O Diário Oficial não devolveu o texto deste ato.")
    return Material(text=text)


def _from_pdf(item: Item, client: httpx.Client) -> Material:
    resp = _get(client, item.url)
    body = resp.content
    if not body.startswith(b"%PDF"):
        raise FullTextError("O endereço da resolução não devolveu um PDF.")
    if len(body) > PDF_MAX_BYTES:
        raise FullTextError("O PDF da resolução é grande demais para ler (limite de 20 MB).")
    return Material(pdf_b64=base64.standard_b64encode(body).decode())


def _from_page(item: Item, client: httpx.Client) -> Material:
    soup = BeautifulSoup(_get(client, item.url).text, "html.parser")
    node = soup.select_one("div.artigo__texto") or soup.select_one("article") or soup.body
    text = " ".join(node.get_text(" ").split()) if node else ""
    if len(text) < 80:
        raise FullTextError("Não consegui extrair o texto desta página.")
    return Material(text=text)


def fetch_material(item: Item, client: httpx.Client) -> Material:
    if item.source == "doe_rs":
        material = _from_doe(item, client)
    elif item.source == "consema" and item.url.lower().endswith(".pdf"):
        material = _from_pdf(item, client)
    else:
        material = _from_page(item, client)
    if len(material.text) > TEXT_MAX_CHARS:
        raise FullTextError(
            f"O texto tem {len(material.text):,} caracteres, mais do que o limite de {TEXT_MAX_CHARS:,}. "
            "Gere o rascunho manualmente a partir do trecho que interessa."
        )
    return material
