"""Filtro de relevancia: regras de palavras-chave + (opcional) avaliacao pelo Claude.

As regras servem de peneira barata: descartam o ruido (nomeacoes, licitacoes) e ordenam o
resto. O Claude, quando configurado, refina a nota dos candidatos.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.sources.base import RawItem

MIN_STORE = 20      # abaixo disso o item nem e guardado
CLAUDE_MIN = 25     # abaixo disso nao vale gastar uma chamada ao Claude

THEMES: dict[str, tuple[str, ...]] = {
    "Licenciamento ambiental": (
        "licenciamento", "licenca de operacao", "licenca de instalacao", "licenca previa",
        "licenca ambiental", "condicionante", "estudo de impacto", "eia/rima", "rima",
        "autorizacao ambiental", "dispensa de licenciamento",
    ),
    "Mineração": (
        "mineracao", "minerario", "lavra", "areia", "extracao mineral", "jazida", "pesquisa mineral",
        "agencia nacional de mineracao", "anm", "prad", "areas mineradas", "barragem de rejeitos",
        "saibro", "cascalho", "argila",
    ),
    "Recursos hídricos": (
        # "outorga" e "enquadramento" sozinhos tambem aparecem em atos financeiros (subvencao, FUNDOPEM)
        "recursos hidricos", "outorga de direito", "outorga de uso", "outorga de agua", "outorga hidrica",
        "bacia hidrografica", "agua subterranea", "corpo hidrico", "comite de bacia",
        "enquadramento de corpos", "cobranca pelo uso da agua", "poco tubular", "poco artesiano",
    ),
    "Resíduos": (
        "residuos solidos", "residuo", "mtr", "aterro", "logistica reversa", "pgrs",
        "coprocessamento", "residuos da construcao", "rcc",
    ),
    "Fauna e flora": (
        "fauna", "flora", "supressao de vegetacao", "vegetacao nativa", "mata atlantica", "pampa",
        "especie ameacada", "manejo", "reposicao florestal", "codigo florestal", "reserva legal",
        "area de preservacao permanente", "app", "silvicultura", "compensacao ambiental",
        "cadastro ambiental rural", "car",
    ),
    "Áreas protegidas": (
        "unidade de conservacao", "parque estadual", "reserva biologica", "zoneamento ambiental",
        "area de protecao ambiental", "zona de amortecimento", "corredor ecologico",
    ),
    "Fiscalização e penalidades": (
        "infracao ambiental", "auto de infracao", "multa ambiental", "embargo", "termo de compromisso",
        "crime ambiental", "dano ambiental",
    ),
    "Qualidade ambiental": (
        "poluicao", "emissoes atmosfericas", "efluente", "padroes de lancamento", "qualidade do ar",
        "qualidade da agua", "contaminacao", "areas contaminadas", "ruido",
    ),
    "Clima e energia": (
        "mudanca do clima", "mudancas climaticas", "gases de efeito estufa", "carbono",
        "energia eolica", "energia solar", "usina", "hidreletrica",
    ),
}

_ENV_BODIES = re.compile(
    r"\b(fepam|sema\b|consema|meio ambiente|ibama|icmbio|agencia nacional de aguas|"
    r"agencia nacional de mineracao|ambiental|ambientais|protecao ambiental)\b"
)
_NORMATIVE = re.compile(
    r"\b(portaria|resolucao|decreto|lei\b|leis\b|instrucao normativa|norma tecnica|diretriz tecnica)\b"
)
_NOISE = re.compile(
    r"\b(nomeacao|exoneracao|designacao|dispensa de servidor|ferias|gratificacao|diarias|"
    r"concurso publico|pregao|aviso de licitacao|extrato de contrato|termo aditivo|"
    r"sumula de contrato|cessao de servidor|progressao funcional|aposentadoria|"
    r"recursos humanos|apostilamento|ata de reuniao|balanco|convocacao de assembleia|"
    r"homologacao|adjudicacao|registro de precos|licitacao|atos de pessoal|afastamento|"
    r"retificacao de contrato|empenho)\b"
)
# Calibragem pelo TIPO do ato (aprendida com dados reais do DOE/CONSEMA): o que muda a vida do empreendedor
# sobe; o que e assunto interno do orgao ou acordo administrativo desce.
_CONSEMA_RES = re.compile(r"\bresolucao consema\b")
_TECH_NORM = re.compile(r"\b(diretriz tecnica|norma tecnica|instrucao normativa)\b")
_COOPERATION = re.compile(
    r"\b(termo de cooperacao|acordo de cooperacao|termo de colaboracao|memorando de entendimento|convenio)\b"
)
_GOVERNANCE = re.compile(
    r"\b(composico?es? d[aoe]s? camaras? tecnicas?|regimento interno|julga\w* (?:os )?processos?|"
    r"processo eleitoral|cadastramento de entidades|grupo de trabalho|comite gestor)\b"
)
_THIRD_PARTY_LICENSE = re.compile(
    r"\b(renovacao de licenca|emissao de licencas?|aviso de concessao de licenca|"
    r"torna publico que (?:recebeu|requereu)|requerimento de licenca|pedido de licenca)\b"
)

# Portarias de designacao de fiscais/gestores de convenio e contrato: o titulo e generico
# ("PORTARIA SEMA N. 206"), entao o sinal esta no comeco do texto.
_PERSONNEL = re.compile(
    r"\b(designar (?:os )?servidor\w*|fiscais?,? titular e suplente|gestor e fiscal|"
    r"comissao de fiscalizacao de contrato|substituicao de fiscal)\b"
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _compile(keywords: tuple[str, ...]) -> re.Pattern:
    # cada palavra-chave precisa casar como palavra inteira (evita "app" dentro de "aplicacao")
    return re.compile(r"\b(?:" + "|".join(re.escape(k) for k in keywords) + r")\b")


_THEME_RX = {name: _compile(kws) for name, kws in THEMES.items()}


@dataclass
class Classification:
    value: int
    themes: list[str] = field(default_factory=list)
    reason: str = ""
    by: str = "rules"


def _theme_hits(norm: str) -> dict[str, int]:
    hits = {}
    for name, rx in _THEME_RX.items():
        found = set(rx.findall(norm))
        if found:
            hits[name] = len(found)
    return hits


def quick_score(text: str) -> int:
    """Peneira rapida para trechos curtos: > 0 se vale a pena ler o texto completo."""
    norm = normalize(text)
    score = 15 if _ENV_BODIES.search(norm) else 0
    score += 15 * len(_theme_hits(norm))
    return score


def score_item(item: "RawItem") -> Classification:
    title = normalize(item.title)
    body = normalize(f"{item.title} {item.summary}")
    hits = _theme_hits(body)

    value = min(60, sum(min(40, 12 * n) for n in hits.values()))
    reasons = []
    if hits:
        reasons.append("temas: " + ", ".join(sorted(hits)))
    issuer = normalize(f"{item.issuer} {item.title}")
    if _ENV_BODIES.search(issuer):
        value += 25
        reasons.append("órgão ambiental")
    if _NORMATIVE.search(normalize(f"{item.doc_type} {item.title}")):
        value += 10
        reasons.append("ato normativo")
    if (
        _NOISE.search(title)
        or _NOISE.search(normalize(item.doc_type))
        or _PERSONNEL.search(normalize(item.summary[:700]))
    ):
        value -= 35
        reasons.append("ato administrativo/rotina")
    head = normalize(f"{item.doc_type} {item.title} {item.summary[:400]}")
    if _CONSEMA_RES.search(head):
        value += 20
        reasons.append("resolução do CONSEMA")
    elif _TECH_NORM.search(head):
        value += 15
        reasons.append("norma técnica")
    elif _COOPERATION.search(head):
        value -= 15
        reasons.append("acordo de cooperação")
    if _GOVERNANCE.search(head):
        value -= 25
        reasons.append("assunto interno do órgão")
    if _THIRD_PARTY_LICENSE.search(head):
        value -= 15
        reasons.append("licença de terceiros")
    value = max(0, min(100, value))
    return Classification(value=value, themes=sorted(hits), reason="; ".join(reasons), by="rules")


SYSTEM_PROMPT = """Voce avalia atos oficiais para a HRBio Ambiental, consultoria de meio ambiente do Rio Grande do Sul.
Os clientes sao empreendimentos de mineracao (areeiras e extracao de areia), industrias e obras sujeitos a
licenciamento ambiental, usuarios de recursos hidricos, geradores de residuos e atividades com fauna/flora.

