"""Gera o pacote de conteudo (carrossel, reel, legenda, status do WhatsApp) a partir de um ato oficial."""
from __future__ import annotations

import json
from datetime import timedelta

import httpx
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.config import Settings
from app.fulltext import FullTextError, Material, fetch_material
from app.models import (
    DRAFT_ERROR, DRAFT_GENERATING, DRAFT_READY, Draft, Item, utcnow,
)
from app.sources.base import make_client

BETAS = ["server-side-fallback-2026-07-01"]
STALE_AFTER = timedelta(minutes=10)  # uma geracao "em andamento" ha mais que isso e tratada como travada


class DraftError(RuntimeError):
    """Falha ao gerar o rascunho; a mensagem e mostrada ao usuario."""


# ----------------------------------------------------------------------- conteudo
class Slide(BaseModel):
    title: str
    body: str


class Scene(BaseModel):
    narration: str
    on_screen_text: str


class Reel(BaseModel):
    hook: str
    scenes: list[Scene] = Field(min_length=1)
    cta: str


class DraftContent(BaseModel):
    headline: str
    plain_summary: str
    who_is_affected: list[str]
    practical_impact: list[str]
    carousel: list[Slide] = Field(min_length=3)
    reel: Reel
    caption: str
    hashtags: list[str]
    whatsapp_status: str
    review_notes: list[str]


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}
SCHEMA = _obj({
    "headline": _STR,
    "plain_summary": _STR,
    "who_is_affected": _STRS,
    "practical_impact": _STRS,
    "carousel": {"type": "array", "items": _obj({"title": _STR, "body": _STR})},
    "reel": _obj({
        "hook": _STR,
        "scenes": {"type": "array", "items": _obj({"narration": _STR, "on_screen_text": _STR})},
        "cta": _STR,
    }),
    "caption": _STR,
    "hashtags": _STRS,
    "whatsapp_status": _STR,
    "review_notes": _STRS,
})

SYSTEM_PROMPT = """Você é redator de conteúdo da HRBio Ambiental, consultoria ambiental do Rio Grande do Sul \
(licenciamento ambiental, mineração e extração de areia, recursos hídricos, resíduos, fauna e flora). \
Você transforma um ato oficial em conteúdo para Instagram e WhatsApp dirigido a empreendedores e gestores, \
que não são advogados.

Regras:
1. Use SOMENTE o que está no texto fornecido. Nunca invente prazos, números, artigos, valores, multas, órgãos \
ou datas de vigência. Se algo importante não estiver claro (quem é afetado, quando vale, qual o prazo), não \
suponha: registre em review_notes.
2. Linguagem simples e direta, sem juridiquês e sem sensacionalismo (nada de "urgente", "bomba", "cuidado!"). \
Explique o que mudou, para quem e o que o leitor precisa fazer.
3. Identifique a norma pelo número e pelo órgão que a publicou. Não escreva endereços de internet: o sistema \
acrescenta a fonte e o link.
4. Feche com uma chamada para a HRBio Ambiental: convide o leitor a falar com a equipe para entender o impacto \
no próprio empreendimento. Não prometa resultado, licença nem prazo.
5. Se o ato for apenas administrativo ou não interessar a empreendedores, gere o rascunho mesmo assim, mas diga \
em review_notes que não vale postar e por quê.
6. Carrossel: de 6 a 8 slides, nesta ordem: capa com gancho, o que mudou, quem é afetado, o que muda na prática \
(com prazos, se houver no texto), o que fazer, como a HRBio ajuda, fonte oficial. Título com até 50 caracteres \
e texto com até 220 caracteres por slide.
7. Reel: de 25 a 40 segundos, gancho nos primeiros 3 segundos, de 4 a 6 cenas com narração em português do \
Brasil (fala natural) e texto de tela curto.
8. caption: até 900 caracteres; a primeira linha é o gancho. hashtags: de 5 a 8, sem o símbolo #.
9. whatsapp_status: até 280 caracteres, para o Status do WhatsApp, terminando com um convite para falar com a HRBio.
10. review_notes: tudo o que a pessoa responsável deve conferir no texto oficial antes de publicar (datas, \
números, trechos que você interpretou). Seja específico, no máximo 6 itens; lista vazia só se tudo estiver \
explícito no texto.

Responda apenas com o JSON do formato pedido."""


