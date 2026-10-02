"""Roda as fontes, filtra por relevancia e grava no banco sem duplicar."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import CollectRun, Item
from app.relevance import CLAUDE_MIN, MIN_STORE, ClaudeClassifier, score_item
from app.sources.base import Source, SourceError, make_client
from app.sources.consema import ConsemaSource
from app.sources.doe_rs import DoeRsSource
from app.sources.fepam import FepamSource


@dataclass
class RunResult:
    source: str
    fetched: int = 0
    created: int = 0
    error: str = ""


def default_sources(settings: Settings) -> list[Source]:
    client = make_client(settings.user_agent)
    return [FepamSource(client), ConsemaSource(client), DoeRsSource(client)]


def collect(
    session: Session,
    sources: list[Source],
    since: date,
    classifier: ClaudeClassifier | None = None,
) -> list[RunResult]:
    results = []
    for source in sources:
        res = RunResult(source=source.name)
        try:
            raw_items = source.fetch(since)
        except SourceError as exc:
            res.error = str(exc)[:500]
            raw_items = []
        res.fetched = len(raw_items)

        for raw in raw_items:
            exists = session.scalar(
                select(Item.id).where(Item.source == raw.source, Item.external_id == raw.external_id)
            )
            if exists:
                continue
            cls = score_item(raw)
            if cls.value < MIN_STORE:
                continue
            if classifier and cls.value >= CLAUDE_MIN:
                cls = classifier.classify(raw, cls)
            session.add(
                Item(
                    source=raw.source,
                    external_id=raw.external_id,
                    title=raw.title,
                    summary=raw.summary,
                    url=raw.url,
                    doc_type=raw.doc_type,
                    issuer=raw.issuer,
                    published_at=raw.published_at,
                    relevance=cls.value,
                    themes=",".join(cls.themes),
                    relevance_reason=cls.reason,
                    classified_by=cls.by,
                )
            )
            res.created += 1
        session.add(CollectRun(source=res.source, fetched=res.fetched, created=res.created, error=res.error))
        session.commit()
        results.append(res)
    return results


def run_default(session: Session, settings: Settings, days: int | None = None) -> list[RunResult]:
    classifier = None
    if settings.anthropic_api_key:
        classifier = ClaudeClassifier(settings.anthropic_api_key, settings.anthropic_model)
    since = date.today() - timedelta(days=days if days is not None else settings.lookback_days)
    return collect(session, default_sources(settings), since, classifier)
