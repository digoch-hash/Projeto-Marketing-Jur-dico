"""Depois da coleta: gera o card (rascunho + artes) das normas muito relevantes e avisa por e-mail.

Nada e publicado: o card fica esperando a sua revisao. O limite diario evita gasto (cada rascunho e uma chamada ao Claude).
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app import ig_account, notify
from app.artservice import generate_art
from app.brand_assets import BrandAssets
from app.config import Settings
from app.drafts import DRAFT_READY, DraftContent, run_draft_job, start_draft
from app.editorial import today_br
from app.models import STATUS_NEW, Draft, Item

log = logging.getLogger("autocard")


def eligible_ids(db, settings: Settings, limit: int) -> list[int]:
    if limit <= 0:
        return []
    has_draft = select(Draft.item_id)
    q = (
        select(Item.id)
        .where(Item.status == STATUS_NEW, Item.relevance >= settings.alert_min_relevance, Item.id.notin_(has_draft))
        .order_by(Item.relevance.desc(), Item.published_at.desc(), Item.id)
        .limit(limit)
    )
    return list(db.scalars(q))


def prepare_cards(session_factory, settings: Settings, claude_client=None, http_client=None) -> list[int]:
    """Gera os cards das normas muito relevantes ainda sem rascunho. Devolve os ids que ficaram prontos."""
    if not settings.auto_draft or not (settings.anthropic_api_key or claude_client):
        return []
    key = f"auto_drafts:{today_br().isoformat()}"
    with session_factory() as db:
        used = int(ig_account.get(db, key, "0") or 0)
        ids = eligible_ids(db, settings, settings.auto_draft_max_per_day - used)
        if not ids:
            return []
        ig_account.put(db, key, str(used + len(ids)))  # conta antes: falha tambem gasta (e nao repete em loop)
    ready: list[int] = []
    assets = BrandAssets(settings.data_dir)
    for item_id in ids:
        with session_factory() as db:
            item = db.get(Item, item_id)
            if not item or not start_draft(db, item):
                continue
        run_draft_job(session_factory, settings, item_id, claude_client=claude_client, http_client=http_client)
        with session_factory() as db:
            draft = db.scalar(select(Draft).where(Draft.item_id == item_id))
            if not draft or draft.status != DRAFT_READY:
                log.warning("card automatico nao ficou pronto item=%s: %s", item_id, draft.error if draft else "?")
                continue
            try:
                generate_art(assets, db.get(Item, item_id), draft, DraftContent.model_validate_json(draft.content))
                db.commit()
            except Exception:  # noqa: BLE001 - sem arte, o rascunho continua valendo (da para gerar na tela)
                log.exception("artes automaticas falharam item=%s", item_id)
            ready.append(item_id)
    return ready


def run_after_collect(session_factory, settings: Settings, claude_client=None, http_client=None, smtp_factory=None) -> dict:
    """Cards primeiro, aviso por e-mail depois (o e-mail ja diz quais cards estao prontos)."""
    ready = prepare_cards(session_factory, settings, claude_client, http_client)
    with session_factory() as db:
        sent = notify.alert_new_relevant(db, settings, ready=set(ready), smtp_factory=smtp_factory)
    return {"ready": ready, "alerted": sent}
