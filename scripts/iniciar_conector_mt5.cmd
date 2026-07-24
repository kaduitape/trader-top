@echo off
setlocal
title AI Trader PRO - Conector MT5
set "PROJECT_ROOT=%~dp0.."
set "CONNECTOR_PYTHON=%PROJECT_ROOT%\.venv-mt5\Scripts\python.exe"

if not exist "%CONNECTOR_PYTHON%" (
    echo O conector ainda nao foi instalado.
    echo Execute primeiro scripts\instalar_conector_mt5.ps1.
    pause
    exit /b 1
)

cd /d "%PROJECT_ROOT%"
"%CONNECTOR_PYTHON%" -m app.mt5.auto_sync
if errorlevel 1 pause
