from datetime import date

import httpx

from app.sources import consema, doe_rs, fepam
from app.sources.base import SourceError
from tests.conftest import fixture_json, fixture_text


# ----------------------------------------------------------------- CONSEMA
def test_consema_parse_extrai_numero_ementa_pdf_e_data():
    items = {i.external_id: i for i in consema.parse(fixture_text("consema_resolucoes.html"))}
    assert "556/2026" in items and "542/2025" in items
    r = items["556/2026"]
    assert r.title == "Resolução CONSEMA 556/2026"
    assert r.summary.startswith("Altera a Resolução 372/2018")
    assert r.url.startswith("https://www.sema.rs.gov.br/upload/arquivos/202607/")
    assert r.url.endswith(".pdf")
    assert r.published_at == date(2026, 7, 15)  # dia vem do nome do arquivo enviado


def test_consema_aceita_texto_unicode_decomposto():
    # a 554/2026 vem com "ç" e "ã" decompostos na pagina real
    items = {i.external_id for i in consema.parse(fixture_text("consema_resolucoes.html"))}
    assert "554/2026" in items


def test_consema_ignora_resolucao_compilada_aninhada():
    items = consema.parse(fixture_text("consema_resolucoes.html"))
    assert not any("compilada" in i.title.lower() for i in items)
    r = next(i for i in items if i.external_id == "554/2026")
    assert "compilada" not in r.summary.lower()


def test_consema_published_from_upload():
    assert consema.published_from_upload("/upload/arquivos/202607/15145558-x.pdf") == date(2026, 7, 15)
    assert consema.published_from_upload("/upload/arquivos/202602/31145558-x.pdf") == date(2026, 2, 1)  # dia invalido
    assert consema.published_from_upload("/outro/caminho.pdf") is None


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_consema_fetch_filtra_por_data():
    c = _client(lambda req: httpx.Response(200, text=fixture_text("consema_resolucoes.html")))
    items = consema.ConsemaSource(c).fetch(since=date(2026, 7, 1))
    assert {i.external_id for i in items} == {"556/2026", "555/2026", "554/2026", "553/2026"}


def test_consema_layout_quebrado_vira_erro_claro():
    c = _client(lambda req: httpx.Response(200, text="<html><body>manutencao</body></html>"))
    try:
        consema.ConsemaSource(c).fetch(since=date(2026, 1, 1))
    except SourceError as exc:
        assert "layout" in str(exc)
    else:
        raise AssertionError("deveria falhar")


# ------------------------------------------------------------------- FEPAM
def test_fepam_parse_comunicados():
    items = fepam.parse(fixture_text("fepam_comunicados.html"))
    assert len(items) == 3
    first = items[0]
    assert first.title.startswith("Fepam se manifesta sobre processo de licenciamento")
    assert first.published_at == date(2026, 9, 30)
    assert first.url == (
        "https://www.fepam.rs.gov.br/fepam-se-manifesta-sobre-processo-de-licenciamento-do-projeto-fosfato-tres-estradas"
    )
    assert "Nota Técnica" in first.summary


def test_fepam_fetch_deduplica_entre_paginas_e_filtra_data():
    c = _client(lambda req: httpx.Response(200, text=fixture_text("fepam_comunicados.html")))
    items = fepam.FepamSource(c).fetch(since=date(2026, 9, 25))
    assert [i.published_at for i in items] == [date(2026, 9, 30)]


def test_fepam_fetch_sem_rede_levanta_erro():
    def boom(req):
        raise httpx.ConnectError("sem rede")

    try:
        fepam.FepamSource(_client(boom)).fetch(since=date(2026, 1, 1))
    except SourceError:
        pass
    else:
        raise AssertionError("deveria falhar")


# ------------------------------------------------------------------ DOE-RS
def test_doe_busca_texto_completo_so_dos_candidatos():
    destaques = fixture_json("doe_destaques.json")
    materia = fixture_json("doe_materia_fepam.json")
    fetched = []

    def handler(req: httpx.Request):
        if req.url.path == "/public/destaques/":
            assert req.url.params["data"] == "2026-10-01"
            return httpx.Response(200, json=destaques)
        fetched.append(req.url.path)
        mid = req.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={**materia, "id": mid})

    src = doe_rs.DoeRsSource(_client(handler), delay=0)
    items = src._day(date(2026, 10, 1))

    # contratos e convenios fora do tema nao geram chamada ao texto completo
    assert "/public/materias/1487405" not in fetched
    assert "/public/materias/1486922" in fetched
    fepam_item = next(i for i in items if i.external_id == "1486922")
    assert fepam_item.source == "doe_rs"
    assert fepam_item.url == "https://www.diariooficial.rs.gov.br/materia?id=1486922"
    assert "PORTARIA FEPAM" in fepam_item.title
    assert fepam_item.issuer.startswith("Fundação Estadual de Proteção Ambiental")
    assert fepam_item.published_at == date(2026, 10, 1)
    assert "<p" not in fepam_item.summary  # html removido


def test_doe_fetch_falha_se_todos_os_dias_falham():
    c = _client(lambda req: httpx.Response(500))
    try:
        doe_rs.DoeRsSource(c, delay=0).fetch(since=date.today())
    except SourceError as exc:
        assert "DOE-RS" in str(exc)
    else:
        raise AssertionError("deveria falhar")


def test_doe_titulo_e_a_primeira_linha_e_o_resumo_nao_repete_o_titulo():
    title, rest = doe_rs.split_title(
        '<p style="text-align:center"><b>PORTARIA FEPAM N°. 634/2026</b></p><p>O PRESIDENTE DA FEPAM resolve designar</p>'
    )
    assert title == "PORTARIA FEPAM N°. 634/2026"
    assert rest.startswith("O PRESIDENTE DA FEPAM") and "PORTARIA" not in rest


def test_doe_titulo_longo_corta_em_palavra_inteira():
    long = "<p>" + "AVISO SEMA Segunda Chamada para Cadastramento de Entidades " * 6 + "</p>"
    title, rest = doe_rs.split_title(long)
    assert title.endswith("…") and len(title) <= doe_rs.TITLE_MAX + 1
    assert not title[:-1].endswith("Entidad")  # nao parte palavra ao meio
    assert rest.startswith("AVISO SEMA")  # texto completo preservado


def test_doe_texto_sem_paragrafos_usa_o_texto_inteiro_como_base():
    title, rest = doe_rs.split_title("PORTARIA CURTA Nº 1")
    assert title == "PORTARIA CURTA Nº 1" and rest == ""
