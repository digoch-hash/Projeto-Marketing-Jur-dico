import json
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from app.db import add_missing_columns, init_db
from app.editorial import build_agenda, conflicts, suggest_date, taken_dates, today_br
from app.models import Draft, Item, User, utcnow
from app.security import hash_password
from app.web.main import create_app
from tests.test_drafts import VALID

D = date(2026, 10, 2)  # sexta


# ---------------------------------------------------------------- ritmo de datas
def test_sem_posts_a_sugestao_e_hoje():
    assert suggest_date(set(), D) == D


def test_dia_sim_dia_nao():
    assert suggest_date({D}, D) == D + timedelta(days=2)            # hoje ocupado -> depois de amanha
    assert suggest_date({D - timedelta(days=1)}, D) == D + timedelta(days=1)  # postou ontem -> amanha
    assert suggest_date({D + timedelta(days=2)}, D) == D            # hoje ainda cabe antes do post de D+2


def test_preenche_o_primeiro_buraco_no_ritmo():
    taken = {D, D + timedelta(days=4)}
    assert suggest_date(taken, D) == D + timedelta(days=2)


def test_conflito_so_quando_ha_menos_de_dois_dias():
    assert conflicts(D, {D}) and conflicts(D, {D + timedelta(days=1)})
    assert not conflicts(D, {D + timedelta(days=2)})


def test_hoje_usa_horario_de_brasilia_e_nao_utc():
    # 01:00 UTC de 03/10 ainda e 22:00 de 02/10 em Brasilia
    assert today_br(datetime(2026, 10, 3, 1, 0, tzinfo=timezone.utc)) == date(2026, 10, 2)
    assert today_br(datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)) == date(2026, 10, 3)


# ------------------------------------------------------------------- migracao
def test_migracao_leve_acrescenta_colunas_sem_perder_dados():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with engine.begin() as c:  # banco da fase 2, sem as colunas de agenda
        c.execute(text(
            "CREATE TABLE drafts (id INTEGER PRIMARY KEY, item_id INTEGER UNIQUE, status VARCHAR(16), content TEXT,"
            " error VARCHAR(500), model VARCHAR(64), generations INTEGER, started_at DATETIME, updated_at DATETIME,"
            " approved_at DATETIME)"
        ))
        c.execute(text("INSERT INTO drafts (item_id, status, content, error, model, generations) VALUES (7,'approved','{}','','m',1)"))
    init_db(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("drafts")}
    assert {"scheduled_for", "published_at"} <= cols
    with engine.connect() as c:
        assert c.execute(text("SELECT status, scheduled_for FROM drafts WHERE item_id=7")).one() == ("approved", None)
    assert add_missing_columns(engine) == []  # idempotente


# -------------------------------------------------------------------- agenda
def _draft(db, n, status, scheduled=None, published=None):
    item = Item(source="x", external_id=str(n), title=f"Norma {n}", url="http://x", relevance=80)
    db.add(item)
    db.flush()
    db.add(Draft(item_id=item.id, status=status, content=json.dumps(VALID), scheduled_for=scheduled,
                 published_at=published, approved_at=utcnow()))
    db.commit()
    return item.id


def test_agenda_marca_posts_dias_livres_e_descanso(session_factory):
    with session_factory() as db:
        _draft(db, 1, "scheduled", scheduled=D + timedelta(days=2))
        agenda = build_agenda(db, D, days=6)
    kinds = [a.kind for a in agenda]
    # D livre, D+1 descanso (colado no post de D+2), D+2 post, D+3 descanso, D+4 livre, D+5 descanso
    assert kinds == ["free", "rest", "post", "rest", "free", "rest"]
    assert agenda[0].is_today and agenda[2].posts[0][1].title == "Norma 1"


def test_publicado_conta_no_intervalo_pelo_dia_de_brasilia(session_factory):
    with session_factory() as db:
        # 01:00 UTC de 03/10 = 22:00 BRT de 02/10
        _draft(db, 1, "published", published=datetime(2026, 10, 3, 1, 0))
        assert taken_dates(db) == {date(2026, 10, 2)}


# --------------------------------------------------------------------- telas
@pytest.fixture
def cal(settings, session_factory):
    with session_factory() as db:
        db.add(User(username="rodrigo", password_hash=hash_password("senha-segura-1")))
        db.commit()
        ids = {
            "approved": _draft(db, 1, "approved"),
            "approved2": _draft(db, 2, "approved"),
            "ready": _draft(db, 3, "draft"),
        }
    c = TestClient(create_app(replace(settings, anthropic_api_key=None), session_factory))
    c.post("/login", data={"username": "rodrigo", "password": "senha-segura-1"})
    c.ids = ids
    return c


def token(c):
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/calendario").text).group(1)


