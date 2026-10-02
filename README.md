# HRBio · Radar de Normas

Sistema web para acompanhar o que saiu de novo nas fontes oficiais de meio ambiente (RS em primeiro
lugar), separar o que importa para os clientes da HRBio e decidir o que vira post no Instagram.

**Fase 1:** coletar → filtrar por relevância → lista de novidades no celular.
**Fase 2:** ao marcar "Quero postar", o sistema gera o rascunho do conteúdo para você revisar e aprovar.
Artes, publicação, calendário e métricas entram nas próximas fases.

## O que já funciona

| Fonte | Como é lida | O que traz |
|---|---|---|
| **Diário Oficial do RS** | API pública do próprio site | Portarias SEMA/FEPAM, diretrizes técnicas, decretos, avisos e termos que citam meio ambiente |
| **CONSEMA** | Página de resoluções da SEMA | Resolução (nº, ementa e PDF oficial) |
| **FEPAM** | Comunicados no site da fundação | Notícias e comunicados (as portarias da FEPAM vêm pelo Diário Oficial) |

Cada ato recebe uma **nota de relevância (0–100)** e **temas** (licenciamento, mineração, recursos
hídricos, resíduos, fauna e flora…). Atos de pessoal, licitações e designação de fiscais são descartados.
Com `ANTHROPIC_API_KEY`, o Claude refina a nota dos candidatos e escreve o "por que importa";
sem a chave, funcionam só as regras de palavras-chave.

Na tela, cada novidade pode ser marcada como **Quero postar**, **Depois** ou **Ignorar**.

## Rascunho do post (fase 2)

Ao marcar **Quero postar** (ou no botão **Gerar rascunho**), o sistema:

1. busca o **texto completo** do ato na fonte oficial (para resoluções do CONSEMA, o Claude lê o próprio PDF);
2. pede ao Claude um pacote com **carrossel (6–8 slides), roteiro de reel, legenda, texto para o Status do
   WhatsApp e hashtags**, sempre com número da norma, link oficial e chamada para a HRBio;
3. mostra no topo **"Confira no texto oficial antes de publicar"**: os pontos que a IA não conseguiu
   confirmar (datas, prazos, trechos interpretados). O Claude só pode usar fatos que estão no texto;
4. você edita qualquer campo, copia a legenda ou o Status com um toque e **aprova**.
   Editar depois de aprovar cancela a aprovação.

Nada é publicado automaticamente. O rascunho usa `ANTHROPIC_DRAFT_MODEL` (padrão `claude-opus-5-5`) com
`fallbacks` ligado, e o filtro de relevância usa `ANTHROPIC_MODEL` (padrão `claude-haiku-4-5`, bem mais barato).

## Rodar localmente

```bash
pip install -r requirements.txt
cp .env.example .env          # ajuste SECRET_KEY; a chave da Anthropic é opcional
export $(grep -v '^#' .env | xargs)

python -m app.cli create-user rodrigo        # pede a senha (mín. 8 caracteres)
python -m app.cli collect --days 7           # busca os últimos 7 dias nas fontes
python -m uvicorn app.web.main:app_factory --factory --port 8000
```

Abra http://localhost:8000. Crie um usuário para cada pessoa que vai usar.
No dia a dia, agende `python -m app.cli collect` (cron) uma vez por manhã, ou use o botão
**"Buscar novidades agora"** na tela. A coleta de 7 dias leva cerca de 1 minuto.

## Testes

```bash
python -m pytest -q
```

Os parsers são testados com trechos reais das fontes (`tests/fixtures`). Se um site mudar de layout,
a coleta daquela fonte falha com mensagem clara e aparece em vermelho na tela; as demais seguem normais.

## Banco de dados

SQLite por padrão (`data/app.db`). Para PostgreSQL, defina `DATABASE_URL`
(`postgresql+psycopg://...`) e instale `psycopg[binary]`.

## Limites conhecidos

- Os atos do Diário Oficial são lidos dia a dia; só entram os que passam pelo filtro de relevância.
- A data das resoluções do CONSEMA vem da data de envio do PDF (a página não publica a data da reunião).
- O DOU (ANM, IBAMA, ANA) e o SINCAGE (legislação estadual consolidada) ainda não foram ligados:
  são as próximas fontes.
- Em produção use HTTPS e `COOKIE_SECURE=1`.

## Próximas fases

1. ~~Gerador de rascunhos~~ (feito).
2. Calendário editorial (novo → rascunho → aprovado → publicado), um post dia sim, dia não.
3. Artes com a identidade visual da HRBio e publicação no Instagram só depois do seu OK.
4. Reels em vídeo, métricas (alcance, compartilhamentos), avisos por WhatsApp e demais fontes.
