# Inicia o HRBio Radar neste computador, com um endereco https temporario (Cloudflare Tunnel).
# Uso: de dois cliques em iniciar_windows.bat (na pasta do projeto). Veja docs/RODAR_NO_COMPUTADOR.md
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$config = Join-Path $raiz "config_local.ps1"

# 1) Arquivo de configuracao local (fica so neste computador; nunca vai para o GitHub)
if (-not (Test-Path $config)) {
    $bytes = New-Object byte[] 36
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $chave = [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_")
    $modelo = @'
# Configuracao local do HRBio Radar. NAO envie este arquivo a ninguem.
# SECRET_KEY: nao mude depois de conectar o Instagram (o token fica criptografado com ela).
$env:SECRET_KEY = "__CHAVE__"
# Usuario e senha para entrar no sistema (senha com 8 caracteres ou mais):
$env:ADMIN_USERNAME = "rodrigo"
$env:ADMIN_PASSWORD = "TROQUE-ESTA-SENHA"
# Chave da Anthropic (opcional; sem ela o sistema nao gera rascunhos):
$env:ANTHROPIC_API_KEY = ""
# ID e chave secreta do app da Meta (Configuracoes do app > Basico):
$env:FACEBOOK_APP_ID = ""
$env:FACEBOOK_APP_SECRET = ""
'@
    Set-Content -Path $config -Value $modelo.Replace("__CHAVE__", $chave) -Encoding UTF8
    Write-Host ""
    Write-Host "Criei o arquivo config_local.ps1 na pasta do projeto."
    Write-Host "Abra-o no Bloco de Notas, troque a senha e preencha o resto, salve e rode este script de novo."
    exit 0
}
. $config
if ($env:ADMIN_PASSWORD -eq "TROQUE-ESTA-SENHA" -or $env:ADMIN_PASSWORD.Length -lt 8) {
    Write-Host "Abra o config_local.ps1 e troque ADMIN_PASSWORD por uma senha de 8 caracteres ou mais."
    exit 1
}

# 2) Python e dependencias (so demora na primeira vez)
if (-not (Test-Path ".venv\Scripts\python.exe")) { python -m venv .venv }
$py = Join-Path $raiz ".venv\Scripts\python.exe"
Write-Host "Conferindo as dependencias..."
& $py -m pip install -q -r requirements.txt

# 3) Tunel gratuito da Cloudflare
$cf = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cf) {
    Write-Host "Falta instalar o cloudflared. Em um PowerShell, rode:  winget install --id Cloudflare.cloudflared"
    Write-Host "Depois feche e abra o PowerShell e rode este script de novo."
    exit 1
}
$log = Join-Path $env:TEMP "hrbio-tunel.log"
Remove-Item $log -ErrorAction SilentlyContinue
$tunel = Start-Process -FilePath $cf.Source -ArgumentList "tunnel", "--url", "http://localhost:8000" `
    -RedirectStandardError $log -PassThru -WindowStyle Hidden
$url = $null
for ($i = 0; $i -lt 60 -and -not $url; $i++) {
    Start-Sleep -Seconds 1
    $achou = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($achou) { $url = $achou.Matches[0].Value }
}
if (-not $url) {
    Stop-Process -Id $tunel.Id -Force -ErrorAction SilentlyContinue
    Write-Host "Nao consegui abrir o tunel. Confira a internet e tente de novo. Detalhes em $log"
    exit 1
}

# 4) Mantem o computador acordado enquanto o sistema roda
try {
    Add-Type -Namespace Win -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
    [Win.Power]::SetThreadExecutionState(0x80000001) | Out-Null
} catch { }

# 5) Sobe o sistema
$env:PUBLIC_BASE_URL = $url
$env:COOKIE_SECURE = "1"
Write-Host ""
Write-Host "Endereco do sistema (muda a cada vez que voce liga): $url"
Write-Host "Use SEMPRE esse endereco no navegador. Para desligar, feche esta janela ou aperte Ctrl+C."
Write-Host ""
Start-Process $url
try {
    & $py -m uvicorn app.web.main:app_factory --factory --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers "--forwarded-allow-ips=*"
} finally {
    Stop-Process -Id $tunel.Id -Force -ErrorAction SilentlyContinue
}
