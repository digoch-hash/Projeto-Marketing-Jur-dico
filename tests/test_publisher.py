import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from PIL import Image

from app import ig_account, publicurls, publisher, scheduler as sched_mod
from app.artservice import generate_art as REAL_GENERATE_ART
from app.instagram import InstagramError
from app.models import Draft, Item, Setting, utcnow
from app.publisher import claim, is_stuck, mark_published_by_hand, publish_draft, release_uncertain
from app.scheduler import Scheduler
from tests.fake_instagram import FakeInstagram
from tests.test_drafts import VALID

TOKEN = "TOKEN-BOM-" + "x" * 30
TODAY = date(2026, 10, 5)


@pytest.fixture(autouse=True)
def fast_art(monkeypatch):
    """Imagens minimas em vez das 7 artes completas (1 s cada): so um teste usa a geracao real."""

    def tiny(assets, item, draft, content, photo="auto"):
        names = {f"slide_{i:02d}": Image.new("RGB", (8, 10)) for i in range(1, len(content.carousel) + 1)}
        names["story"] = Image.new("RGB", (8, 14))
        return assets.save_art(item.id, names)

    monkeypatch.setattr(publisher, "generate_art", tiny)


@pytest.fixture
def cfg(settings):
    return replace(settings, public_base_url="https://app.exemplo.com", publish_hour=9)


@pytest.fixture
def fake():
    return FakeInstagram()


@pytest.fixture
def connected(session_factory, cfg, fake):
    with session_factory() as db:
        ig_account.connect(db, cfg, TOKEN, http=fake.client())
    fake.calls.clear()
    return fake


def add_post(session_factory, status="scheduled", day=TODAY, n=1):
    with session_factory() as db:
        item = Item(source="consema", external_id=str(n), title=f"Resolução CONSEMA {n}/2026", url="https://x/y.pdf", relevance=80)
        db.add(item)
        db.flush()
        db.add(Draft(item_id=item.id, status=status, content=json.dumps(VALID), scheduled_for=day if status == "scheduled" else None))
        db.commit()
        return item.id


def draft_of(session_factory, item_id):
    with session_factory() as db:
        d = db.scalar(select(Draft).where(Draft.item_id == item_id))
        db.expunge(d)
        return d


def run(session_factory, cfg, item_id, fake):
    return publish_draft(session_factory, cfg, item_id, http=fake.client(), sleep=lambda s: None)


# ------------------------------------------------------------------- conta
def test_conectar_valida_na_meta_e_guarda_o_token_criptografado(session_factory, cfg, fake):
    with session_factory() as db:
        assert ig_account.connect(db, cfg, TOKEN, http=fake.client()) == "hrbioambiental"
        raw = db.get(Setting, "ig_token").value
        assert TOKEN not in raw and "TOKEN" not in raw  # nunca em texto puro
        st = ig_account.status(db)
        assert st["connected"] and st["username"] == "hrbioambiental" and 59 <= st["days_left"] <= 60
        assert st["auto_publish"] is False  # nasce desligado: voce testa antes
        assert ig_account.load_client(db, cfg).token == TOKEN


def test_conectar_recusa_token_curto_ou_invalido_sem_guardar_nada(session_factory, cfg, fake):
    with session_factory() as db:
        with pytest.raises(InstagramError, match="curto"):
            ig_account.connect(db, cfg, "abc", http=fake.client())
        with pytest.raises(InstagramError) as exc:
            ig_account.connect(db, cfg, "TOKEN-VENCIDO-" + "x" * 20, http=fake.client())
        assert exc.value.token_invalid and not ig_account.is_connected(db)


def test_desconectar_apaga_o_token(session_factory, cfg, connected):
    with session_factory() as db:
        ig_account.disconnect(db)
        assert not ig_account.is_connected(db) and db.get(Setting, "ig_token") is None
        with pytest.raises(InstagramError, match="não está conectado"):
            ig_account.load_client(db, cfg)


