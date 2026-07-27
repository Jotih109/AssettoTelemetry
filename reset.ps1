Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Telemetria Assetto Corsa - Iniciando dashboard" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  O Assetto Corsa publica a telemetria em memoria compartilhada:" -ForegroundColor Gray
Write-Host "  nao ha porta UDP para liberar nem nada a configurar no jogo." -ForegroundColor Gray
Write-Host "  Basta abrir o AC e entrar na pista." -ForegroundColor Gray
Write-Host ""

# Avisa se o Assetto Corsa ainda nao esta rodando (nao e obrigatorio:
# o dashboard fica aguardando e conecta sozinho quando o jogo abrir)
$ac = Get-Process -Name "acs", "AssettoCorsa" -ErrorAction SilentlyContinue
if ($ac) {
    Write-Host "  Assetto Corsa detectado (PID $($ac[0].Id))." -ForegroundColor Green
} else {
    Write-Host "  Assetto Corsa nao esta aberto - o dashboard vai aguardar." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  Iniciando main.pyw..." -ForegroundColor Cyan
py .\main.pyw
