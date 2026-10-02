# Colocar o sistema no ar (sem precisar de programador)

O que você vai fazer: criar uma conta na **Render** (um serviço de hospedagem), apontar para este repositório e
preencher 3 campos. Depois disso o sistema fica ligado 24 horas, coleta as normas todo dia de manhã e publica os
posts agendados no horário.

> **Custo:** precisa ser um plano **pago** da Render (aproximadamente **US$ 7 a 10 por mês**, mais o disco, que custa
> centavos). Confira o valor na própria tela antes de confirmar. O plano gratuito **não serve**: ele "dorme" depois
> de 15 minutos sem acesso e não tem disco permanente, então não publicaria no horário nem guardaria seus dados.

## Antes de começar, tenha à mão
- Sua conta do **GitHub** (a mesma onde está este repositório).
- Um **cartão de crédito** para o plano pago.
- A **chave da API da Anthropic** (para o Claude escrever os rascunhos). Sem ela o sistema funciona, mas não gera rascunhos.
- Uma **senha forte** e um **nome de usuário** para você entrar no sistema.

## Passo a passo
1. Entre em **render.com** e crie a conta usando **"Sign in with GitHub"**.
2. No painel, clique em **New +** e escolha **Blueprint**.
3. Conecte o repositório **Projeto-Marketing-Jur-dico** (a Render pede permissão ao GitHub; autorize só este repositório).
4. Escolha o **branch**. O ideal é o branch principal do repositório; se o código ainda estiver no branch
   `claude/ola-sa2pea`, escolha esse mesmo.
5. A Render lê o arquivo `render.yaml` e mostra o serviço **hrbio-radar** com um disco de 1 GB. Ela vai pedir 3 valores:
   - `ADMIN_USERNAME`: o nome de usuário que você quer (por exemplo `rodrigo`);
   - `ADMIN_PASSWORD`: sua senha (mínimo 8 caracteres). **Não use a senha de nenhum outro serviço**;
   - `ANTHROPIC_API_KEY`: a chave da Anthropic (pode deixar em branco e colocar depois).
6. **Escolha um plano pago** (o mais barato que ficar sempre ligado) e clique em **Apply** / **Deploy**.
7. Espere uns minutos. Quando ficar **Live**, clique no endereço do serviço (algo como
   `https://hrbio-radar.onrender.com`) e entre com o usuário e a senha do passo 5.
8. Abra a tela **Instagram** (link no rodapé): a linha "Endereço público" deve aparecer como **ok**.

Pronto. Daqui em diante, abra esse endereço no celular ou no computador. Dica: no celular, use "Adicionar à tela
inicial" para virar um ícone.

## Aviso por e-mail (opcional, recomendado)
Para o sistema te avisar quando aparecer uma norma muito relevante, já com o card pronto:
1. Use um e-mail seu para **enviar** os avisos (pode ser o e-mail da Hostinger da HRBio; o ideal é criar um só para isso,
   por exemplo `radar@seudominio.com.br`).
2. Na Render, abra o serviço → **Environment** e acrescente:
   - `SMTP_HOST`: o servidor de saída do seu e-mail (na Hostinger costuma ser `smtp.hostinger.com`; confira nas
     configurações do e-mail);
   - `SMTP_PORT`: `465` (ou `587`, se o seu servidor pedir);
   - `SMTP_USER`: o endereço de e-mail completo;
   - `SMTP_PASSWORD`: a senha desse e-mail;
   - `ALERT_EMAILS`: quem recebe o aviso (o seu e a da sua esposa, separados por vírgula).
3. Salve (a Render reinicia o serviço) e, na tela **Conta**, clique em **Enviar e-mail de teste**.

Você só recebe e-mail quando há novidade muito relevante. Sem novidade, sem e-mail.

## O que acontece sozinho depois
- **Todo dia às 7h (Brasília):** o sistema busca as normas novas (Diário Oficial, CONSEMA, FEPAM). Para a cada dois dias, defina `COLLECT_EVERY_DAYS=2`.
- **Logo depois:** se houver norma muito relevante, gera o rascunho e as artes (até 3 por dia) e te avisa por e-mail.
- **Dias de publicação, a partir das 9h:** posta o que você aprovou e agendou (só se a publicação automática estiver
  ligada, veja `docs/CONECTAR_INSTAGRAM.md`).
- **Autorização do Instagram:** renovada sozinha antes de vencer (vale 60 dias).

## Cuidados
- **Backup:** na tela **Conta** há o botão **Baixar backup**. Baixe de vez em quando (por exemplo, uma vez por mês) e
  guarde no seu computador.
- **Segurança:** a senha tem trava de tentativas (5 erros bloqueiam o usuário por 15 minutos). Troque a senha na tela
  **Conta** quando quiser. Para a sua esposa entrar, crie o usuário dela na mesma tela.
- **Não aumente o número de "workers"/instâncias**: o agendador roda dentro do sistema e duplicaria as publicações.
- **Atualizações do sistema:** quando houver código novo, a Render faz um novo deploy (alguns minutos). Durante esse
  tempo o sistema fica indisponível.
- **Se algo der errado:** na Render, abra o serviço e veja a aba **Logs**.

## Limites
- Os nomes dos botões da Render podem mudar com o tempo; o caminho é sempre: Blueprint → repositório → preencher
  variáveis → plano pago → Deploy.
- Este roteiro usa a Render por ser a opção que dá para fazer sem terminal. O mesmo sistema roda em qualquer
  hospedagem que aceite Docker (há um `Dockerfile` pronto), mas aí o passo a passo é outro.
