import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app import autocard, ig_account, notify, scheduler as sched_mod
from app.brand_assets import BrandAssets
from app.models import Draft, Item
from app.scheduler import Scheduler
from tests.test_drafts import FakeClaude, FakeResponse, VALID, ok_client
from tests.test_notify import FakeSMTP, factory


@pytest.fixture(autouse=True)
def fast_art(monkeypatch):
    def tiny(assets, item, draft, content, photo="auto"):
        names = {f"slide_{i:02d}": Image.new("RGB", (8, 10)) for i in range(1, len(content.carousel) + 1)}
        names["story"] = Image.new("RGB", (8, 14))
        return assets.save_art(item.id, names)

    monkeypatch.setattr(autocard, "generate_art", tiny)


@pytest.fixture
def cfg(settings):
    return replace(
        settings, anthropic_api_key="sk-teste", alert_min_relevance=60, auto_draft_max_per_day=3,
        smtp_host="smtp.x", smtp_user="radar@hrbio.com.br", smtp_password="pw", alert_emails="rodrigo@hrbio.com.br",
        public_base_url="https://app.exemplo.com",
    )


def http():
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"conteudo": "<p>PORTARIA</p><p>Texto completo do ato</p>"})))


def add(db, n, relevance, status="new", **kw):
    item = Item(source="doe_rs", external_id=str(n), title=f"Norma {n}", url=f"https://doe/{n}", relevance=relevance,
                status=status, published_at=date(2026, 10, 1), **kw)
    db.add(item)
    db.commit()
    return item.id


def draft_status(session_factory, item_id):
    with session_factory() as db:
        d = db.scalar(select(Draft).where(Draft.item_id == item_id))
        return d.status if d else None


# ------------------------------------------------------------------ cards
def test_gera_card_so_do_muito_relevante_novo_e_sem_rascunho(session_factory, cfg):
    with session_factory() as db:
        top, mid = add(db, 1, 90), add(db, 2, 70)
        low, ignored, done = add(db, 3, 59), add(db, 4, 95, status="ignored"), add(db, 5, 80)
        db.add(Draft(item_id=done, status="draft", content="{}"))
        db.commit()
    ready = autocard.prepare_cards(session_factory, cfg, claude_client=ok_client(), http_client=http())
    assert ready == [top, mid]  # do mais relevante para o menos
    assert draft_status(session_factory, top) == "draft" and draft_status(session_factory, mid) == "draft"
    assert draft_status(session_factory, low) is None and draft_status(session_factory, ignored) is None
    assert BrandAssets(cfg.data_dir).art_path(top, "slide_01.png")  # as artes tambem ficam prontas


def test_teto_diario_limita_gasto_e_nao_repete_no_mesmo_dia(session_factory, cfg):
    cfg = replace(cfg, auto_draft_max_per_day=2)
    with session_factory() as db:
        ids = [add(db, n, 90 - n) for n in range(1, 6)]
    claude = ok_client()
    assert autocard.prepare_cards(session_factory, cfg, claude_client=claude, http_client=http()) == ids[:2]
    assert autocard.prepare_cards(session_factory, cfg, claude_client=claude, http_client=http()) == []  # teto do dia atingido
    assert len(claude.calls) == 2
    assert draft_status(session_factory, ids[2]) is None


def test_falha_do_claude_nao_trava_os_outros_e_ainda_gasta_do_teto(session_factory, cfg):
    with session_factory() as db:
        bad, good = add(db, 1, 95), add(db, 2, 80)
    answers = [FakeResponse(None, stop_reason="refusal"), FakeResponse(json.dumps(VALID))]

    class Seq(FakeClaude):
        def create(self, **kw):
            self.calls.append(kw)
            return answers.pop(0)

    ready = autocard.prepare_cards(session_factory, cfg, claude_client=Seq(), http_client=http())
    assert ready == [good] and draft_status(session_factory, bad) == "error"
    with session_factory() as db:
        assert ig_account.get(db, f"auto_drafts:{datetime.now(timezone(timedelta(hours=-3))).date().isoformat()}") == "2"


def test_sem_chave_ou_desligado_nao_gera_nada(session_factory, cfg):
    with session_factory() as db:
        add(db, 1, 99)
    assert autocard.prepare_cards(session_factory, replace(cfg, anthropic_api_key=None), http_client=http()) == []
    assert autocard.prepare_cards(session_factory, replace(cfg, auto_draft=False), claude_client=ok_client(), http_client=http()) == []
    assert autocard.prepare_cards(session_factory, replace(cfg, auto_draft_max_per_day=0), claude_client=ok_client(), http_client=http()) == []
    assert draft_status(session_factory, 1) is None