def test_token_so_e_renovado_quando_faltam_menos_de_25_dias_e_passou_um_dia(session_factory, cfg, connected):
    now = utcnow()
    with session_factory() as db:
        assert ig_account.refresh_if_needed(db, cfg, connected.client(), now=now) is False  # acabou de conectar
        ig_account.put(db, "ig_token_expires", (now + timedelta(days=40)).isoformat())
        ig_account.put(db, "ig_token_refreshed", (now - timedelta(days=20)).isoformat())
        assert ig_account.refresh_if_needed(db, cfg, connected.client(), now=now) is False  # ainda longe de vencer
        ig_account.put(db, "ig_token_expires", (now + timedelta(days=10)).isoformat())
        ig_account.put(db, "ig_token_refreshed", (now - timedelta(hours=2)).isoformat())
        assert ig_account.refresh_if_needed(db, cfg, connected.client(), now=now) is False  # menos de 24 h
        ig_account.put(db, "ig_token_refreshed", (now - timedelta(days=3)).isoformat())
        assert ig_account.refresh_if_needed(db, cfg, connected.client(), now=now) is True
        assert ig_account.load_client(db, cfg).token.startswith("NOVO-TOKEN")
        assert ig_account.status(db, now)["days_left"] >= 59


# ------------------------------------------------------- login do Facebook (Pagina)
@pytest.fixture
def fb_cfg(cfg):
    return replace(cfg, facebook_app_id="123456", facebook_app_secret="segredo-do-app")


def test_conectar_pelo_facebook_guarda_o_token_da_pagina_que_nao_vence(session_factory, fb_cfg, fake):
    with session_factory() as db:
        assert ig_account.connect(db, fb_cfg, TOKEN, http=fake.client()) == "hrbioambiental"
        trocou = next(c for c in fake.calls if c["path"].endswith("/oauth/access_token"))
        assert trocou["params"]["client_id"] == "123456" and trocou["params"]["client_secret"] == "segredo-do-app"
        assert trocou["params"]["fb_exchange_token"] == TOKEN
        raw = db.get(Setting, "ig_token").value
        assert "PAGINA-TOKEN" not in raw and TOKEN not in raw  # nunca em texto puro
        st = ig_account.status(db)
        assert st["connected"] and st["facebook_mode"] and st["page_name"] == "HRBio Ambiental"
        assert st["days_left"] is None and st["auto_publish"] is False
        client = ig_account.load_client(db, fb_cfg)
        assert client.token.startswith("PAGINA-TOKEN") and client.ig_user_id == "1784140000"
        assert client.host == "https://graph.facebook.com"
        assert ig_account.refresh_if_needed(db, fb_cfg, fake.client()) is False  # nao ha o que renovar


def test_publica_pelo_host_do_facebook(session_factory, fb_cfg, fake):
    with session_factory() as db:
        ig_account.connect(db, fb_cfg, TOKEN, http=fake.client())
        fake.calls.clear()
        client = ig_account.load_client(db, fb_cfg, fake.client(), sleep=lambda s: None)
    client.publish_carousel(["https://x/1.jpg", "https://x/2.jpg"], "legenda")
    assert fake.calls and {c["host"] for c in fake.calls} == {"graph.facebook.com"}
    assert fake.posts("/media_publish")


def test_conectar_pelo_facebook_sem_pagina_com_instagram_nao_guarda_nada(session_factory, fb_cfg, fake):
    fake.pages = [{"name": "Outra Página", "access_token": "P" * 25}]  # sem Instagram ligado
    with session_factory() as db:
        with pytest.raises(InstagramError, match="Página"):
            ig_account.connect(db, fb_cfg, TOKEN, http=fake.client())
        assert not ig_account.is_connected(db)


def test_conectar_pelo_facebook_recusa_token_vencido(session_factory, fb_cfg, fake):
    with session_factory() as db:
        with pytest.raises(InstagramError) as exc:
            ig_account.connect(db, fb_cfg, "TOKEN-VENCIDO-" + "x" * 20, http=fake.client())
        assert exc.value.token_invalid and not ig_account.is_connected(db)


