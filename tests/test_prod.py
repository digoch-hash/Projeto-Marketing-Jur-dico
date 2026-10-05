import json
import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app import ig_account, publicurls
from app.brand_assets import BrandAssets
from app.models import Draft, Item, Setting, User
from app.security import LoginThrottle, hash_password, verify_password
from app.web import main as web
from app.web.main import create_app, ensure_admin
from tests.fake_instagram import FakeInstagram
from tests.test_drafts import VALID

PW = "senha-segura-1"


def make_client(settings, session_factory, **overrides):
    with session_factory() as db:
        if not db.scalar(select(User)):
            db.add(User(username="rodrigo", password_hash=hash_password(PW)))
            db.commit()
    return TestClient(create_app(replace(settings, public_base_url="https://app.exemplo.com", **overrides), session_factory))


def login(c, user="rodrigo", pw=PW):
    return c.post("/login", data={"username": user, "password": pw})


def csrf(c, path="/conta"):
    return re.search(r'name="csrf" value="([^"]+)"', c.get(path).text).group(1)


@pytest.fixture
def c(settings, session_factory):
    client = make_client(settings, session_factory)
    login(client)
    return client


# -------------------------------------------------------------------- seguranca
def test_cabecalhos_de_seguranca_em_todas_as_respostas(settings, session_factory):
    r = make_client(settings, session_factory).get("/login")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "noindex" in r.headers["x-robots-tag"] and r.headers["referrer-policy"] == "no-referrer"


def test_em_producao_recusa_subir_com_chave_padrao(settings, session_factory):
    for weak in ("", "dev-only-change-me", "troque-esta-chave"):
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            create_app(replace(settings, cookie_secure=True, secret_key=weak), session_factory)
    create_app(replace(settings, cookie_secure=True, secret_key="uma-chave-longa-e-unica-123"), session_factory)


def test_cookie_de_sessao_e_marcado_como_seguro_em_producao(settings, session_factory):
    c = make_client(settings, session_factory, cookie_secure=True, secret_key="uma-chave-longa-e-unica-123")
    r = c.post("/login", data={"username": "rodrigo", "password": PW}, follow_redirects=False)
    cookie = r.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie


def test_login_trava_depois_de_5_erros_mesmo_com_a_senha_certa(settings, session_factory):
    c = make_client(settings, session_factory)
    for _ in range(5):
        assert "incorretos" in login(c, pw="errada").text
    assert "Muitas tentativas" in login(c).text  # senha certa, mas bloqueado
    with session_factory() as db:
        db.add(User(username="esposa", password_hash=hash_password(PW)))
        db.commit()
    assert c.get("/", follow_redirects=False).status_code == 303 and "Radar de Normas" in login(c, "esposa").text  # outro usuario nao e afetado


def test_acerto_zera_a_contagem_de_erros(settings, session_factory):
    c = make_client(settings, session_factory)
    for _ in range(4):
        login(c, pw="errada")
    assert "Radar" in login(c).text  # entrou
    c.post("/logout", data={"csrf": csrf(c, "/")})
    for _ in range(4):
        assert "incorretos" in login(c, pw="errada").text  # recomecou do zero


def test_throttle_libera_apos_o_tempo():
    now = [0.0]
    t = LoginThrottle(max_failures=3, window=60, lock=100, clock=lambda: now[0])
    for _ in range(3):
        t.failure("u:x")
    assert t.blocked_for("u:x") > 0
    now[0] = 101
    assert t.blocked_for("u:x") == 0
    now[0] = 500
    t.failure("u:x")
    t.failure("u:x")
    now[0] = 700  # erros antigos saem da janela
    t.failure("u:x")
    assert t.blocked_for("u:x") == 0


# --------------------------------------------------------------- primeiro usuario
def test_usuario_inicial_vem_das_variaveis_e_nao_sobrescreve_senha(settings, session_factory):
    cfg = replace(settings, admin_username="hrbio", admin_password="senha-inicial-123")
    ensure_admin(session_factory, cfg)
    c = TestClient(create_app(cfg, session_factory))
    assert "Radar" in login(c, "hrbio", "senha-inicial-123").text
    with session_factory() as db:
        db.scalar(select(User).where(User.username == "hrbio")).password_hash = hash_password("senha-trocada-456")
        db.commit()
    ensure_admin(session_factory, cfg)  # reinicio do servidor: nao desfaz a troca
    with session_factory() as db:
        assert verify_password("senha-trocada-456", db.scalar(select(User).where(User.username == "hrbio")).password_hash)
    with pytest.raises(RuntimeError, match="8 caracteres"):
        ensure_admin(session_factory, replace(cfg, admin_password="curta"))


