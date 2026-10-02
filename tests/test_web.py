import re

import pytest
from fastapi.testclient import TestClient

from app.models import Item, User
from app.security import hash_password, verify_password
from app.web.main import create_app


@pytest.fixture
def client(settings, session_factory):
    with session_factory() as db:
        db.add(User(username="rodrigo", password_hash=hash_password("senha-segura-1")))
        db.add_all([
            Item(source="consema", external_id="551/2026", title="Resolução CONSEMA 551/2026",
                 url="https://sema.rs.gov.br/x.pdf", summary="Diretrizes para PRAD de áreas mineradas",
                 doc_type="Resolução CONSEMA", relevance=80, themes="Mineração"),
            Item(source="doe_rs", external_id="1", title="Portaria SEMA 206", url="https://doe/1",
                 relevance=45, themes="Licenciamento ambiental"),
            Item(source="doe_rs", external_id="2", title="Ato fraco", url="https://doe/2", relevance=25),
        ])
        db.commit()
    return TestClient(create_app(settings, session_factory))


def login(client):
    r = client.post("/login", data={"username": "rodrigo", "password": "senha-segura-1"})
    assert r.status_code == 200
    return r


def csrf_of(client, path="/"):
    html = client.get(path).text
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def test_senha_scrypt_roundtrip():
    h = hash_password("abc12345")
    assert verify_password("abc12345", h) and not verify_password("outra", h)
    assert not verify_password("x", "lixo")


def test_sem_login_redireciona_para_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_login_errado_nao_entra(client):
    r = client.post("/login", data={"username": "rodrigo", "password": "errada"})
    assert "incorretos" in r.text
    assert client.get("/", follow_redirects=False).status_code == 303


def test_lista_respeita_relevancia_minima_e_filtros(client):
    login(client)
    html = client.get("/").text
    assert "Resolução CONSEMA 551/2026" in html and "Portaria SEMA 206" in html
    assert "Ato fraco" not in html  # abaixo do corte padrao (40)
    assert "Ato fraco" in client.get("/?min_rel=0").text
    only = client.get("/?theme=Mineração").text
    assert "551/2026" in only and "Portaria SEMA 206" not in only
    only = client.get("/?source=doe_rs").text
    assert "Portaria SEMA 206" in only and "551/2026" not in only


def test_marcar_quero_postar_move_de_aba(client, session_factory):
    login(client)
    token = csrf_of(client)
    with session_factory() as db:
        item_id = db.query(Item).filter_by(external_id="551/2026").one().id
    r = client.post(f"/items/{item_id}/status", data={"new_status": "post", "csrf": token, "next_url": "/"})
    assert r.status_code == 200
    assert "551/2026" not in client.get("/?status=new").text
    assert "551/2026" in client.get("/?status=post").text
    client.post(f"/items/{item_id}/status", data={"new_status": "new", "csrf": token})
    assert "551/2026" in client.get("/?status=new").text


def test_post_sem_csrf_e_recusado(client, session_factory):
    login(client)
    with session_factory() as db:
        item_id = db.query(Item).first().id
    r = client.post(f"/items/{item_id}/status", data={"new_status": "ignored", "csrf": "falso"})
    assert r.status_code == 403
    with session_factory() as db:
        assert db.get(Item, item_id).status == "new"


def test_next_url_externa_e_neutralizada(client, session_factory):
    login(client)
    token = csrf_of(client)
    with session_factory() as db:
        item_id = db.query(Item).first().id
    r = client.post(
        f"/items/{item_id}/status",
        data={"new_status": "later", "csrf": token, "next_url": "https://malicioso.example"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/"


def test_detalhe_mostra_link_oficial_e_logout_funciona(client):
    login(client)
    html = client.get("/items/1").text
    assert "https://sema.rs.gov.br/x.pdf" in html and "Abrir norma oficial" in html
    assert client.get("/items/999").status_code == 404
    token = csrf_of(client)
    client.post("/logout", data={"csrf": token})
    assert client.get("/", follow_redirects=False).status_code == 303


def test_healthz_e_publico(client):
    assert client.get("/healthz").json()["ok"] is True
