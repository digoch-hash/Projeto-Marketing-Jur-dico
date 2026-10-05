import base64
import json
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from app import drafts
from app.drafts import DraftContent, DraftError, build_full_caption, generate_content, run_draft_job, start_draft
from app.fulltext import FullTextError, Material, fetch_material
from app.models import DRAFT_ERROR, DRAFT_GENERATING, DRAFT_READY, Draft, Item, utcnow

VALID = {
    "headline": "Nova regra para irrigação",
    "plain_summary": "O CONSEMA mudou o licenciamento da irrigação.",
    "who_is_affected": ["Produtores que irrigam"],
    "practical_impact": ["Novos procedimentos de licenciamento"],
    "carousel": [{"title": f"Slide {i}", "body": f"Texto {i}"} for i in range(1, 7)],
    "reel": {
        "hook": "Irrigou? Mudou o licenciamento.",
        "scenes": [{"narration": "Cena 1", "on_screen_text": "Mudou"}, {"narration": "Cena 2", "on_screen_text": "Confira"}],
        "cta": "Fale com a HRBio.",
    },
    "caption": "Mudou o licenciamento da irrigação.\n\nA HRBio Ambiental pode ajudar.",
    "hashtags": ["licenciamentoambiental", "#irrigacao"],
    "whatsapp_status": "Novidade no licenciamento da irrigação. Fale com a HRBio.",
    "review_notes": ["Confirmar a data de vigência no art. 5º"],
}


class FakeResponse:
    def __init__(self, text=None, stop_reason="end_turn"):
        block = type("B", (), {"type": "text", "text": text})()
        self.content = [block] if text is not None else []
        self.stop_reason = stop_reason


class FakeClaude:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []
        self.beta = self
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def ok_client():
    return FakeClaude(FakeResponse(json.dumps(VALID)))


def make_item(**kw):
    base = dict(
        source="doe_rs", external_id="1486922", title="PORTARIA FEPAM N° 634/2026", url="https://doe/materia?id=1486922",
        issuer="FEPAM", doc_type="Portarias", published_at=date(2026, 10, 1), relevance=80,
    )
    base.update(kw)
    return Item(**base)


# --------------------------------------------------------------- generate_content
def test_pedido_ao_claude_segue_o_contrato_da_api():
    client = ok_client()
    content = generate_content(make_item(), Material(text="Art. 1º Fica alterada a Resolução 512."), client, "claude-opus-5-5")
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["betas"] == ["server-side-fallback-2026-07-01"] and call["fallbacks"] == "default"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == "high"
    assert "temperature" not in call and "thinking" not in call  # removidos/ligado por padrao no Opus 5.5
    assert call["messages"][-1]["role"] == "user"  # sem prefill
    text = call["messages"][0]["content"][0]["text"]
    assert "PORTARIA FEPAM N° 634/2026" in text and "Art. 1º Fica alterada" in text
    assert "Nunca invente" in call["system"]
    assert isinstance(content, DraftContent) and len(content.carousel) == 6


def test_pdf_vai_como_bloco_document():
    client = ok_client()
    pdf = base64.b64encode(b"%PDF-1.4 fake").decode()
    generate_content(make_item(source="consema"), Material(pdf_b64=pdf), client, "m")
    blocks = client.calls[0]["messages"][0]["content"]
    assert blocks[0]["type"] == "document" and blocks[0]["source"]["media_type"] == "application/pdf"
    assert blocks[0]["source"]["data"] == pdf


@pytest.mark.parametrize(
    "client, trecho",
    [
        (FakeClaude(FakeResponse(None, stop_reason="refusal")), "recusou"),
        (FakeClaude(FakeResponse('{"headline": "cortado', stop_reason="max_tokens")), "cortada"),
        (FakeClaude(FakeResponse("isso não é json")), "formato inesperado"),
        (FakeClaude(FakeResponse(json.dumps({**VALID, "carousel": []}))), "formato inesperado"),
        (FakeClaude(error=RuntimeError("rede caiu")), "Falha ao chamar o Claude"),
    ],
)
def test_falhas_viram_draft_error_com_mensagem_clara(client, trecho):
    with pytest.raises(DraftError) as exc:
        generate_content(make_item(), Material(text="texto"), client, "m")
    assert trecho in str(exc.value)


def test_legenda_completa_traz_fonte_link_e_hashtags_normalizadas():
    caption = build_full_caption(make_item(), DraftContent.model_validate(VALID))
    assert "Fonte oficial: PORTARIA FEPAM N° 634/2026" in caption and "https://doe/materia?id=1486922" in caption
    assert caption.rstrip().endswith("#licenciamentoambiental #irrigacao")
    assert "##" not in caption


