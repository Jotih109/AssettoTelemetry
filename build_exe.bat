@echo off
title Build ApexView - Standalone Executable
echo ========================================================
echo   Compilando ApexView — Assetto Corsa Telemetry Pro
echo ========================================================
echo.

echo 1/2. Garantindo dependencias do PyInstaller...
pip install pyinstaller pyqt5 pyqtgraph

echo.
echo 2/2. Gerando pasta portatil do executavel ApexView...
pyinstaller --noconsole --onedir --name "ApexView" --clean main.pyw

echo.
echo ========================================================
echo   Build concluido com sucesso!
echo   O executavel encontra-se em: dist\ApexView\ApexView.exe
echo ========================================================
echo.
pause
