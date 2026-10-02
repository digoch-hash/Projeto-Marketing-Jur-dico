"""Publica um rascunho aprovado no Instagram, sem nunca duplicar o post."""
from __future__ import annotations

import logging
import time
from datetime import timedelta

import httpx
from sqlalchemy import or_, select, update

from app import ig_account, publicurls
from app.artservice import art_is_complete, generate_art
from app.brand_assets import BrandAssets
from app.config import Settings
from app.drafts import DraftContent, build_full_caption
from app.instagram import InstagramError, UncertainPublish
from app.models import DRAFT_APPROVED, DRAFT_PUBLISHED, DRAFT_SCHEDULED, Draft, Item, utcnow

log = logging.getLogger("publisher")
STUCK_AFTER = timedelta(minutes=15)


def claim(db, item_id: int) -> bool:
    """Reserva a publicacao de forma atomica: so uma execucao ganha, mesmo com cliques/ticks simultaneos."""
    res = db.execute(
        update(Draft)
        .where(
            Draft.item_id == item_id,
            Draft.status.in_((DRAFT_APPROVED, DRAFT_SCHEDULED)),
            or_(Draft.publish_state.is_(None), Draft.publish_state == "failed"),
        )
        .values(publish_state="publishing", publish_started_at=utcnow(), publish_error=None)
    )
    db.commit()
    return res.rowcount == 1


def release_uncertain(db, item_id: int) -> None:
    """O usuario conferiu no Instagram que o post NAO saiu: libera para tentar de novo."""
    draft = db.scalar(select(Draft).where(Draft.item_id == item_id))
    if draft and draft.publish_state in ("uncertain", "publishing"):
        draft.publish_state, draft.publish_error = None, None
        db.commit()


def mark_published_by_hand(db, item_id: int) -> None:
    draft = db.scalar(select(Draft).where(Draft.item_id == item_id))
    if draft and draft.status != DRAFT_PUBLISHED:
        draft.status, draft.scheduled_for, draft.published_at = DRAFT_PUBLISHED, None, utcnow()
        draft.publish_state, draft.publish_error = None, None
        db.commit()


def is_stuck(draft: Draft, now=None) -> bool:
    now = now or utcnow()
    return draft.publish_state == "publishing" and bool(draft.publish_started_at) and now - draft.publish_started_at > STUCK_AFTER


def publish_draft(session_factory, settings: Settings, item_id: int, *, http: httpx.Client | None = None,
                  sleep=time.sleep) -> str:
    """Devolve: "published", "failed", "uncertain" ou "skipped" (outra execucao ja cuida disso)."""
    with session_factory() as db:
        if not claim(db, item_id):
            return "skipped"
        item = db.get(Item, item_id)
        draft = db.scalar(select(Draft).where(Draft.item_id == item_id))
        assets = BrandAssets(settings.data_dir)
        try:
            if not settings.public_base_url.startswith("https://"):
                raise InstagramError("Falta configurar PUBLIC_BASE_URL (o endereço https do sistema): o Instagram precisa baixar as imagens dele.")
            client = ig_account.load_client(db, settings, http, sleep=sleep)
            content = DraftContent.model_validate_json(draft.content)
            if not art_is_complete(assets, item_id, len(content.carousel)):
                generate_art(assets, item, draft, content, draft.photo or "auto")
                db.commit()
            urls = [publicurls.sign(settings.secret_key, settings.public_base_url, item_id, f"slide_{i:02d}.jpg")
                    for i in range(1, len(content.carousel) + 1)]
            result = client.publish_carousel(urls, build_full_caption(item, content))
        except UncertainPublish as exc:
            draft.publish_state = "uncertain"
            draft.publish_error = f"{exc} Confira no Instagram se o post saiu antes de tentar de novo."[:500]
            db.commit()
            log.error("publicacao incerta item=%s: %s", item_id, exc)
            return "uncertain"
        except Exception as exc:  # noqa: BLE001 - antes do ultimo passo e seguro tentar de novo
            draft.publish_state = "failed"
            draft.publish_error = str(exc)[:500]
            db.commit()
            log.warning("falha ao publicar item=%s: %s", item_id, exc)
            return "failed"

        draft.status, draft.scheduled_for, draft.published_at = DRAFT_PUBLISHED, None, utcnow()
        draft.publish_state, draft.publish_error = None, None
        draft.ig_media_id, draft.ig_permalink = result.media_id, result.permalink
        db.commit()

        if ig_account.get(db, "ig_publish_story", "1") == "1":  # o story e um extra: nao desfaz o post principal
            try:
                client.publish_story(publicurls.sign(settings.secret_key, settings.public_base_url, item_id, "story.jpg"))
            except Exception as exc:  # noqa: BLE001
                draft.publish_error = f"Post publicado. O story não saiu: {exc}"[:500]
                db.commit()
        return "published"
