# Conectar o Instagram da HRBio

O sistema **não guarda a senha** do Instagram. Você gera, na Meta (dona do Instagram), uma **autorização de
publicação** (um "token") e cola na tela **Instagram** do sistema. Ele guarda essa autorização criptografada e a
renova sozinho. Você pode apagá-la a qualquer momento pelo botão **Desconectar** ou revogando na Meta.

> Isto é o jeito oficial e permitido pelo Instagram. Programas que entram com login e senha para postar violam as
> regras e costumam levar ao bloqueio da conta.

## O que você precisa
1. O sistema **já no ar** com endereço `https://...` (veja `docs/COLOCAR_NO_AR.md`). O Instagram precisa baixar as
   imagens por esse endereço.
2. O Instagram da HRBio como conta **Profissional** (Comercial ou Criador). No app do Instagram:
   *Configurações → Tipo de conta e ferramentas → Mudar para conta profissional*. Se já é profissional, siga em frente.
3. Uma conta de **desenvolvedor da Meta** (gratuita): entre em **developers.facebook.com** com o seu Facebook e siga
   o cadastro (a Meta pede confirmação por telefone).

## Gerar o token
> A Meta muda os nomes das telas de vez em quando. O caminho é o mesmo; os rótulos podem variar um pouco.

1. Em **developers.facebook.com → Meus apps → Criar app**. Escolha o caso de uso de **gerenciar conteúdo/mensagens no
   Instagram** e dê um nome (por exemplo, "HRBio Radar").
2. No painel do app, abra **Instagram → API setup with Instagram login** (configuração da API com login do Instagram).
3. Em **Generate access tokens**, clique em **Add account**, entre com o Instagram da HRBio e autorize.
4. Clique em **Generate token** ao lado da conta e **copie o token inteiro** (é uma sequência longa de letras e números).
5. No sistema, abra **Instagram** (rodapé), cole o token em **Conectar** e confirme. Deve aparecer
   **"Conectado como @..."**.

O app pode ficar em **modo de desenvolvimento** (não precisa "publicar" o app nem passar por revisão da Meta): como a
conta é a sua própria, ela já tem permissão. Se a Meta pedir revisão (App Review) em algum passo, pare e me avise.

## Variante com login do Facebook (via Página)
A Meta nem sempre oferece o "login do Instagram" acima: em alguns apps só existe a **API do Instagram com login do
Facebook**. Nesse caso o Instagram da HRBio precisa estar ligado a uma **Página do Facebook**, e o sistema conecta por ela.

**O que fazer:**
1. **Ligar o Instagram à Página.** Na Página da HRBio: *Configurações → Contas vinculadas → Instagram*. A conta do
   Instagram precisa ser **Profissional**.
2. **Dar ao sistema o ID e a chave do app.** No painel do app: *Configurações do app → Básico*. Copie o **ID do app** e a
   **Chave secreta do app** e coloque nas variáveis `FACEBOOK_APP_ID` e `FACEBOOK_APP_SECRET` do servidor (na Render:
   *Environment*). A chave secreta é como uma senha: não envie a ninguém. Com essas duas variáveis preenchidas, a tela
   **Instagram** passa a usar este modo.
3. **Gerar o token.** Em **developers.facebook.com/tools/explorer**, escolha o seu app, clique em **Generate Access
   Token** e marque a **Página** e o **Instagram** da HRBio na autorização. Antes, adicione as permissões
   `instagram_basic`, `instagram_content_publishing`, `pages_read_engagement`, `pages_show_list` e `business_management`.
4. **Colar na tela Instagram** do sistema e confirmar. Ele troca o token (que dura cerca de 1 hora) por um de longa
   duração, pega o token da Página e mostra **"Conectado como @..."**.

O token da Página obtido assim **não vence**: não há renovação. Se você mudar a senha do Facebook ou remover o app,
conecte de novo. Se o token der acesso a **mais de um Instagram** (você administra outras Páginas), o sistema **não
escolhe sozinho**: digite o **@ do Instagram da HRBio** no campo "@ do Instagram" ao conectar. Dá para digitar o @ mesmo
quando há uma conta só.

## Testar antes de automatizar
1. No sistema, deixe a **publicação automática DESLIGADA** (é assim que nasce).
2. Tenha um rascunho **aprovado** e abra a página dele. Toque em **Publicar agora no Instagram**.
   **Atenção: é uma publicação de verdade, pública.** Escolha um conteúdo que você quer mesmo no perfil.
3. Em cerca de 1 minuto o post aparece no perfil e o rascunho vira **Publicado** com o link.
4. Deu certo? Na tela **Instagram**, ligue **Publicar sozinho os posts agendados** (e escolha se o story também vai).

## Como funciona o agendamento
- Você aprova o rascunho e escolhe a data no calendário.
- No dia, **a partir das 9h (Brasília)**, o sistema publica sozinho (o horário muda com a variável `PUBLISH_HOUR`).
- Posts **atrasados** (agendados para um dia que já passou sem sair) **não** são publicados sozinhos: você decide na
  tela Calendário entre publicar agora ou tirar do calendário.
- Se a publicação falhar (por exemplo, imagem recusada), o post continua agendado e a tela mostra o motivo, com o
  botão **Tentar publicar de novo**.
- Se a resposta do Instagram se perder no último passo, o sistema **não tenta de novo sozinho** (poderia duplicar o
  post): ele pede para você conferir no perfil e dizer se o post está lá.

## Limites e cuidados
- A Meta limita a 100 publicações por dia por conta (muito acima do que você vai usar).
- O Instagram só aceita imagens **JPEG**; o sistema converte automaticamente.
- **Reels em vídeo ainda não são publicados por aqui** (o sistema gera o roteiro, mas não o vídeo).
- O token vale **60 dias** e é renovado sozinho enquanto o sistema estiver ligado. Se ele ficar desligado por mais de
  60 dias, cole um token novo.
- **Nunca compartilhe o token** com ninguém, nem o envie por mensagem. Se vazar, clique em **Desconectar** e gere outro.
