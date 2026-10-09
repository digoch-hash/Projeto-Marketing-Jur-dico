# Pega a credencial do Instagram (token da Pagina, que nao vence) para guardar como segredo na rotina do Claude.
# O TOKEN NAO APARECE NA TELA: ele vai direto para a area de transferencia (Ctrl+V / colar), para voce nao precisar
# ve-lo nem copia-lo. Nao o envie a ninguem; cole-o apenas no campo INSTAGRAM_TOKEN do ambiente da rotina.
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
$saida = & $py -m app.cli ig-token --username hrbioambiental
if ($LASTEXITCODE -ne 0) { exit 1 }
$j = ($saida -join "`n") | ConvertFrom-Json
Write-Host ""
Write-Host "Conta:             $($j.conta)"
Write-Host "INSTAGRAM_USER_ID: $($j.INSTAGRAM_USER_ID)"
Write-Host "INSTAGRAM_LOGIN:   $($j.INSTAGRAM_LOGIN)"
Write-Host ""
Write-Host "O TOKEN foi copiado para a area de transferencia, mas nao aparece aqui."
Write-Host "Va ao campo INSTAGRAM_TOKEN do ambiente da rotina e cole (Ctrl+V). NAO cole em nenhum chat."
Set-Clipboard -Value $j.INSTAGRAM_TOKEN