# ------------------------------------------------------------------------ conta
def test_trocar_senha_e_criar_usuario(c, settings, session_factory):
    t = csrf(c)
    assert "senha atual está errada" in c.post("/conta/senha", data={"csrf": t, "atual": "x", "nova": "nova-senha-123"}).text
    assert "pelo menos 8" in c.post("/conta/senha", data={"csrf": t, "atual": PW, "nova": "curta"}).text
    assert "Senha alterada" in c.post("/conta/senha", data={"csrf": t, "atual": PW, "nova": "nova-senha-123"}).text
    assert "incorretos" in login(make_client(settings, session_factory), pw=PW).text
    assert "Radar" in login(make_client(settings, session_factory), pw="nova-senha-123").text

    assert "Usuário esposa criado" in c.post("/conta/usuario", data={"csrf": t, "usuario": "esposa", "senha": "outra-senha-123"}).text
    assert "já existe" in c.post("/conta/usuario", data={"csrf": t, "usuario": "ESPOSA", "senha": "outra-senha-123"}).text
    assert "pelo menos 8" in c.post("/conta/usuario", data={"csrf": t, "usuario": "novo", "senha": "x"}).text


def test_conta_exige_login_e_csrf(c, settings, session_factory):
    assert c.post("/conta/senha", data={"csrf": "falso", "atual": PW, "nova": "nova-senha-123"}).status_code == 403
    anon = make_client(settings, session_factory)
    for path in ("/conta", "/instagram"):
        assert anon.get(path, follow_redirects=False).status_code == 303


# ----------------------------------------------------------- imagens publicas
def put_art(settings, item_id=1):
    BrandAssets(settings.data_dir).save_art(item_id, {"slide_01": Image.new("RGB", (40, 50), (10, 90, 30)), "story": Image.new("RGB", (30, 50))})


def test_link_publico_serve_o_jpeg_sem_login_e_so_com_assinatura_valida(settings, session_factory):
    anon = make_client(settings, session_factory)
    put_art(settings)
    url = publicurls.sign(settings.secret_key, "", 1, "slide_01.jpg")
    r = anon.get(url)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert r.headers["cache-control"] == "no-store"
    assert anon.get(url.replace("slide_01", "story")).status_code == 404  # assinatura vale so para aquele arquivo
    assert anon.get(url.replace("/pub/1/", "/pub/2/")).status_code == 404
    assert anon.get(publicurls.sign(settings.secret_key, "", 1, "slide_01.jpg", ttl=-5)).status_code == 404  # vencido
    assert anon.get("/pub/1/9999999999/xxxx/slide_01.jpg").status_code == 404
    assert anon.get("/items/1/art/slide_01.png", follow_redirects=False).status_code == 303  # a pasta normal continua privada


# ----------------------------------------------------------------- tela instagram
@pytest.fixture
def ig(monkeypatch):
    fake = FakeInstagram()
    monkeypatch.setattr(ig_account, "_shared", fake.client())
    return fake


def test_conectar_pela_tela_nunca_mostra_o_token_de_volta(c, ig, session_factory):
    t = csrf(c, "/instagram")
    token = "TOKEN-BOM-" + "z" * 40
    page = c.post("/instagram/conectar", data={"csrf": t, "token": token}).text
    assert "Conectado como @hrbioambiental" in page and "DESLIGADA" in page
    assert token not in page and token not in c.get("/instagram").text
    with session_factory() as db:
        assert all(token not in (row.value or "") for row in db.scalars(select(Setting)))  # nem no banco, em texto puro
    assert "Não foi possível conectar" in c.post("/instagram/conectar", data={"csrf": t, "token": "TOKEN-VENCIDO-" + "z" * 30}).text


def test_ligar_automatico_e_desconectar(c, ig):
    t = csrf(c, "/instagram")
    assert c.post("/instagram/config", data={"csrf": t, "auto_publish": "on"}).status_code == 400  # sem conexao
    c.post("/instagram/conectar", data={"csrf": t, "token": "TOKEN-BOM-" + "z" * 40})
    page = c.post("/instagram/config", data={"csrf": t, "auto_publish": "on", "publish_story": "on"}).text
    assert "Configurações salvas" in page and re.search(r'name="auto_publish" checked', page)
    page = c.post("/instagram/config", data={"csrf": t}).text
    assert not re.search(r'name="auto_publish" checked', page)
    assert "Instagram desconectado" in c.post("/instagram/desconectar", data={"csrf": t}).text
    assert "Não conectado" in c.get("/instagram").text


def test_aviso_quando_falta_o_endereco_publico(settings, session_factory):
    c = TestClient(create_app(settings, session_factory))  # settings de teste: sem PUBLIC_BASE_URL
    with session_factory() as db:
        db.add(User(username="rodrigo", password_hash=hash_password(PW)))
        db.commit()
    login(c)
    assert "PUBLIC_BASE_URL" in c.get("/instagram").text