# ------------------------------------------------------------------ fulltext
def _http(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_texto_completo_do_doe_vem_da_api_sem_html():
    def handler(req):
        assert req.url.path == "/public/materias/1486922"
        return httpx.Response(200, json={"conteudo": "<p><b>PORTARIA</b></p><p>Art. 1º Fica designado</p>"})

    m = fetch_material(make_item(), _http(handler))
    assert m.text == "PORTARIA Art. 1º Fica designado" and not m.is_pdf


def test_consema_baixa_o_pdf_e_entrega_em_base64():
    item = make_item(source="consema", url="https://www.sema.rs.gov.br/upload/arquivos/202607/x.pdf")
    m = fetch_material(item, _http(lambda r: httpx.Response(200, content=b"%PDF-1.7 corpo")))
    assert m.is_pdf and base64.b64decode(m.pdf_b64).startswith(b"%PDF")


def test_consema_que_nao_devolve_pdf_falha_com_mensagem():
    item = make_item(source="consema", url="https://www.sema.rs.gov.br/upload/arquivos/202607/x.pdf")
    with pytest.raises(FullTextError, match="PDF"):
        fetch_material(item, _http(lambda r: httpx.Response(200, content=b"<html>erro</html>")))


def test_pagina_da_fepam_extrai_o_texto_do_artigo():
    html = "<html><body><div class='artigo__texto'>" + ("Texto do comunicado oficial da FEPAM. " * 5) + "</div></body></html>"
    item = make_item(source="fepam", url="https://www.fepam.rs.gov.br/x")
    assert "comunicado oficial" in fetch_material(item, _http(lambda r: httpx.Response(200, text=html))).text


def test_fonte_fora_do_ar_vira_erro_amigavel():
    with pytest.raises(FullTextError, match="Não consegui abrir"):
        fetch_material(make_item(), _http(lambda r: httpx.Response(503)))


def test_texto_gigante_nao_e_truncado_em_silencio(monkeypatch):
    monkeypatch.setattr("app.fulltext.TEXT_MAX_CHARS", 20)
    with pytest.raises(FullTextError, match="limite"):
        fetch_material(make_item(), _http(lambda r: httpx.Response(200, json={"conteudo": "x" * 50})))


# ---------------------------------------------------------------------- job
def _seed(session_factory):
    with session_factory() as db:
        item = make_item()
        db.add(item)
        db.commit()
        return item.id


def doe_http():
    return _http(lambda r: httpx.Response(200, json={"conteudo": "<p>PORTARIA FEPAM</p><p>Texto completo</p>"}))


def test_job_grava_o_rascunho_pronto(session_factory, settings):
    item_id = _seed(session_factory)
    with session_factory() as db:
        assert start_draft(db, db.get(Item, item_id)) is not None
    run_draft_job(session_factory, settings, item_id, claude_client=ok_client(), http_client=doe_http())
    with session_factory() as db:
        d = db.scalar(select(Draft))
        assert d.status == DRAFT_READY and d.generations == 1 and d.model == "test-draft-model"
        assert DraftContent.model_validate_json(d.content).headline == "Nova regra para irrigação"


def test_job_com_erro_deixa_status_error_e_nao_levanta(session_factory, settings):
    item_id = _seed(session_factory)
    with session_factory() as db:
        start_draft(db, db.get(Item, item_id))
    run_draft_job(session_factory, settings, item_id, claude_client=FakeClaude(FakeResponse(None, "refusal")), http_client=doe_http())
    with session_factory() as db:
        d = db.scalar(select(Draft))
        assert d.status == DRAFT_ERROR and "recusou" in d.error


def test_job_sem_chave_explica_o_que_falta(session_factory, settings):
    item_id = _seed(session_factory)
    with session_factory() as db:
        start_draft(db, db.get(Item, item_id))
    run_draft_job(session_factory, settings, item_id, http_client=doe_http())  # settings sem chave
    with session_factory() as db:
        assert "ANTHROPIC_API_KEY" in db.scalar(select(Draft)).error


def test_nao_inicia_duas_geracoes_ao_mesmo_tempo_mas_destrava_a_travada(session_factory):
    item_id = _seed(session_factory)
    with session_factory() as db:
        item = db.get(Item, item_id)
        assert start_draft(db, item) is not None
        assert start_draft(db, item) is None  # ja em andamento
        d = db.scalar(select(Draft))
        d.started_at = utcnow() - drafts.STALE_AFTER - timedelta(seconds=1)
        db.commit()
        assert start_draft(db, item) is not None  # travada ha muito tempo: pode reiniciar
        assert db.scalar(select(Draft)).status == DRAFT_GENERATING