# --------------------------------------------------------------------- aviso
def test_email_diz_quais_cards_estao_prontos_e_leva_ao_rascunho(session_factory, cfg):
    FakeSMTP.instances.clear()
    cfg = replace(cfg, auto_draft_max_per_day=1)
    with session_factory() as db:
        with_card, without = add(db, 1, 90), add(db, 2, 80)
    result = autocard.run_after_collect(session_factory, cfg, claude_client=ok_client(), http_client=http(), smtp_factory=factory())
    assert result == {"ready": [with_card], "alerted": 2}
    msg = FakeSMTP.instances[0].sent[0]
    body = msg.get_content()
    assert msg["Subject"] == "HRBio Radar: 2 novidades relevantes (1 card pronto para revisar)"
    assert f"✅ Card pronto para você revisar: https://app.exemplo.com/items/{with_card}/draft" in body
    assert f"https://app.exemplo.com/items/{without}\n" in body  # a que ficou sem card (teto) leva a pagina normal
    assert "Nada é publicado sem a sua aprovação" in body


def test_sem_novidade_relevante_nao_gera_card_nem_manda_email(session_factory, cfg):
    FakeSMTP.instances.clear()
    with session_factory() as db:
        add(db, 1, 40)
    claude = ok_client()
    assert autocard.run_after_collect(session_factory, cfg, claude_client=claude, http_client=http(), smtp_factory=factory()) == {"ready": [], "alerted": 0}
    assert claude.calls == [] and FakeSMTP.instances == []


def test_sem_chave_ainda_avisa_so_que_sem_card(session_factory, cfg):
    FakeSMTP.instances.clear()
    with session_factory() as db:
        add(db, 1, 90)
    result = autocard.run_after_collect(session_factory, replace(cfg, anthropic_api_key=None), smtp_factory=factory())
    assert result == {"ready": [], "alerted": 1}
    assert "card" not in FakeSMTP.instances[0].sent[0]["Subject"]


# ----------------------------------------------------------------- agendador
def morning(day=date(2026, 10, 5)):
    return datetime(day.year, day.month, day.day, 10, 5, tzinfo=timezone.utc)  # 07:05 em Brasilia


def test_agendador_coleta_gera_card_e_avisa_uma_vez(session_factory, cfg, monkeypatch):
    monkeypatch.setattr(sched_mod, "run_default", lambda db, st: [])
    monkeypatch.setattr(autocard, "run_draft_job", __import__("app.drafts", fromlist=["x"]).run_draft_job)
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda st, subj, body, f=None: sent.append(subj))
    with session_factory() as db:
        item_id = add(db, 1, 90)
    s = Scheduler(session_factory, cfg, claude_client=ok_client())
    monkeypatch.setattr("app.drafts.make_client", lambda *a, **k: http())
    done = s.tick(morning())
    assert "collect" in done and "cards:1" in done and "alert:1" in done
    assert draft_status(session_factory, item_id) == "draft" and sent == ["HRBio Radar: 1 novidade relevante (1 card pronto para revisar)"]
    assert not [a for a in s.tick(morning() + timedelta(minutes=1)) if a.startswith(("collect", "cards", "alert"))]  # nao repete no mesmo dia


def test_coleta_a_cada_dois_dias(session_factory, cfg, monkeypatch):
    calls = []
    monkeypatch.setattr(sched_mod, "run_default", lambda db, st: calls.append(1) or [])
    s = Scheduler(session_factory, replace(cfg, collect_every_days=2, anthropic_api_key=None))
    d = date(2026, 10, 5)
    assert "collect" in s.tick(morning(d))
    assert "collect" not in s.tick(morning(d + timedelta(days=1)))  # dia seguinte: pula
    assert "collect" in s.tick(morning(d + timedelta(days=2)))
    assert len(calls) == 2


def test_falha_na_geracao_dos_cards_nao_derruba_o_agendador(session_factory, cfg, monkeypatch):
    monkeypatch.setattr(sched_mod, "run_default", lambda db, st: [])

    def boom(*a, **k):
        raise RuntimeError("quebrou")

    monkeypatch.setattr(autocard, "run_after_collect", boom)
    done = Scheduler(session_factory, cfg).tick(morning())
    assert "collect" in done and "cards-failed" in done
