import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import notify, scheduler as sched_mod
from app.models import Item, User, utcnow
from app.scheduler import Scheduler
from app.security import hash_password
from app.web import main as web
from app.web.main import create_app


class FakeSMTP:
    """Servidor de e-mail de mentira: guarda o que foi enviado."""

    instances: list = []

    def __init__(self, fail_login=False):
        self.fail_login, self.logged, self.sent, self.tls = fail_login, None, [], False
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.tls = True

    def login(self, user, password):
        if self.fail_login:
            raise RuntimeError("535 senha incorreta")
        self.logged = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture
def mail(settings):
    FakeSMTP.instances.clear()
    return replace(
        settings, smtp_host="smtp.hostinger.com", smtp_port=465, smtp_user="radar@hrbio.com.br",
        smtp_password="s3gredo", alert_emails="rodrigo@hrbio.com.br; esposa@hrbio.com.br, invalido",
        alert_min_relevance=60, public_base_url="https://app.exemplo.com",
    )


def add_item(db, n, relevance, status="new", seen=None, **kw):
    item = Item(source="consema", external_id=str(n), title=f"Resolução CONSEMA {n}/2026", url=f"https://x/{n}.pdf",
                relevance=relevance, status=status, published_at=date(2026, 10, 1), relevance_reason="licenciamento; resolução do CONSEMA", **kw)
    if seen:
        item.first_seen_at = seen
    db.add(item)
    db.commit()
    return item


def factory(**kw):
    return lambda: FakeSMTP(**kw)


# -------------------------------------------------------------------- envio
def test_destinatarios_aceitam_virgula_e_ponto_e_virgula_e_ignoram_lixo(mail):
    assert notify.recipients(mail) == ["rodrigo@hrbio.com.br", "esposa@hrbio.com.br"]
    assert notify.is_configured(mail)
    for missing in ("smtp_host", "smtp_user", "smtp_password", "alert_emails"):
        assert not notify.is_configured(replace(mail, **{missing: ""}))


def test_envio_usa_login_remetente_e_destinatarios(mail):
    notify.send_email(mail, "Assunto", "Corpo", factory())
    smtp = FakeSMTP.instances[0]
    assert smtp.logged == ("radar@hrbio.com.br", "s3gredo") and not smtp.tls
    msg = smtp.sent[0]
    assert msg["From"] == "radar@hrbio.com.br" and msg["To"] == "rodrigo@hrbio.com.br, esposa@hrbio.com.br"
    assert msg["Subject"] == "Assunto" and "Corpo" in msg.get_content()


def test_porta_587_usa_starttls(mail):
    notify.send_email(replace(mail, smtp_port=587), "A", "B", factory())
    assert FakeSMTP.instances[0].tls


def test_mensagem_singular_plural_limite_de_lista_e_links(mail, session_factory):
    with session_factory() as db:
        one = [add_item(db, 1, 80)]
        subject, body = notify.build_message(mail, one)
        assert subject == "HRBio Radar: 1 novidade relevante" and "https://app.exemplo.com/items/" in body
        assert "licenciamento; resolução do CONSEMA" in body and "relevância 80" in body and "CONSEMA · 01/10/2026" in body
        many = [add_item(db, n, 70) for n in range(2, 15)]
        subject, body = notify.build_message(mail, many)
        assert subject == "HRBio Radar: 13 novidades relevantes" and "e mais 3 no sistema" in body
        assert body.count("• ") == 10
        assert "Sem novidade, sem e-mail" in body


# --------------------------------------------------------------- quando avisar
def test_avisa_so_o_muito_relevante_novo_e_ainda_nao_avisado(mail, session_factory):
    with session_factory() as db:
        add_item(db, 1, 85)  # entra
        add_item(db, 2, 59)  # abaixo do corte
        add_item(db, 3, 90, status="ignored")  # ja decidido
        assert notify.alert_new_relevant(db, mail, smtp_factory=factory()) == 1
        body = FakeSMTP.instances[0].sent[0].get_content()
        assert "Resolução CONSEMA 1/2026" in body and "CONSEMA 2/2026" not in body and "CONSEMA 3/2026" not in body
        # segunda passada sem novidade: silencio total (nenhum e-mail novo)
        FakeSMTP.instances.clear()
        assert notify.alert_new_relevant(db, mail, smtp_factory=factory()) == 0
        assert FakeSMTP.instances == []


def test_nao_repete_o_que_ja_foi_avisado_mas_avisa_o_novo(mail, session_factory):
    with session_factory() as db:
        add_item(db, 1, 85)
        notify.alert_new_relevant(db, mail, smtp_factory=factory())
        add_item(db, 2, 75, seen=utcnow() + timedelta(minutes=5))
        FakeSMTP.instances.clear()
        assert notify.alert_new_relevant(db, mail, now=utcnow() + timedelta(minutes=10), smtp_factory=factory()) == 1
        body = FakeSMTP.instances[0].sent[0].get_content()
        assert "CONSEMA 2/2026" in body and "CONSEMA 1/2026" not in body