def test_reconectar_pelo_instagram_volta_ao_host_do_instagram(session_factory, cfg, fb_cfg, fake):
    with session_factory() as db:
        ig_account.connect(db, fb_cfg, TOKEN, http=fake.client())
        ig_account.connect(db, cfg, TOKEN, http=fake.client())
        assert ig_account.load_client(db, cfg).host == "https://graph.instagram.com"
        assert not ig_account.status(db)["facebook_mode"]
        ig_account.disconnect(db)
        assert db.get(Setting, "ig_mode") is None


# --------------------------------------------------------------- publicacao
def test_publica_carrossel_e_story_e_fecha_o_ciclo(session_factory, cfg, connected):
    item_id = add_post(session_factory)
    assert run(session_factory, cfg, item_id, connected) == "published"
    d = draft_of(session_factory, item_id)
    assert d.status == "published" and d.published_at and d.scheduled_for is None and d.publish_state is None
    assert d.ig_media_id.startswith("MEDIA") and d.ig_permalink == "https://www.instagram.com/p/ABC123/"
    assert len(connected.posts("/media_publish")) == 2  # carrossel + story
    stories = [c for c in connected.posts("/media") if c["params"].get("media_type") == "STORIES"]
    assert len(stories) == 1
    # todas as imagens usam link assinado do proprio sistema, valido e em JPEG
    for call in [c for c in connected.posts("/media") if "image_url" in c["params"]]:  # (o container do carrossel so tem os filhos)
        url = call["params"]["image_url"]
        assert url.startswith("https://app.exemplo.com/pub/") and url.endswith(".jpg")
        _, _, _, _, iid, exp, sig, name = url.split("/")
        assert publicurls.verify(cfg.secret_key, int(iid), int(exp), sig, name)
    caption = [c for c in connected.posts("/media") if c["params"].get("media_type") == "CAROUSEL"][0]["params"]["caption"]
    assert "Fonte oficial: Resolução CONSEMA 1/2026" in caption and "https://x/y.pdf" in caption


def test_gera_as_artes_sozinho_quando_faltam(session_factory, cfg, connected, monkeypatch):
    from app.brand_assets import BrandAssets

    monkeypatch.setattr(publisher, "generate_art", REAL_GENERATE_ART)  # este usa a geracao real, de ponta a ponta
    item_id = add_post(session_factory)
    assets = BrandAssets(cfg.data_dir)
    assert assets.list_art(item_id) == []
    run(session_factory, cfg, item_id, connected)
    assert assets.art_jpg_path(item_id, "slide_01.jpg") and assets.art_jpg_path(item_id, "story.jpg")


def test_story_desligado_nao_publica_story(session_factory, cfg, connected):
    with session_factory() as db:
        ig_account.put(db, "ig_publish_story", "0")
    run(session_factory, cfg, add_post(session_factory), connected)
    assert len(connected.posts("/media_publish")) == 1


def test_falha_no_story_nao_desfaz_o_post_principal(session_factory, cfg, connected):
    connected.fail_on["story"] = httpx.Response(400, json={"error": {"message": "Story recusado", "code": 100}})
    item_id = add_post(session_factory)
    assert run(session_factory, cfg, item_id, connected) == "published"
    d = draft_of(session_factory, item_id)
    assert d.status == "published" and "story não saiu" in d.publish_error.lower()


def test_falha_antes_do_fim_mantem_agendado_e_tentar_de_novo_nao_duplica(session_factory, cfg, connected):
    item_id = add_post(session_factory)
    connected.fail_on["media"] = httpx.Response(400, json={"error": {"message": "URL inacessível", "code": 9004}})
    assert run(session_factory, cfg, item_id, connected) == "failed"
    d = draft_of(session_factory, item_id)
    assert d.status == "scheduled" and d.publish_state == "failed" and "inacessível" in d.publish_error
    assert connected.posts("/media_publish") == []  # nada foi ao ar
    del connected.fail_on["media"]
    assert run(session_factory, cfg, item_id, connected) == "published"
    assert len([c for c in connected.posts("/media_publish")]) == 2  # 1 carrossel + 1 story: so uma vez cada


