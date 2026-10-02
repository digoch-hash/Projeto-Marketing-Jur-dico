# HRBio · Radar de Normas

Sistema web para acompanhar o que saiu de novo nas fontes oficiais de meio ambiente (RS em primeiro
lugar), separar o que importa para os clientes da HRBio e decidir o que vira post no Instagram.

**Fase 1:** coletar → filtrar por relevância → lista de novidades no celular.
**Fase 2:** ao marcar "Quero postar", o sistema gera o rascunho do conteúdo para você revisar e aprovar.
**Fase 3:** calendário editorial com ritmo de um post dia sim, dia não.
**Fase 4:** artes prontas para postar (carrossel e story/Status) no estilo da HRBio.
**Fase 5:** publicação agendada no Instagram da HRBio, coleta diária e hospedagem.
**Fase 6:** alerta por e-mail só quando há norma muito relevante, já com o card pronto para revisar.
Métricas e reels em vídeo entram nas próximas fases.

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

## Calendário editorial (fase 3)

O caminho de cada norma: **nova → rascunho → aprovado → agendado → publicado**.

- Depois de aprovar o rascunho, o sistema **sugere a próxima data livre** respeitando o ritmo
  (`MIN_GAP_DAYS = 2` em `app/editorial.py`: nunca dois posts a menos de 2 dias). Você pode escolher outra
  data; se ela quebrar o ritmo, a tela avisa, mas deixa.
- A tela **Calendário** mostra os próximos 14 dias (post, dia livre ou descanso), os aprovados sem data, os
  **atrasados** (agendados que passaram sem marcar como publicados) e os publicados recentes.
- Publicar ainda é manual: use "Já publiquei" depois de postar. Posts publicados contam para o intervalo.
- Editar um conteúdo aprovado ou agendado **cancela a aprovação e a data**: ele precisa ser aprovado de novo.
- "Hoje" é sempre a data de Brasília, mesmo com o servidor em UTC.
- Bancos criados em fases anteriores ganham as colunas novas sozinhos ao iniciar (migração leve, sem perda de dados).

## Artes (fase 4)

No rascunho, o botão **Gerar artes** cria, em poucos segundos:

- o **carrossel** (1080×1350, formato 4:5 do feed): um slide por item do rascunho, com contador e "arraste";
- uma imagem **story** (1080×1920) com o gancho e o texto do Status do WhatsApp, para Status e stories.

O visual segue os stories da marca: foto escurecida, título grande em Montserrat extranegrito com a última
linha em verde-limão, traço vertical fino e painel verde-escuro arredondado. O texto sempre cabe na arte
(a fonte diminui sozinha e, no limite, corta com "…"). Toque numa arte para baixar, ou baixe tudo em `.zip`.

Os **logos da HRBio já vêm no sistema** (`app/art/brand/`): o de texto branco entra direto sobre a arte escura e o
de texto escuro só é usado, sobre uma plaquinha clara, se o outro faltar. A tela **Marca** (link no rodapé) permite
trocá-los (e voltar ao padrão) e receber **fotos de campo** (JPG, PNG ou WEBP, até 25 MB). Com fotos, o fundo é
automático (gira entre elas) ou escolhido por rascunho. **Sem fotos, o sistema desenha uma paisagem verde** (colinas
em camadas com neblina, em 4 climas: manhã, fim de tarde, vale e floresta), sempre igual para o mesmo item.
Editar o texto do rascunho apaga as artes antigas, para nunca sair arte desatualizada.

Arquivos enviados e artes ficam em `DATA_DIR` (padrão `data/`), fora do banco e fora do Git.
A fonte Montserrat (licença SIL OFL, em `app/art/fonts/OFL.txt`) vai junto no repositório.

## Publicação no Instagram e hospedagem (fase 5)

- **No ar sem programador:** `docs/COLOCAR_NO_AR.md` (Render, plano pago, cerca de 10 minutos).
- **Conectar o Instagram:** `docs/CONECTAR_INSTAGRAM.md`. Usa a API oficial da Meta; o sistema nunca guarda a senha
  do Instagram, só um token **criptografado** (chave derivada do `SECRET_KEY`) que ele renova sozinho.
- **Publicação:** carrossel (e, se ligado, o story) no dia agendado, a partir de `PUBLISH_HOUR` (padrão 9h de Brasília).
  Só sai o que foi aprovado e agendado. Atrasados não saem sozinhos. Falha antes do último passo: continua agendado e
  mostra o motivo. Resposta perdida no último passo: marca **"a confirmar"** e **nunca** tenta de novo sozinho
  (evita post duplicado).
- **Rotina diária:** coleta das fontes às 7h e renovação do token, no mesmo processo (use **um único worker**).
- **Imagens para o Instagram:** servidas em links públicos assinados e temporários (6 h), só do arquivo pedido.
- **Segurança do servidor público:** trava de tentativas de senha, cookie seguro, cabeçalhos de segurança, recusa
  subir com `SECRET_KEY` fraca quando `COOKIE_SECURE=1`, usuário inicial por `ADMIN_USERNAME`/`ADMIN_PASSWORD`
  (sem precisar de terminal), troca de senha e criação de usuários pela tela **Conta**, e **backup** em um clique.

