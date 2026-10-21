# Gera o token de PAGINA do Instagram da HRBio (nao vence) e copia para a area de transferencia.
# Nao precisa de Python, config_local nem de nada instalado. O token NAO aparece na tela e nao e gravado em arquivo.
$ErrorActionPreference = "Stop"
$v = "v23.0"
function DaAreaDeTransferencia($instrucao) {
    # Le o que voce copiou (Ctrl+C) sem precisar colar na janela. Nada e mostrado.
    Read-Host $instrucao | Out-Null
    $t = Get-Clipboard -Raw
    if ($null -eq $t) { $t = "" }
    return $t
}
function Limpa($t) { return (($t.ToCharArray() | Where-Object { -not [char]::IsWhiteSpace($_) -and -not [char]::IsControl($_) }) -join "").Trim([char]39, [char]34) }
function MsgErro($e) {
    $m = $e.Exception.Message
    try { $j = $e.ErrorDetails.Message | ConvertFrom-Json; if ($j.error.message) { $m = $j.error.message } } catch {}
    return $m
}
try {
    Write-Host ""
    Write-Host "Vou pedir 3 coisas. Os segredos NAO aparecem na tela nem sao gravados: voce so copia (Ctrl+C) e aperta Enter."
    Write-Host ""
    $appId = Limpa (Read-Host "1) ID do aplicativo (Meta > app HRBio Radar > Configuracoes do app > Basico)")
    Write-Host ""
    Write-Host "2) Chave secreta do aplicativo: na tela da Meta (Configuracoes do app > Basico) clique em Mostrar e COPIE (Ctrl+C)."
    $appSecret = Limpa (DaAreaDeTransferencia "   Ja copiou? Volte aqui e aperte Enter")
    Write-Host "   Recebi $($appSecret.Length) caracteres (a chave costuma ter 32)."
    Write-Host ""
    Write-Host "3) Token do Explorador da Graph API: la, clique no icone de copiar ao lado do campo Token de acesso (comeca com EAA)."
    $curto = Limpa (DaAreaDeTransferencia "   Ja copiou? Volte aqui e aperte Enter")
    Write-Host "   Recebi $($curto.Length) caracteres (o token costuma ter mais de 150)."
    if ($appSecret.Length -lt 20) { throw "A chave secreta nao foi copiada (recebi $($appSecret.Length) caracteres). Na Meta, clique em Mostrar e copie com Ctrl+C antes de apertar Enter." }
    if ($curto.Length -lt 100 -or -not $curto.StartsWith("EA")) { throw "O token do Explorador parece incompleto ($($curto.Length) caracteres). Ele tem mais de 150 e comeca com EAA. Copie de novo pelo icone de copiar." }
    Write-Host ""
    Write-Host "Falando com a Meta..."
    $url1 = "https://graph.facebook.com/$v/oauth/access_token?grant_type=fb_exchange_token&client_id=$appId&client_secret=$appSecret&fb_exchange_token=$curto"
    $longo = (Invoke-RestMethod -Uri $url1 -Method Get).access_token
    if (-not $longo) { throw "A Meta nao devolveu o token de longa duracao." }
    $url2 = "https://graph.facebook.com/$v/me/accounts?fields=name,access_token,instagram_business_account{id,username}&limit=100&access_token=$longo"
    $paginas = (Invoke-RestMethod -Uri $url2 -Method Get).data
    $achadas = @($paginas | Where-Object { $_.instagram_business_account -and $_.access_token -and $_.instagram_business_account.username -eq "hrbioambiental" })
    if ($achadas.Count -ne 1) {
        $nomes = ($paginas | Where-Object { $_.instagram_business_account } | ForEach-Object { "@" + $_.instagram_business_account.username }) -join ", "
        throw "Nao achei exatamente uma Pagina ligada ao @hrbioambiental. Contas encontradas: $nomes. No Explorador, marque as permissoes e escolha a Pagina da HRBio ao autorizar."
    }
    $pag = $achadas[0]
    $tokenPagina = $pag.access_token
    $igId = $pag.instagram_business_account.id
    $dbg = (Invoke-RestMethod -Uri "https://graph.facebook.com/debug_token?input_token=$tokenPagina&access_token=$appId|$appSecret" -Method Get).data
    Set-Clipboard -Value $tokenPagina
    Write-Host ""
    Write-Host "PRONTO." -ForegroundColor Green
    Write-Host "Conta:              @$($pag.instagram_business_account.username)"
    Write-Host "INSTAGRAM_USER_ID:  $igId"
    Write-Host "INSTAGRAM_LOGIN:    facebook"
    if ($dbg.expires_at -eq 0) { Write-Host "Validade do token:  NUNCA VENCE" -ForegroundColor Green } else { Write-Host "Validade do token:  atencao, tem vencimento (expires_at=$($dbg.expires_at))" -ForegroundColor Yellow }
    Write-Host ""
    Write-Host "O TOKEN ja foi copiado para a area de transferencia (nao aparece aqui)."
    Write-Host "Agora va nas configuracoes do ambiente (Claude), no campo INSTAGRAM_TOKEN, apague o valor antigo e cole (Ctrl+V)."
    Write-Host "Confira tambem se INSTAGRAM_USER_ID e INSTAGRAM_LOGIN estao como acima. NAO cole o token em nenhum chat."
} catch {
    Write-Host ""
    Write-Host "NAO DEU CERTO:" -ForegroundColor Red
    Write-Host (MsgErro $_)
}
