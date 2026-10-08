# Pega a credencial do Instagram (token da Pagina, que nao vence) para guardar como segredo na rotina do Claude.
# O token aparece SO nesta janela. Nao o envie a ninguem; coloque-o apenas nos segredos do ambiente da rotina.
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$config = Join-Path $raiz "config_local.ps1"
if (-not (Test-Path $config)) { Write-Host "Falta o config_local.ps1. Rode antes o iniciar_windows.bat."; exit 1 }
. $config
if (-not $env:FACEBOOK_APP_ID -or -not $env:FACEBOOK_APP_SECRET) { Write-Host "Preencha FACEBOOK_APP_ID e FACEBOOK_APP_SECRET no config_local.ps1."; exit 1 }
$py = Join-Path $raiz ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "Rode antes o iniciar_windows.bat uma vez, para instalar as dependencias."; exit 1 }
Write-Host "No Explorador da Graph API (app HRBio Radar), gere um token novo, copie e cole abaixo."
& $py -m app.cli ig-token --username hrbioambiental
