# Atualiza o HRBio Radar neste computador com a versao mais recente do GitHub.
# Mantem a pasta data (seus dados) e o config_local.ps1 (suas senhas).
$ErrorActionPreference = "Stop"
$ramo = "claude/nifty-mccarthy-ns3p5f"
$raiz = Split-Path -Parent $PSScriptRoot
$tmp = Join-Path $env:TEMP "hrbio-atualizacao"
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
New-Item -ItemType Directory -Path $tmp | Out-Null
$zip = Join-Path $tmp "codigo.zip"
Write-Host "Baixando a versao mais recente..."
Invoke-WebRequest -Uri "https://github.com/digoch-hash/Projeto-Marketing-Jur-dico/archive/refs/heads/$ramo.zip" -OutFile $zip
Expand-Archive -Path $zip -DestinationPath $tmp -Force
$novo = Get-ChildItem $tmp -Directory | Select-Object -First 1
foreach ($item in @("app", "docs", "scripts", "requirements.txt", "requirements-dev.txt", "iniciar_windows.bat", "atualizar_windows.bat", "pegar_credencial_windows.bat", ".env.example", "README.md")) {
    $origem = Join-Path $novo.FullName $item
    if (-not (Test-Path $origem)) { continue }
    $destino = Join-Path $raiz $item
    if ((Get-Item $origem).PSIsContainer) {
        Get-ChildItem $origem -Recurse -File | ForEach-Object {
            $rel = $_.FullName.Substring($origem.Length).TrimStart("\")
            if ($rel -eq "atualizar_windows.ps1" -and $item -eq "scripts") { return }   # nao sobrescreve este script enquanto ele roda
            $alvo = Join-Path $destino $rel
            New-Item -ItemType Directory -Path (Split-Path $alvo) -Force | Out-Null
            Copy-Item $_.FullName $alvo -Force
        }
    } else {
        Copy-Item $origem $destino -Force
    }
}
Remove-Item $tmp -Recurse -Force
Write-Host ""
Write-Host "Pronto. Atualizado. Seus dados e o config_local.ps1 foram mantidos."
Write-Host "Agora de dois cliques em iniciar_windows.bat para ligar o sistema."
