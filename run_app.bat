@echo off
chcp 65001 > nul
title QazaqProtocol AI — Студия автопротоколирования совещаний

echo ======================================================================
echo    QAZAQPROTOCOL AI — СТУДИЯ АВТОПРОТОКОЛИРОВАНИЯ СОВЕЩАНИЙ
echo    АО «Самрук-Казына Ондеу»
echo ======================================================================
echo.

REM Check if .env exists, if not copy from example
if not exist ".env" (
    echo [INFO] Создание файла конфигурации .env из .env.example...
    copy .env.example .env > nul
)

echo [INFO] Запуск автономного десктопного приложения...
py -3.11 app.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [WARN] Попытка запуска через системный python...
    python app.py
)

pause
