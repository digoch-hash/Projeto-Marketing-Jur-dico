import pytest

from app.relevance import ClaudeClassifier, Classification, MIN_STORE, quick_score, score_item
from app.sources.base import RawItem


def item(title, summary="", doc_type="", issuer=""):
    return RawItem("x", "1", title, "http://x", summary=summary, doc_type=doc_type, issuer=issuer)


def test_norma_ambiental_de_orgao_ambiental_pontua_alto():
    c = score_item(item(
        "Resolução CONSEMA 551/2026",
        "Estabelece diretrizes para recuperação de áreas mineradas (PRAD) e licenciamento ambiental da mineração",
        doc_type="Resolução CONSEMA", issuer="CONSEMA - Conselho Estadual do Meio Ambiente",
    ))
    assert c.value >= 70
    assert "Mineração" in c.themes and "Licenciamento ambiental" in c.themes


def test_ruido_administrativo_fica_abaixo_do_corte():
    for title, doc_type in [
        ("HOMOLOGAÇÃO E ADJUDICAÇÃO. Ata de Registro de Preços N° 012/2026", "Homologações"),
        ("Assunto: Afastamentos - SCC", "Atos de Pessoal"),
        ("Nomeação de servidor para cargo em comissão", "Atos de Pessoal"),
    ]:
        assert score_item(item(title, doc_type=doc_type)).value < MIN_STORE


def test_palavra_inteira_nao_gera_falso_positivo():
    # "app" aparece dentro de "aplicacao"/"apple"; "car" dentro de "carro"
    assert score_item(item("Aplicação do carro oficial")).themes == []


def test_acentos_sao_ignorados():
    assert "Resíduos" in score_item(item("Gestão de resíduos sólidos e MTR")).themes


def test_quick_score_peneira_trechos_curtos():
    assert quick_score("PORTARIA FEPAM N°. 634/2026 O PRESIDENTE DA FUNDAÇÃO ESTADUAL DE PROTEÇÃO AMBIENTAL") > 0
    assert quick_score("Contrato de aquisição de material de escritório") == 0


class FakeClient:
    def __init__(self, text=None, error=None):
        self._text, self._error = text, error
        self.messages = self
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error

        class Block:
            type = "text"
            text = self._text

        class Msg:
            content = [Block()]

        return Msg()


def test_claude_substitui_a_nota_das_regras():
    fallback = Classification(50, ["Mineração"], "regras")
    client = FakeClient('Claro! {"relevancia": 88, "temas": ["Mineração", "Inventado"], "motivo": "Muda o PRAD de areeiras"}')
    out = ClaudeClassifier("k", "m", client=client).classify(item("Res 551"), fallback)
    assert (out.value, out.by) == (88, "claude")
    assert out.themes == ["Mineração"]  # tema fora da lista e descartado
    assert out.reason == "Muda o PRAD de areeiras"
    assert client.calls[0]["model"] == "m"


def test_claude_com_falha_ou_resposta_ruim_cai_para_as_regras():
    fallback = Classification(50, ["Mineração"], "regras")
    for client in (
        FakeClient(error=RuntimeError("timeout")),
        FakeClient("sem json aqui"),
        FakeClient('{"relevancia": "alta"}'),
    ):
        assert ClaudeClassifier("k", "m", client=client).classify(item("x"), fallback) is fallback


def test_claude_limita_nota_a_0_100():
    out = ClaudeClassifier("k", "m", client=FakeClient('{"relevancia": 250, "temas": [], "motivo": "x"}')).classify(
        item("x"), Classification(30)
    )
    assert out.value == 100


def test_outorga_financeira_e_enquadramento_de_fundopem_nao_sao_recursos_hidricos():
    for title in (
        "SÚMULA COLETIVA DE TERMOS DE OUTORGA DE SUBVENÇÃO ECONÔMICA DE PROJETOS",
        "PARECER DE ENQUADRAMENTO Nº 131/2026 - FUNDOPEM RECUPERA O GRUPO DE ANÁLISE",
    ):
        assert "Recursos hídricos" not in score_item(item(title)).themes
    assert "Recursos hídricos" in score_item(item("Portaria sobre outorga de direito de uso de recursos hídricos")).themes