def test_falha_no_e_mail_nao_derruba_e_tenta_de_novo_depois(mail, session_factory):
    with session_factory() as db:
        add_item(db, 1, 85)
        assert notify.alert_new_relevant(db, mail, smtp_factory=factory(fail_login=True)) is None  # nao levanta
        FakeSMTP.instances.clear()
        assert notify.alert_new_relevant(db, mail, smtp_factory=factory()) == 1  # a novidade nao se perdeu


def test_sem_configuracao_nao_faz_nada(settings, session_factory):
    with session_factory() as db:
        add_item(db, 1, 99)
        assert notify.alert_new_relevant(db, settings) is None


def test_agendador_avisa_depois_da_coleta_diaria_e_so_quando_ha_algo(mail, session_factory, monkeypatch):
    sent = []
    monkeypatch.setattr(sched_mod, "run_default", lambda db, st: [])
    monkeypatch.setattr(notify, "send_email", lambda st, subj, body, f=None: sent.append(subj))
    with session_factory() as db:
        add_item(db, 1, 90)
    s = Scheduler(session_factory, mail)
    morning = datetime(2026, 10, 5, 10, 5, tzinfo=timezone.utc)  # 07:05 em Brasilia
    assert "alert:1" in s.tick(morning) and sent == ["HRBio Radar: 1 novidade relevante"]
    assert not [a for a in s.tick(morning + timedelta(days=1)) if a.startswith("alert")]  # dia seguinte, nada novo: silencio
    assert len(sent) == 1


# ------------------------------------------------------------------- telas
@pytest.fixture
def client(mail, session_factory):
    with session_factory() as db:
        db.add(User(username="rodrigo", password_hash=hash_password("senha-segura-1")))
        db.commit()
    c = TestClient(create_app(mail, session_factory))
    c.post("/login", data={"username": "rodrigo", "password": "senha-segura-1"})
    return c


def csrf(c, path="/conta"):
    return re.search(r'name="csrf" value="([^"]+)"', c.get(path).text).group(1)


def test_radar_destaca_quantas_muito_relevantes_ou_tranquiliza(client, session_factory):
    page = client.get("/").text
    assert "Nenhuma novidade muito relevante agora" in page and "Sem pressão" in page and "class=\"hot\"" not in page
    with session_factory() as db:
        add_item(db, 1, 85)
        add_item(db, 2, 70)
        add_item(db, 3, 45)  # aparece na lista, mas nao conta como "muito relevante"
    page = client.get("/").text
    assert "2 normas muito relevantes esperando a sua decisão" in page and "/?status=new&min_rel=60" in page
    with session_factory() as db:
        db.query(Item).filter(Item.external_id == "2").delete()
        db.commit()
    assert "1 norma muito relevante esperando" in client.get("/").text
    assert "Resolução CONSEMA 3/2026" not in client.get("/?status=new&min_rel=60").text  # o link do aviso filtra de verdade


def test_calendario_nao_fala_em_cota_de_posts(client):
    page = client.get("/calendario").text
    assert "Sem cota de posts" in page and "só publicamos quando há novidade relevante" in page
    assert "dia sim, dia não" not in page and "dia de postar" not in page.lower()
    assert "Livre" in page and "Nenhum post agendado, e tudo bem" in page


def test_conta_mostra_estado_do_aviso_e_testa_o_envio(client, monkeypatch):
    page = client.get("/conta").text
    assert "só quando aparece uma norma muito relevante" in page and "rodrigo@hrbio.com.br, esposa@hrbio.com.br" in page
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda st, subj, body, f=None: sent.append(subj))
    t = csrf(client)
    assert "E-mail de teste enviado para rodrigo@hrbio.com.br" in client.post("/conta/email-teste", data={"csrf": t}).text
    assert sent == ["HRBio Radar: e-mail de teste"]

    def boom(*a, **k):
        raise RuntimeError("535 senha incorreta")

    monkeypatch.setattr(notify, "send_email", boom)
    assert "Não consegui enviar: 535 senha incorreta" in client.post("/conta/email-teste", data={"csrf": t}).text
    assert client.post("/conta/email-teste", data={"csrf": "falso"}).status_code == 403


def test_conta_sem_smtp_explica_o_que_configurar(settings, session_factory):
    with session_factory() as db:
        db.add(User(username="rodrigo", password_hash=hash_password("senha-segura-1")))
        db.commit()
    c = TestClient(create_app(settings, session_factory))
    c.post("/login", data={"username": "rodrigo", "password": "senha-segura-1"})
    assert "SMTP_HOST" in c.get("/conta").text
    t = csrf(c)
    assert "não está configurado" in c.post("/conta/email-teste", data={"csrf": t}).text
