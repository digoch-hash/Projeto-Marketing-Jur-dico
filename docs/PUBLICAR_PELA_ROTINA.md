# Publicar sozinho pela rotina diária (sem servidor, sem chave da Anthropic)

A rotina do Claude ("Radar HRBio: normas relevantes de ontem") escreve o card. Para ela **também publicar** no
Instagram, ela precisa de duas coisas, ambas gratuitas:

1. **Credencial do Instagram** nas variáveis secretas do ambiente da rotina.
2. **Um endereço público para as imagens.** O script `scripts/publicar_card.sh` guarda os JPEG no ramo
   `cards-publicos` deste repositório (que é público) e usa o endereço `raw.githubusercontent.com`. Ele mantém só os
   5 cards mais recentes; o Instagram guarda a própria cópia.

## Pegar a credencial (uma vez, no seu computador)
Com o sistema configurado como em `docs/RODAR_NO_COMPUTADOR.md` (`FACEBOOK_APP_ID` e `FACEBOOK_APP_SECRET`):
1. Gere um token no Explorador da Graph API (app **HRBio Radar**, as 5 permissões) e copie.
2. No PowerShell, na pasta do projeto, depois de rodar `. .\config_local.ps1`:
   `.venv\Scripts\python -m app.cli ig-token --username hrbioambiental`
   (ele pergunta o token no terminal, sem deixar no histórico).
3. Ele mostra `INSTAGRAM_USER_ID` e `INSTAGRAM_TOKEN`. O token da Página **não vence**.

## Guardar no ambiente da rotina
Nas configurações do ambiente de nuvem do Claude Code, em **Segredos** (variáveis de ambiente), crie:
`INSTAGRAM_USER_ID`, `INSTAGRAM_TOKEN` e `INSTAGRAM_LOGIN=facebook`. Nunca coloque o token no texto da rotina nem no
GitHub. Se vazar: revogue o app na Meta e gere de novo.

## Como a rotina publica
Depois do `render-card`: `PY=... bash scripts/publicar_card.sh /tmp/cards/ID ID`. O script sobe as imagens, espera o
endereço responder e publica o carrossel e os dois stories. Com `--dry-run`, só hospeda e confere, sem publicar.