| Variável | Para quê |
|---|---|
| `SECRET_KEY` | Assina o login e criptografa o token do Instagram (a Render gera sozinha) |
| `COOKIE_SECURE=1` | Obrigatório em produção (HTTPS) |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Cria o primeiro usuário no primeiro start (nunca troca uma senha existente) |
| `PUBLIC_BASE_URL` | Endereço https do sistema (na Render, `RENDER_EXTERNAL_URL` é usado sozinho) |
| `PUBLISH_HOUR` | Hora (Brasília) a partir da qual os agendados saem; padrão `9` |
| `ANTHROPIC_API_KEY` | Rascunhos e filtro de relevância com o Claude |
| `DISABLE_SCHEDULER=1` | Desliga a rotina em segundo plano (útil em testes) |

## Alerta e card automático (fase 6)

Princípio: **só publicamos quando há algo relevante**. Não existe cota de posts; o calendário só evita dois posts colados
(`MIN_GAP_DAYS`).

- **Monitoramento:** todo dia às 7h (Brasília), enquanto o sistema estiver no ar. Para a cada 2 dias: `COLLECT_EVERY_DAYS=2`.
- **Card automático:** depois da coleta, cada norma com nota ≥ `ALERT_MIN_RELEVANCE` (padrão 60) e sem rascunho ganha o
  rascunho **e as artes**, com teto de `AUTO_DRAFT_MAX_PER_DAY` por dia (padrão 3) para limitar o gasto com o Claude.
  Exige `ANTHROPIC_API_KEY`; desligue com `AUTO_DRAFT=0`.
- **E-mail:** um único e-mail por coleta, **só se houver novidade muito relevante** ("Sem novidade, sem e-mail"), dizendo
  quais cards estão prontos e levando direto ao rascunho. Falha de e-mail não perde a novidade: entra no próximo aviso.
  Teste o envio na tela **Conta**.
- **Radar:** faixa no topo com quantas normas muito relevantes esperam a sua decisão.
- **Nada é publicado sozinho** por causa disso: o card espera a sua revisão e aprovação.
- **Calibragem da nota:** resolução do CONSEMA e norma técnica sobem; acordo de cooperação, assunto interno do órgão
  (câmaras técnicas, eleições de comitê, grupos de trabalho) e licença de terceiros descem. Foi ajustada com 8 atos reais;
  revise depois de algumas semanas de uso (e, com a chave da Anthropic, o Claude refina a nota).

| Variável | Para quê |
|---|---|
| `ALERT_MIN_RELEVANCE` | Nota mínima para "muito relevante" (padrão 60) |
| `AUTO_DRAFT`, `AUTO_DRAFT_MAX_PER_DAY` | Liga/desliga e limita o card automático (padrão ligado, 3 por dia) |
| `COLLECT_EVERY_DAYS` | 1 = todo dia, 2 = a cada dois dias |
| `MIN_GAP_DAYS` | Espaçamento mínimo entre posts (2 = um dia de intervalo; 1 = só não repetir o dia) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | Conta de e-mail que envia o aviso (porta 465 ou 587) |
| `ALERT_EMAILS` | Quem recebe (separe por vírgula) |

## Rotina diária sem servidor (alternativa à hospedagem)

Para quem posta à mão e não quer manter um servidor ligado, uma **rotina agendada do Claude** faz o trabalho todo dia
de manhã, usando só estes comandos (sem `ANTHROPIC_API_KEY`: quem escreve o card é o próprio Claude da rotina):

```bash
python -m app.cli daily                      # normas muito relevantes de ONTEM (JSON com o texto completo); --date AAAA-MM-DD
python -m app.cli render-card --item-id N --content card.json --out pasta   # artes + legenda.txt + .zip
```

Cada execução cobre exatamente um dia (ontem, em Brasília), sem memória entre execuções: rodando todo dia, cada dia
é coberto uma vez e nada se repete. Dia sem norma relevante: a rotina não entrega nada.

## Rodar localmente

```bash
pip install -r requirements-dev.txt   # (em produção basta requirements.txt)
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
- Em produção use HTTPS e `COOKIE_SECURE=1` (veja `docs/COLOCAR_NO_AR.md`).
- A publicação no Instagram foi testada só contra um Instagram simulado (a API real exige o seu token): faça o teste
  com "Publicar agora" antes de ligar a publicação automática.
- Reels em vídeo e métricas ainda não existem.

## Próximas fases

1. ~~Gerador de rascunhos~~ e ~~calendário editorial~~ (feitos).
2. ~~Artes com a identidade da HRBio~~ (feito).
3. ~~Publicação agendada no Instagram~~ (feito).
4. Métricas (alcance, compartilhamentos, salvamentos), reels em vídeo, avisos por WhatsApp e demais fontes (DOU, ANM, IBAMA, ANA).