def test_calendario_lista_fila_com_datas_sugeridas_diferentes(cal):
    html = cal.get("/calendario").text
    assert "Norma 1" in html and "Norma 2" in html and "Norma 3" not in html  # rascunho nao aprovado nao entra
    dates = re.findall(r'type="date" name="day" min="[\d-]+" value="([\d-]+)"', html)
    assert len(dates) == 2 and dates[0] != dates[1]
    assert (date.fromisoformat(dates[1]) - date.fromisoformat(dates[0])).days >= 2


def test_agendar_sem_data_usa_a_sugestao_e_aparece_no_calendario(cal):
    t = token(cal)
    r = cal.post(f"/items/{cal.ids['approved']}/draft/schedule", data={"csrf": t, "next_url": "/calendario"})
    assert r.status_code == 200 and "Agendado para" in r.text
    html = cal.get("/calendario").text
    assert "Aprovados, sem data (1)" in html
    post_row = re.search(r'<div class="ag post">(.*?)</div>\s*</div>', html, re.S)
    assert post_row and "Norma 1" in post_row.group(1)  # o post esta na linha de hoje da agenda


def test_agendar_data_passada_e_recusada_e_fora_do_ritmo_avisa(cal):
    t = token(cal)
    past = (today_br() - timedelta(days=1)).isoformat()
    html = cal.post(f"/items/{cal.ids['approved']}/draft/schedule", data={"csrf": t, "day": past}).text
    assert "já passou" in html
    d1 = today_br() + timedelta(days=3)
    cal.post(f"/items/{cal.ids['approved']}/draft/schedule", data={"csrf": t, "day": d1.isoformat()})
    html = cal.post(
        f"/items/{cal.ids['approved2']}/draft/schedule", data={"csrf": t, "day": (d1 + timedelta(days=1)).isoformat()}
    ).text
    assert "fora do ritmo" in html


def test_so_agenda_o_que_foi_aprovado(cal):
    t = token(cal)
    assert cal.post(f"/items/{cal.ids['ready']}/draft/schedule", data={"csrf": t}).status_code == 400


def test_tirar_do_calendario_e_marcar_como_publicado(cal):
    t = token(cal)
    a = cal.ids["approved"]
    cal.post(f"/items/{a}/draft/schedule", data={"csrf": t})
    cal.post(f"/items/{a}/draft/unschedule", data={"csrf": t})
    assert "Aprovados, sem data (2)" in cal.get("/calendario").text
    cal.post(f"/items/{a}/draft/schedule", data={"csrf": t})
    cal.post(f"/items/{a}/draft/published", data={"csrf": t, "next_url": "/calendario"})
    html = cal.get("/calendario").text
    assert "Publicados recentemente" in html and "Norma 1" in html
    # o publicado ocupa o dia de hoje: o proximo sugerido respeita o intervalo
    assert today_br().isoformat() not in re.findall(r'name="day" min="[\d-]+" value="([\d-]+)"', html)


def test_editar_depois_de_agendar_cancela_aprovacao_e_agenda(cal):
    t = token(cal)
    a = cal.ids["approved"]
    cal.post(f"/items/{a}/draft/schedule", data={"csrf": t})
    cal.post(f"/items/{a}/draft/save", data={"csrf": t, "caption": "mudou"})
    page = cal.get(f"/items/{a}/draft").text
    assert "Aprovar este conteúdo" in page and "Agendado para" not in page
    assert "Aprovados, sem data (1)" in cal.get("/calendario").text


def test_publicado_nao_e_regerado_nem_perde_o_status(cal, settings, session_factory):
    t = token(cal)
    a = cal.ids["approved"]
    cal.post(f"/items/{a}/draft/published", data={"csrf": t})
    cal.post(f"/items/{a}/draft/save", data={"csrf": t, "caption": "ajuste de ortografia"})
    assert "Publicado ✓" in cal.get(f"/items/{a}/draft").text
    with session_factory() as db:
        assert db.query(Draft).filter_by(item_id=a).one().status == "published"


def test_atrasados_aparecem_em_destaque(cal, session_factory):
    t = token(cal)
    a = cal.ids["approved"]
    cal.post(f"/items/{a}/draft/schedule", data={"csrf": t})
    with session_factory() as db:
        d = db.query(Draft).filter_by(item_id=a).one()
        d.scheduled_for = today_br() - timedelta(days=2)
        db.commit()
    html = cal.get("/calendario").text
    assert "Atrasados (1)" in html and "Norma 1" in html


def test_calendario_exige_login(settings, session_factory):
    c = TestClient(create_app(settings, session_factory))
    assert c.get("/calendario", follow_redirects=False).status_code == 303
    assert c.post("/items/1/draft/schedule", data={"csrf": "x"}, follow_redirects=False).status_code == 303
