"""Rotina diaria "sem servidor": acha as normas muito relevantes de ONTEM e entrega o texto para escrever o card.

Roda dentro de uma sessao agendada. Cada execucao e independente (sem memoria): por isso cobre exatamente um dia
(ontem, em Brasilia). Rodando todo dia, cada dia e coberto uma vez e nada se repete.
"""
from __future__ import annotations

import base64
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.collector import run_default
from app.config import Settings
from app.editorial import today_br
from app.fulltext import fetch_material
from app.models import Item
from app.sources.base import make_client

MAYBE_FROM = 45  # abaixo do corte de aviso, mas vale citar o titulo


def target_date(arg: str | None, today: date | None = None) -> date:
    if arg:
        return datetime.strptime(arg, "%Y-%m-%d").date()
    return (today or today_br()) - timedelta(days=1)


def _brief(item: Item) -> dict:
    return {
        "id": item.id, "titulo": item.title, "fonte": item.source, "orgao": item.issuer, "tipo": item.doc_type,
        "publicado_em": item.published_at.isoformat() if item.published_at else None,
        "relevancia": item.relevance, "temas": item.theme_list, "motivo": item.relevance_reason, "url": item.url,
    }


def run_daily(session_factory, settings: Settings, date_arg: str | None = None, *, max_cards: int = 3,
              collect=run_default, material=fetch_material, http=None, today: date | None = None) -> dict:
    target = target_date(date_arg, today)
    days_back = max(0, ((today or today_br()) - target).days)
    http = http or make_client(settings.user_agent, timeout=60.0)
    out_dir = Path(settings.data_dir) / "pdf"

    with session_factory() as db:
        results = collect(db, settings, days_back)
        errors = [{"fonte": r.source, "erro": r.error} for r in results if r.error]

        day_items = list(db.scalars(
            select(Item).where(Item.published_at == target).order_by(Item.relevance.desc(), Item.id)
        ))
        hot = [i for i in day_items if i.relevance >= settings.alert_min_relevance][:max_cards]
        maybe = [i for i in day_items if MAYBE_FROM <= i.relevance < settings.alert_min_relevance]

        candidates = []
        for item in hot:
            entry = _brief(item)
            try:
                m = material(item, http)
                if m.is_pdf:  # resolucao do CONSEMA: o PDF vai para um arquivo, que o Claude le
                    out_dir.mkdir(parents=True, exist_ok=True)
                    path = out_dir / f"{item.id}.pdf"
                    path.write_bytes(base64.b64decode(m.pdf_b64))
                    entry.update(texto="", pdf=str(path))
                else:
                    entry.update(texto=m.text, pdf=None)
            except Exception as exc:  # noqa: BLE001 - sem o texto o card nao deve ser inventado: a rotina avisa
                entry.update(texto="", pdf=None, erro_texto=str(exc))
            candidates.append(entry)

        return {
            "data_alvo": target.isoformat(),
            "lidos_no_dia": len(day_items),
            "fontes": [{"fonte": r.source, "lidos": r.fetched, "novos": r.created, "erro": r.error} for r in results],
            "fontes_com_erro": errors,
            "candidatos": candidates,
            "talvez_titulos": [{"id": i.id, "titulo": i.title, "relevancia": i.relevance} for i in maybe],
        }