def _user_content(item: Item, material: Material) -> list[dict]:
    header = (
        f"Ato oficial a transformar em conteúdo.\n"
        f"Fonte: {item.source} | Órgão: {item.issuer or 'não informado'} | Tipo: {item.doc_type or 'não informado'}\n"
        f"Título: {item.title}\n"
        f"Data de publicação: {item.published_at.strftime('%d/%m/%Y') if item.published_at else 'não informada'}\n"
    )
    if material.is_pdf:
        return [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": material.pdf_b64}},
            {"type": "text", "text": header + "\nO texto oficial está no PDF acima."},
        ]
    return [{"type": "text", "text": header + "\nTexto oficial:\n" + material.text}]


def generate_content(item: Item, material: Material, client, model: str) -> DraftContent:
    """Chama o Claude e devolve o conteudo validado. `client` e um anthropic.Anthropic."""
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            betas=BETAS,
            fallbacks="default",
            system=SYSTEM_PROMPT,
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": _user_content(item, material)}],
        )
    except Exception as exc:  # noqa: BLE001 - erros de rede/API viram mensagem para a tela
        raise DraftError(f"Falha ao chamar o Claude: {exc}") from exc

    if response.stop_reason == "refusal":
        raise DraftError("O Claude recusou gerar este conteúdo. Escreva o rascunho manualmente.")
    if response.stop_reason == "max_tokens":
        raise DraftError("A resposta do Claude foi cortada por tamanho. Tente gerar de novo.")
    text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
    try:
        return DraftContent.model_validate(json.loads(text))
    except (ValueError, ValidationError) as exc:
        raise DraftError("O Claude devolveu um formato inesperado. Tente gerar de novo.") from exc


# ------------------------------------------------------------------- legenda final
def build_full_caption(item: Item, content: DraftContent) -> str:
    tags = " ".join("#" + t.lstrip("#").replace(" ", "") for t in content.hashtags if t.strip())
    source = f"Fonte oficial: {item.title}\n{item.url}"
    note = "Conteúdo informativo. Consulte o texto oficial."
    return "\n\n".join(p for p in (content.caption.strip(), source, note, tags) if p)


# ------------------------------------------------------------------ tarefa em segundo plano
def start_draft(db, item: Item) -> Draft | None:
    """Reserva a geracao. None se ja ha uma em andamento (e nao travada)."""
    draft = db.scalar(select(Draft).where(Draft.item_id == item.id))
    now = utcnow()
    if draft and draft.status == DRAFT_GENERATING and now - draft.started_at < STALE_AFTER:
        return None
    if not draft:
        draft = Draft(item_id=item.id)
        db.add(draft)
    draft.status = DRAFT_GENERATING
    draft.error = ""
    draft.started_at = draft.updated_at = now
    db.commit()
    return draft


def run_draft_job(session_factory, settings: Settings, item_id: int, claude_client=None, http_client=None) -> None:
    """Busca o texto, gera e grava. Nunca levanta: o resultado (ou o erro) fica no Draft."""
    with session_factory() as db:
        item = db.get(Item, item_id)
        draft = db.scalar(select(Draft).where(Draft.item_id == item_id))
        if not item or not draft:
            return
        try:
            if claude_client is None:
                if not settings.anthropic_api_key:
                    raise DraftError("Falta configurar ANTHROPIC_API_KEY no servidor.")
                import anthropic

                claude_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            http_client = http_client or make_client(settings.user_agent, timeout=60.0)
            material = fetch_material(item, http_client)
            content = generate_content(item, material, claude_client, settings.draft_model)
            draft.content = content.model_dump_json()
            draft.model = settings.draft_model
            draft.status = DRAFT_READY
            draft.approved_at = None
            draft.generations += 1
            draft.error = ""
        except (DraftError, FullTextError, httpx.HTTPError) as exc:
            draft.status = DRAFT_ERROR
            draft.error = str(exc)[:500]
        except Exception as exc:  # noqa: BLE001 - nao deixa a geracao "presa" por erro inesperado
            draft.status = DRAFT_ERROR
            draft.error = f"Erro inesperado: {exc}"[:500]
        draft.updated_at = utcnow()
        db.commit()