def test_nome_de_pessoa_ana_nao_vira_agencia_de_aguas():
    c = score_item(item("Assunto: Revisão de Proventos Nome: ANA MARA MACHADO", doc_type="Previdenciária"))
    assert c.value < MIN_STORE


def test_portaria_de_designacao_de_fiscais_e_ruido_mesmo_com_titulo_generico():
    c = score_item(item(
        "PORTARIA SEMA Nº 176, DE 28 DE AGOSTO DE 2026",
        "A SECRETÁRIA DE ESTADO DO MEIO AMBIENTE E INFRAESTRUTURA RESOLVE: Art. 1º Designar os servidores "
        "públicos abaixo listados para atuarem na fiscalização do Termo de Cooperação, gestão de resíduos",
        doc_type="Portarias", issuer="Secretaria do Meio Ambiente e Infraestrutura",
    ))
    assert c.value < 40


# ---- calibragem com atos reais (DOE/CONSEMA de 2026): quem decide postar depende disso
REAIS = [
    # (titulo, ementa/texto, tipo, orgao, deve ser "muito relevante"?)
    ("Resolução CONSEMA 554/2026", "Altera a Resolução CONSEMA nº 512, de 08 de agosto de 2024, que dispõe sobre os procedimentos de Licenciamento Ambiental dos empreendimentos de irrigação.", "Resolução CONSEMA", "CONSEMA - Conselho Estadual do Meio Ambiente (RS)", True),
    ("Resolução CONSEMA 551/2026", "Estabelece as diretrizes e os procedimentos para a recuperação de áreas mineradas (PRAD).", "Resolução CONSEMA", "CONSEMA - Conselho Estadual do Meio Ambiente (RS)", True),
    ("Súmula da Diretriz Técnica FEPAM nº. 02/2017", "Objeto: realizada atualização da diretriz técnica de licenciamento ambiental", "Atos Administrativos", "Fundação Estadual de Proteção Ambiental", True),
    ("Resolução CONSEMA 553/2026", "Altera Resolução 296/2015 que dispõe sobre a reformulação das composições das câmaras técnicas.", "Resolução CONSEMA", "CONSEMA", False),
    ("SÚMULA DO TERMO DE COOPERAÇÃO MATA ATLÂNTICA - SEMA/FEPAM - MUNICÍPIO DE PICADA CAFÉ", "Termo de cooperação técnica para o Plano da Mata Atlântica e licenciamento", "Convênios", "Secretaria do Meio Ambiente e Infraestrutura", False),
    ("AVISO SEMA - CRH/RS Nº 10/2026", "Segunda Chamada para Cadastramento de Entidades para o Processo Eleitoral do Comitê da Bacia Hidrográfica dos Rios Vacacai", "Atos Administrativos", "Secretaria do Meio Ambiente e Infraestrutura", False),
    ("RENOVAÇÃO DE LICENÇA DE OPERAÇÃO COOPERATIVA REGIONAL", "Comunica a renovação de licença de operação LO nº 123/2026 emitida pela FEPAM", "Licença de Operação", "COOPERATIVA REGIONAL", False),
    ("PORTARIA FEPAM N° 630/2026 Institui, em caráter permanente, o Grupo de Trabalho", "Institui grupo de trabalho para qualidade ambiental e licenciamento", "Atos Administrativos", "Fundação Estadual de Proteção Ambiental", False),
]


@pytest.mark.parametrize("title,text,doc_type,issuer,relevante", REAIS)
def test_norma_que_muda_a_vida_do_cliente_passa_do_corte_de_aviso_e_o_resto_nao(title, text, doc_type, issuer, relevante):
    value = score_item(item(title, text, doc_type, issuer)).value
    assert (value >= 60) is relevante, f"{title}: {value}"
    if not relevante:
        assert value < 40  # nem aparece na lista padrao


def test_resolucao_do_consema_vence_acordo_administrativo():
    resolucao = score_item(item("Resolução CONSEMA 554/2026", "Licenciamento ambiental da irrigação", "Resolução CONSEMA", "CONSEMA")).value
    acordo = score_item(item("Termo de cooperação técnica", "licenciamento ambiental", "Convênios", "Secretaria do Meio Ambiente")).value
    assert resolucao > acordo + 20
