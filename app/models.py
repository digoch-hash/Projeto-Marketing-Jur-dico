from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

STATUS_NEW = "new"
STATUS_POST = "post"
STATUS_LATER = "later"
STATUS_IGNORED = "ignored"
STATUSES = (STATUS_NEW, STATUS_POST, STATUS_LATER, STATUS_IGNORED)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Item(Base):
    """Uma norma ou ato publicado por uma fonte oficial."""

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_item_source_ext"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(1000))
    doc_type: Mapped[str] = mapped_column(String(100), default="")
    issuer: Mapped[str] = mapped_column(String(300), default="")
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    relevance: Mapped[int] = mapped_column(Integer, default=0, index=True)
    themes: Mapped[str] = mapped_column(String(300), default="")  # separados por virgula
    relevance_reason: Mapped[str] = mapped_column(String(500), default="")
    classified_by: Mapped[str] = mapped_column(String(16), default="rules")  # rules | claude

    status: Mapped[str] = mapped_column(String(16), default=STATUS_NEW, index=True)
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def theme_list(self) -> list[str]:
        return [t for t in self.themes.split(",") if t]


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))


class CollectRun(Base):
    """Historico de coletas, para a tela mostrar quando foi a ultima e se alguma fonte falhou."""

    __tablename__ = "collect_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(500), default="")


DRAFT_GENERATING = "generating"
DRAFT_READY = "draft"
DRAFT_APPROVED = "approved"
DRAFT_SCHEDULED = "scheduled"
DRAFT_PUBLISHED = "published"
DRAFT_ERROR = "error"


class Draft(Base):
    """Pacote de conteudo gerado a partir de um item: carrossel, reel, legenda e status do WhatsApp."""

    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default=DRAFT_GENERATING)
    content: Mapped[str] = mapped_column(Text, default="")  # JSON do DraftContent
    error: Mapped[str] = mapped_column(String(500), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    generations: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scheduled_for: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    photo: Mapped[str | None] = mapped_column(String(200), nullable=True)  # foto de fundo escolhida para as artes