# --------------------------------------------------------- publicar pela tela
@pytest.fixture
def posted(c, ig, session_factory, monkeypatch):
    calls = []
    monkeypatch.setattr(web, "publish_draft", lambda *a, **k: calls.append(a[2]))
    with session_factory() as db:
        item = Item(source="consema", external_id="1", title="Norma 1", url="https://x/y.pdf", relevance=80)
        db.add(item)
        db.flush()
        db.add(Draft(item_id=item.id, status="approved", content=json.dumps(VALID)))
        db.commit()
    c.calls = calls
    return c


def test_publicar_agora_exige_conexao_e_dispara_uma_vez(posted, session_factory):
    c = posted
    t = csrf(c, "/instagram")
    assert "Conecte o Instagram antes" in c.post("/items/1/draft/publish-now", data={"csrf": t}).text and c.calls == []
    c.post("/instagram/conectar", data={"csrf": t, "token": "TOKEN-BOM-" + "z" * 40})
    assert "Publicando no Instagram" in c.post("/items/1/draft/publish-now", data={"csrf": t}).text and c.calls == [1]
    with session_factory() as db:
        db.scalar(select(Draft)).publish_state = "publishing"
        db.commit()
    assert "já há uma publicação" in c.post("/items/1/draft/publish-now", data={"csrf": t}).text.lower() and c.calls == [1]


def test_so_publica_o_que_esta_aprovado(posted, session_factory):
    c = posted
    t = csrf(c, "/instagram")
    c.post("/instagram/conectar", data={"csrf": t, "token": "TOKEN-BOM-" + "z" * 40})
    with session_factory() as db:
        db.scalar(select(Draft)).status = "draft"
        db.commit()
    assert c.post("/items/1/draft/publish-now", data={"csrf": t}).status_code == 400 and c.calls == []
    assert c.post("/items/1/draft/publish-now", data={"csrf": "falso"}).status_code == 403


def test_tela_do_rascunho_mostra_erro_e_pede_confirmacao_quando_incerto(posted, session_factory):
    c = posted
    t = csrf(c, "/instagram")
    c.post("/instagram/conectar", data={"csrf": t, "token": "TOKEN-BOM-" + "z" * 40})
    with session_factory() as db:
        d = db.scalar(select(Draft))
        d.publish_state, d.publish_error = "failed", "URL da imagem inacessível"
        db.commit()
    page = c.get("/items/1/draft").text
    assert "Não saiu no Instagram: URL da imagem inacessível" in page and "Tentar publicar de novo" in page
    with session_factory() as db:
        d = db.scalar(select(Draft))
        d.publish_state, d.publish_error = "uncertain", "Sem resposta. Confira no Instagram."
        db.commit()
    page = c.get("/items/1/draft").text
    assert "Confirme no Instagram" in page and "o post está lá" in page and "Publicar agora no Instagram" not in page
    c.post("/items/1/draft/publish-resolve", data={"csrf": t, "action": "retry"})
    assert "Publicar agora no Instagram" in c.get("/items/1/draft").text
    c.post("/items/1/draft/publish-resolve", data={"csrf": t, "action": "published"})
    assert "Publicado ✓" in c.get("/items/1/draft").text
    assert c.post("/items/1/draft/publish-resolve", data={"csrf": t, "action": "x"}).status_code == 400


# ----------------------------------------------------------------------- backup
def test_backup_traz_banco_logo_e_fotos_sem_miniaturas(settings, tmp_path):
    import io
    import sqlite3
    import zipfile

    from app.backup import BackupError, make_backup, sqlite_path
    from sqlalchemy import create_engine

    db_file = tmp_path / "app.db"
    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE t (x TEXT)")
    con.execute("INSERT INTO t VALUES ('guardado')")
    con.commit()
    con.close()
    assets = BrandAssets(tmp_path)
    assets.save_logo(__import__("tests.test_art", fromlist=["png_bytes"]).png_bytes((50, 20), mode="RGBA"), "escuro")
    name = assets.save_photo(__import__("tests.test_art", fromlist=["jpg_bytes"]).jpg_bytes((300, 200)))
    cfg = replace(settings, database_url=f"sqlite:///{db_file}", data_dir=str(tmp_path))
    data, filename = make_backup(cfg)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        assert "app.db" in names and "brand/logo_escuro.png" in names and f"photos/{name}" in names
        assert not any("thumbs" in n for n in names)
        restored = tmp_path / "restaurado.db"
        restored.write_bytes(z.read("app.db"))
    assert sqlite3.connect(restored).execute("SELECT x FROM t").fetchone() == ("guardado",)
    assert filename.startswith("backup-hrbio-radar-") and filename.endswith(".zip")
    with pytest.raises(BackupError):
        make_backup(replace(cfg, database_url="postgresql+psycopg://u:p@h/db"))
    assert sqlite_path("sqlite://") is None


def test_backup_pela_tela_exige_login(settings, session_factory):
    assert make_client(settings, session_factory).get("/conta/backup", follow_redirects=False).status_code == 303
