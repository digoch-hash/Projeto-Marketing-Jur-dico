"""Calendario editorial: ritmo de postagem (dia sim, dia nao) e agenda dos proximos dias."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.models import DRAFT_PUBLISHED, DRAFT_SCHEDULED, Draft, Item

MIN_GAP_DAYS = 2  # 2 = dia sim, dia nao (um dia de intervalo entre posts)
WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
_BRT = timezone(timedelta(hours=-3))  # o Brasil nao tem horario de verao desde 2019


def today_br(now: datetime | None = None) -> date:
    """Data de hoje em Brasilia (o servidor roda em UTC, e depois das 21h ja seria 'amanha')."""
    return (now or datetime.now(timezone.utc)).astimezone(_BRT).date()


def brt_date(naive_utc: datetime) -> date:
    """Data em Brasilia de um datetime gravado em UTC (sem fuso) no banco."""
    return (naive_utc + timedelta(hours=-3)).date()


def conflicts(day: date, taken: set[date], gap: int = MIN_GAP_DAYS) -> bool:
    """True se `day` ficaria a menos de `gap` dias de outro post."""
    return any(abs((day - t).days) < gap for t in taken)


def suggest_date(taken: set[date], today: date, gap: int = MIN_GAP_DAYS) -> date:
    """Primeiro dia a partir de hoje que respeita o ritmo em relacao aos posts ja marcados."""
    day = today
    while conflicts(day, taken, gap):
        day += timedelta(days=1)
    return day


def taken_dates(db, exclude_item_id: int | None = None) -> set[date]:
    """Dias com post agendado, ou publicado (publicados contam para o intervalo)."""
    q = select(Draft.scheduled_for, Draft.published_at, Draft.item_id, Draft.status).where(
        Draft.status.in_((DRAFT_SCHEDULED, DRAFT_PUBLISHED))
    )
    days: set[date] = set()
    for scheduled_for, published_at, item_id, status in db.execute(q):
        if item_id == exclude_item_id:
            continue
        if status == DRAFT_SCHEDULED and scheduled_for:
            days.add(scheduled_for)
        elif status == DRAFT_PUBLISHED and published_at:
            days.add(brt_date(published_at))
    return days


@dataclass
class AgendaDay:
    day: date
    kind: str  # "post" | "free" | "rest"
    posts: list[tuple[Draft, Item]] = field(default_factory=list)
    is_today: bool = False

    @property
    def label(self) -> str:
        return f"{WEEKDAYS[self.day.weekday()]} {self.day.strftime('%d/%m')}"


def build_agenda(db, today: date, days: int = 14, gap: int = MIN_GAP_DAYS) -> list[AgendaDay]:
    """Proximos `days` dias: o que esta agendado, quais dias estao livres para postar e quais sao de descanso."""
    end = today + timedelta(days=days - 1)
    rows = db.execute(
        select(Draft, Item)
        .join(Item, Item.id == Draft.item_id)
        .where(Draft.status == DRAFT_SCHEDULED, Draft.scheduled_for >= today, Draft.scheduled_for <= end)
        .order_by(Draft.scheduled_for, Draft.id)
    ).all()
    by_day: dict[date, list] = {}
    for draft, item in rows:
        by_day.setdefault(draft.scheduled_for, []).append((draft, item))

    taken = taken_dates(db)
    agenda = []
    free_taken = set(taken)  # dias "livres" sugeridos nao devem ficar colados um no outro
    for i in range(days):
        day = today + timedelta(days=i)
        if day in by_day:
            kind = "post"
        elif not conflicts(day, free_taken, gap):
            kind = "free"
            free_taken.add(day)
        else:
            kind = "rest"
        agenda.append(AgendaDay(day=day, kind=kind, posts=by_day.get(day, []), is_today=(i == 0)))
    return agenda