Diga quao relevante o ato e como CONTEUDO para esses clientes (algo que muda obrigacoes, prazos, procedimentos
ou riscos deles). Atos de pessoal, contratos, licitacoes e rotina administrativa nao sao relevantes.
Normas do RS pesam mais; normas federais so se tiverem impacto claro no estado.

Responda APENAS um JSON: {"relevancia": 0-100, "temas": [..], "motivo": "ate 140 caracteres"}
Temas permitidos: """ + "; ".join(THEMES) + "."


class ClaudeClassifier:
    def __init__(self, api_key: str, model: str, client=None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
        self.client = client
        self.model = model

    def classify(self, item: "RawItem", fallback: Classification) -> Classification:
        user = (
            f"Fonte: {item.source}\nÓrgão: {item.issuer}\nTipo: {item.doc_type}\n"
            f"Título: {item.title}\n\nTexto:\n{item.summary[:1800]}"
        )
        try:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user}],
            )
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            return self._parse(text, fallback)
        except Exception:  # noqa: BLE001 - qualquer falha cai de volta para as regras
            return fallback

    @staticmethod
    def _parse(text: str, fallback: Classification) -> Classification:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return fallback
        try:
            data = json.loads(m.group(0))
            value = max(0, min(100, int(data["relevancia"])))
        except (ValueError, KeyError, TypeError):
            return fallback
        themes = [t for t in data.get("temas", []) if t in THEMES]
        return Classification(
            value=value,
            themes=themes or fallback.themes,
            reason=str(data.get("motivo", ""))[:140] or fallback.reason,
            by="claude",
        )