def test_resposta_perdida_no_ultimo_passo_vira_incerto_e_nunca_tenta_sozinho(session_factory, cfg, connected):
    item_id = add_post(session_factory)
    connected.fail_on["media_publish"] = httpx.ReadTimeout("sem resposta")
    assert run(session_factory, cfg, item_id, connected) == "uncertain"
    d = draft_of(session_factory, item_id)
    assert d.status == "scheduled" and d.publish_state == "uncertain" and "Confira no Instagram" in d.publish_error
    del connected.fail_on["media_publish"]
    before = len(connected.calls)
    assert run(session_factory, cfg, item_id, connected) == "skipped"  # nao repete sozinho: poderia duplicar
    assert len(connected.calls) == before
    with session_factory() as db:  # so o usuario destrava, depois de conferir no Instagram
        release_uncertain(db, item_id)
    assert run(session_factory, cfg, item_id, connected) == "published"


def test_usuario_confirma_que_o_post_saiu(session_factory, cfg, connected):
    item_id = add_post(session_factory)
    connected.fail_on["media_publish"] = httpx.ReadTimeout("x")
    run(session_factory, cfg, item_id, connected)
    with session_factory() as db:
        mark_published_by_hand(db, item_id)
    d = draft_of(session_factory, item_id)
    assert d.status == "published" and d.publish_state is None and d.scheduled_for is None


def test_so_uma_execucao_ganha_a_reserva(session_factory, cfg, connected):
    item_id = add_post(session_factory)
    with session_factory() as a, session_factory() as b:
        assert claim(a, item_id) is True
        assert claim(b, item_id) is False  # segunda tentativa simultanea (clique duplo, tick do agendador)
    assert run(session_factory, cfg, item_id, connected) == "skipped"
    assert connected.calls == []


def test_so_publica_o_que_foi_aprovado(session_factory, cfg, connected):
    assert run(session_factory, cfg, add_post(session_factory, status="draft"), connected) == "skipped"
    assert connected.calls == []
    assert run(session_factory, cfg, add_post(session_factory, status="approved", n=2), connected) == "published"  # sem data: "publicar agora"


def test_sem_endereco_https_ou_sem_conexao_falha_com_mensagem_clara(session_factory, cfg, connected):
    item_id = add_post(session_factory)
    assert run(session_factory, replace(cfg, public_base_url="http://localhost:8000"), item_id, connected) == "failed"
    assert "PUBLIC_BASE_URL" in draft_of(session_factory, item_id).publish_error
    with session_factory() as db:
        ig_account.disconnect(db)
    assert run(session_factory, cfg, item_id, connected) == "failed"
    assert "não está conectado" in draft_of(session_factory, item_id).publish_error


def test_publicacao_travada_e_detectada():
    d = Draft(item_id=1, publish_state="publishing", publish_started_at=utcnow() - timedelta(minutes=20))
    assert is_stuck(d) and not is_stuck(Draft(item_id=1, publish_state="publishing", publish_started_at=utcnow()))


# ---------------------------------------------------------------- agendador
def at(hour, minute=5, day=TODAY):
    """Horario de Brasilia (UTC-3) como datetime UTC."""
    return datetime(day.year, day.month, day.day, hour + 3, minute, tzinfo=timezone.utc)


def make_scheduler(session_factory, cfg, fake, monkeypatch, collect_calls=None):
    monkeypatch.setattr(sched_mod, "run_default", lambda db, st: (collect_calls.append(1) if collect_calls is not None else None) or [])
    return Scheduler(session_factory, cfg, http=fake.client())


def enable_auto(session_factory, on=True):
    with session_factory() as db:
        ig_account.put(db, "ig_auto_publish", "1" if on else "0")


