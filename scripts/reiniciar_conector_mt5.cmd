@echo off
setlocal
title AI Trader PRO - Reiniciar conector MT5

rem Reinicia o conector sem reinstalar nada.
rem
rem Existe porque "reinstalar" virou o remedio para qualquer queda, e
rem reinstalar refaz o ambiente Python inteiro para resolver algo que um
rem restart resolveria em dois segundos. Reinstalacao so e necessaria quando
rem o ambiente muda: dependencia nova, caminho do projeto diferente.

set "TASK=AI Trader PRO - Conector MT5"

schtasks /query /tn "%TASK%" >nul 2>&1
if errorlevel 1 (
    echo.
    echo A tarefa agendada nao existe neste Windows.
    echo Ai sim e caso de instalar: scripts\instalar_conector_mt5.ps1
    echo.
    pause
    exit /b 1
)

echo Parando o conector...
schtasks /end /tn "%TASK%" >nul 2>&1

rem O Windows leva um instante para liberar o processo; subir em cima da
rem parada faz a tarefa nascer e morrer junto.
timeout /t 3 /nobreak >nul

echo Iniciando o conector...
schtasks /run /tn "%TASK%"
if errorlevel 1 (
    echo.
    echo Falha ao iniciar. Abra o Agendador de Tarefas e veja o historico
    echo da tarefa "%TASK%".
    pause
    exit /b 1
)

echo.
echo Conector reiniciado. Volte ao painel: o status sai de OFFLINE em ate
echo 90 segundos. Se voltar a cair, o log fica em logs\ na pasta do projeto.
echo.
pause
