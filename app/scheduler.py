"""Rotina em segundo plano: coleta diaria, publicacao no horario e renovacao do token."""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timezone

from sqlalchemy import select

from app import ig_account
from app import autocard
from app.collector import run_default
from app.config import Settings
from app.editorial import _BRT
from app.models import DRAFT_SCHEDULED, Draft
from app.publisher import publish_draft

log = logging.getLogger("scheduler")
COLLECT_HOUR = 7


class Scheduler:
    def __init__(self, session_factory, settings: Settings, http=None, claude_client=None):
        self.session_factory, self.settings, self.http, self.claude_client = session_factory, settings, http, claude_client
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def tick(self, now: datetime | None = None) -> list[str]:
        """Uma passada. Devolve o que fez (para os testes e para o log)."""
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(_BRT)
        today = local.date()
        done: list[str] = []
        with self._lock:
            self._collect(local, done)
            self._refresh(now, done)
            self._publish(local, done)
        return done

    def _collect(self, local, done):
        with self.session_factory() as db:
            last = ig_account.get(db, "last_collect")
            if local.hour < COLLECT_HOUR:
                return
            if last and (local.date() - date.fromisoformat(last)).days < self.settings.collect_every_days:
                return
            ig_account.put(db, "last_collect", local.date().isoformat())  # marca antes: falha nao repete a cada minuto
            try:
                run_default(db, self.settings)
                done.append("collect")
            except Exception:  # noqa: BLE001
                log.exception("coleta diaria falhou")
                done.append("collect-failed")
                return
        try:
            result = autocard.run_after_collect(self.session_factory, self.settings, claude_client=self.claude_client)
            if result["ready"]:
                done.append(f"cards:{len(result['ready'])}")
            if result["alerted"]:
                done.append(f"alert:{result['alerted']}")
        except Exception:  # noqa: BLE001
            log.exception("cards/aviso automaticos falharam")
            done.append("cards-failed")

    def _refresh(self, now, done):
        with self.session_factory() as db:
            try:
                if ig_account.refresh_if_needed(db, self.settings, self.http, now=now.replace(tzinfo=None)):
                    done.append("token-refreshed")
            except Exception:  # noqa: BLE001
                log.exception("renovacao do token falhou")
                done.append("token-refresh-failed")

    def _publish(self, local, done):
        with self.session_factory() as db:
            if not ig_account.status(db)["auto_publish"] or not ig_account.is_connected(db):
                return
            if local.hour < self.settings.publish_hour:
                return
            ids = list(db.scalars(
                select(Draft.item_id).where(
                    Draft.status == DRAFT_SCHEDULED,
                    Draft.scheduled_for == local.date(),
                    Draft.publish_state.is_(None),
                ).order_by(Draft.id)
            ))
        for item_id in ids:  # um por vez; atrasados de dias anteriores NAO saem sozinhos (ver "Atrasados")
            done.append(f"publish:{item_id}:{publish_draft(self.session_factory, self.settings, item_id, http=self.http)}")

    # ----------------------------------------------------------------- thread
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        def loop():
            while not self._stop.wait(60):
                try:
                    actions = self.tick()
                    if actions:
                        log.info("agendador: %s", actions)
                except Exception:  # noqa: BLE001 - o agendador nunca pode morrer
                    log.exception("falha no agendador")

        self._stop.clear()
        self._thread = threading.Thread(target=loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
