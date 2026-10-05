"""Aviso por e-mail: so quando aparece algo muito relevante (silencio nos outros dias)."""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage

from sqlalchemy import select

from app import ig_account
from app.config import Settings
from app.models import STATUS_NEW, Item, utcnow

log = logging.getLogger("notify")
MAX_LISTED = 10
SOURCE_NAMES = {"doe_rs": "Diário Oficial RS", "consema": "CONSEMA", "fepam": "FEPAM"}


def recipients(settings: Settings) -> list[str]:
    return [e.strip() for e in settings.alert_emails.replace(";", ",").split(",") if "@" in e]


def is_configured(settings: Settings) -> bool:
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password and recipients(settings))


def send_email(settings: Settings, subject: str, body: str, smtp_factory=None) -> None:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, settings.smtp_user, ", ".join(recipients(settings))
    msg.set_content(body)
    if smtp_factory is None:
        smtp_factory = (lambda: smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30)) \
            if settings.smtp_port == 465 else (lambda: smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30))
    with smtp_factory() as smtp:
        if settings.smtp_port != 465 and hasattr(smtp, "starttls"):
            smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


def build_message(settings: Settings, items: list[Item], ready: set[int] | None = None) -> tuple[str, str]:
    ready = ready or set()
    n = len(items)
    subject = f"HRBio Radar: {n} novidade{'s' if n != 1 else ''} relevante{'s' if n != 1 else ''}"
    cards = sum(1 for it in items if it.id in ready)
    if cards:
        subject += f" ({cards} card{'s' if cards != 1 else ''} pronto{'s' if cards != 1 else ''} para revisar)"
    base = settings.public_base_url
    lines = [f"O Radar encontrou {n} norma{'s' if n != 1 else ''} muito relevante{'s' if n != 1 else ''} para os clientes da HRBio:", ""]
    for it in items[:MAX_LISTED]:
        when = it.published_at.strftime("%d/%m/%Y") if it.published_at else "sem data"
        lines += [f"• {it.title}", f"  {SOURCE_NAMES.get(it.source, it.source)} · {when} · relevância {it.relevance}"]
        if it.relevance_reason:
            lines.append(f"  {it.relevance_reason}")
        if it.id in ready:
            lines += [f"  ✅ Card pronto para você revisar: {base}/items/{it.id}/draft" if base else "  ✅ Card pronto para você revisar", ""]
        else:
            lines += [f"  {base}/items/{it.id}" if base else "", ""]
    if n > MAX_LISTED:
        lines += [f"... e mais {n - MAX_LISTED} no sistema.", ""]
    lines += [f"Abrir o sistema: {base}" if base else "", "",
              "Nada é publicado sem a sua aprovação.",
              "Você só recebe este e-mail quando aparece algo muito relevante. Sem novidade, sem e-mail."]
    return subject, "\n".join(lines)


def alert_new_relevant(db, settings: Settings, now: datetime | None = None, smtp_factory=None,
                       ready: set[int] | None = None) -> int | None:
    """Envia um e-mail com as novidades muito relevantes desde o ultimo aviso.

    Devolve quantas foram avisadas, 0 se nao havia nada (nao envia nada) ou None se o e-mail nao esta configurado
    ou falhou (nesse caso o marcador nao avanca: as novidades entram no proximo aviso).
    """
    if not is_configured(settings):
        return None
    now = now or utcnow()
    last = datetime.fromisoformat(ig_account.get(db, "last_alert", "2000-01-01T00:00:00"))
    items = list(db.scalars(
        select(Item).where(
            Item.first_seen_at > last, Item.status == STATUS_NEW, Item.relevance >= settings.alert_min_relevance
        ).order_by(Item.relevance.desc(), Item.id)
    ))
    if not items:
        ig_account.put(db, "last_alert", now.isoformat())
        return 0
    subject, body = build_message(settings, items, ready)
    try:
        send_email(settings, subject, body, smtp_factory)
    except Exception:  # noqa: BLE001 - senha errada, rede... nao pode derrubar a rotina
        log.exception("falha ao enviar o aviso por e-mail")
        return None
    ig_account.put(db, "last_alert", now.isoformat())
    return len(items)
