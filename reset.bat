@echo off
echo ============================================================
echo  Telemetria Assetto Corsa - Iniciando dashboard
echo ============================================================
echo.
echo O Assetto Corsa publica a telemetria em memoria compartilhada,
echo entao nao existe porta UDP para liberar nem nada a configurar
echo no jogo. Basta abrir o AC e entrar na pista.
echo.
cd /d "%~dp0"
py main.pyw
