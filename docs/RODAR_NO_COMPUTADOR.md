# Rodar o sistema no seu computador (Windows), de graça

Alternativa à hospedagem paga (`docs/COLOCAR_NO_AR.md`). O sistema roda no seu computador e um **túnel gratuito da
Cloudflare** dá o endereço `https://` que o Instagram precisa para baixar as imagens.

## Limites (leia antes)
- O computador precisa estar **ligado e conectado à internet** na hora de publicar. Ligado fora do horário, nada sai.
  O script impede a suspensão automática enquanto roda, mas **fechar a janela ou desligar o PC** para tudo.
- O **endereço muda toda vez** que você liga. Use sempre o que o script mostra (ele abre o navegador sozinho).
- Esse endereço é público: qualquer pessoa que o descobrisse veria a tela de login. Use uma **senha forte**.
- Guarde o `config_local.ps1` só no seu computador: tem senhas e a chave do app da Meta. Ele **não vai** para o GitHub.

## Instalar (uma vez)
1. **Python 3.12** (você já tem). Confira no PowerShell: `python --version`.
2. **Cloudflared**, no PowerShell: `winget install --id Cloudflare.cloudflared`. Depois feche e abra o PowerShell.
3. **O código do sistema**: baixe o ZIP do repositório no GitHub (botão verde **Code → Download ZIP**, no branch certo) e
   extraia numa pasta, por exemplo `C:\HRBio`.

## Ligar (toda vez)
1. Na pasta do projeto, dê dois cliques em **`iniciar_windows.bat`**.
2. Na **primeira vez** ele cria o `config_local.ps1` e para. Abra esse arquivo no Bloco de Notas, troque a senha,
   preencha o `FACEBOOK_APP_ID` e o `FACEBOOK_APP_SECRET` (Configurações do app → Básico, no painel da Meta), salve e
   dê dois cliques no `.bat` de novo.
3. Ele instala o que falta (a primeira vez demora), abre o túnel e o navegador. Entre com o usuário e a senha do
   `config_local.ps1`.
4. Abra a tela **Instagram**: a linha "Endereço público" deve aparecer como **ok**.
5. Conecte o Instagram (veja "Variante com login do Facebook" em `docs/CONECTAR_INSTAGRAM.md`).

Para desligar, feche a janela do PowerShell ou aperte **Ctrl+C**.

## Cuidados
- **Não mude a `SECRET_KEY`** do `config_local.ps1` depois de conectar o Instagram: o token fica criptografado com ela.
- Os dados ficam na pasta `data` do projeto. Faça o **backup** de vez em quando (botão na tela **Conta**).
- Para atualizar o sistema, baixe o ZIP novo e copie por cima, **mantendo** a pasta `data` e o `config_local.ps1`.