def test_agendador_publica_no_dia_marcado_so_depois_do_horario(session_factory, cfg, connected, monkeypatch):
    enable_auto(session_factory)
    item_id = add_post(session_factory)
    s = make_scheduler(session_factory, cfg, connected, monkeypatch)
    assert [a for a in s.tick(at(8, 55)) if a.startswith("publish")] == []  # antes das 9h
    assert connected.posts("/media_publish") == []
    done = [a for a in s.tick(at(9, 5)) if a.startswith("publish")]
    assert done == [f"publish:{item_id}:published"]
    assert [a for a in s.tick(at(9, 6)) if a.startswith("publish")] == []  # ja saiu: nao repete


def test_agendador_respeita_o_interruptor_e_a_conexao(session_factory, cfg, fake, monkeypatch):
    add_post(session_factory)
    s = make_scheduler(session_factory, cfg, fake, monkeypatch)
    assert [a for a in s.tick(at(10)) if a.startswith("publish")] == []  # sem conexao
    with session_factory() as db:
        ig_account.connect(db, cfg, TOKEN, http=fake.client())
    assert [a for a in s.tick(at(10)) if a.startswith("publish")] == []  # conectado, mas automatico desligado
    assert fake.posts("/media_publish") == []


def test_agendador_nao_publica_atrasados_nem_dias_futuros(session_factory, cfg, connected, monkeypatch):
    enable_auto(session_factory)
    add_post(session_factory, day=TODAY - timedelta(days=1), n=1)  # esqueceu de ligar ontem: decide o usuario
    add_post(session_factory, day=TODAY + timedelta(days=1), n=2)
    s = make_scheduler(session_factory, cfg, connected, monkeypatch)
    assert [a for a in s.tick(at(12)) if a.startswith("publish")] == []


def test_um_post_com_falha_nao_impede_os_outros_do_dia(session_factory, cfg, connected, monkeypatch):
    enable_auto(session_factory)
    first = add_post(session_factory, n=1)
    second = add_post(session_factory, n=2)
    connected.fail_on["media"] = httpx.Response(400, json={"error": {"message": "falhou", "code": 100}})
    s = make_scheduler(session_factory, cfg, connected, monkeypatch)
    done = s.tick(at(9, 10))
    assert f"publish:{first}:failed" in done and f"publish:{second}:failed" in done  # ambos tentados, nenhum derrubou o agendador


def test_coleta_diaria_roda_uma_vez_por_dia_depois_das_7h(session_factory, cfg, fake, monkeypatch):
    calls = []
    s = make_scheduler(session_factory, cfg, fake, monkeypatch, calls)
    assert "collect" not in s.tick(at(6, 50))
    assert "collect" in s.tick(at(7, 5)) and len(calls) == 1
    assert "collect" not in s.tick(at(7, 6)) and "collect" not in s.tick(at(15)) and len(calls) == 1
    assert "collect" in s.tick(at(7, 5, day=TODAY + timedelta(days=1))) and len(calls) == 2


def test_coleta_que_falha_nao_derruba_nem_repete_a_cada_minuto(session_factory, cfg, fake, monkeypatch):
    def boom(db, st):
        raise RuntimeError("fonte fora do ar")

    monkeypatch.setattr(sched_mod, "run_default", boom)
    s = Scheduler(session_factory, cfg, http=fake.client())
    assert "collect-failed" in s.tick(at(7, 5))
    assert "collect-failed" not in s.tick(at(7, 6))


def test_agendador_renova_o_token_quando_precisa(session_factory, cfg, connected, monkeypatch):
    with session_factory() as db:
        ig_account.put(db, "ig_token_expires", (utcnow() + timedelta(days=5)).isoformat())
        ig_account.put(db, "ig_token_refreshed", (utcnow() - timedelta(days=30)).isoformat())
    s = make_scheduler(session_factory, cfg, connected, monkeypatch)
    assert "token-refreshed" in s.tick(datetime.now(timezone.utc))
