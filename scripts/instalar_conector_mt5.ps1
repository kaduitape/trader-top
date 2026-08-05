$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv-mt5"
$pythonPath = Join-Path $venvPath "Scripts\python.exe"
$pythonwPath = Join-Path $venvPath "Scripts\pythonw.exe"
$taskName = "AI Trader PRO - Conector MT5"

Write-Host ""
Write-Host "AI Trader PRO - Instalacao do conector MetaTrader 5" -ForegroundColor Cyan
Write-Host "Projeto: $projectRoot"
Write-Host ""

if (-not (Test-Path (Join-Path $projectRoot ".env"))) {
    throw "Arquivo .env nao encontrado em $projectRoot. Configure o projeto antes de instalar."
}

if (-not (Test-Path $pythonPath)) {
    Write-Host "[1/4] Criando ambiente isolado do conector..."
    $pyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($null -ne $pyLauncher) {
        & $pyLauncher.Source -3 -m venv $venvPath
    }
    else {
        $pythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
        if ($null -eq $pythonCommand) {
            throw "Python 3.12 ou superior nao foi encontrado no Windows."
        }
        & $pythonCommand.Source -m venv $venvPath
    }
}
else {
    Write-Host "[1/4] Ambiente do conector ja existe." -ForegroundColor DarkGray
}

Write-Host "[2/4] Instalando/atualizando o conector oficial MT5..."
& $pythonPath -m pip install --disable-pip-version-check --upgrade pip
& $pythonPath -m pip install --disable-pip-version-check `
    "MetaTrader5>=5.0.45" `
    "pydantic>=2.9" `
    "pydantic-settings>=2.6" `
    "sqlalchemy>=2.0" `
    "pymysql>=1.1" `
    "cryptography>=43.0" `
    "numpy>=1.26" `
    "pandas>=2.2" `
    "httpx>=0.27"
& $pythonPath -m pip install --disable-pip-version-check --no-deps -e $projectRoot

Write-Host "[3/4] Registrando inicializacao automatica no Windows..."
$action = New-ScheduledTaskAction `
    -Execute $pythonwPath `
    -Argument "-m app.mt5.auto_sync" `
    -WorkingDirectory $projectRoot

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

# DOIS gatilhos, de proposito.
#
# O de logon sozinho tem um buraco conhecido: se o processo morrer no meio
# do dia (banco fora do ar, terminal fechado, pane de rede), nada o traz de
# volta ate o proximo login do Windows — e a unica saida visivel vira
# reinstalar o conector.
#
# O gatilho repetitivo fecha esse buraco. Combinado com -MultipleInstances
# IgnoreNew, ele e inofensivo quando o conector ja esta rodando (a nova
# instancia e simplesmente descartada) e e a rede de seguranca quando nao
# esta. Cinco minutos e o pior atraso possivel para voltar sozinho.
$gatilhoLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$gatilhoRede = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
$triggers = @($gatilhoLogon, $gatilhoRede)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $triggers `
    -Principal $principal `
    -Settings $settings `
    -Description "Sincronizacao e operacoes automaticas do AI Trader PRO. Reinicia sozinho a cada 5 min se cair." `
    -Force | Out-Null

Write-Host "[4/4] Iniciando o conector..."
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3

$task = Get-ScheduledTask -TaskName $taskName
Write-Host ""
Write-Host "Conector instalado com sucesso." -ForegroundColor Green
Write-Host "Estado da tarefa: $($task.State)"
Write-Host ""
Write-Host "Ele sobe sozinho a cada login E se conferido a cada 5 minutos:" -ForegroundColor Cyan
Write-Host "  se cair por qualquer motivo, volta em no maximo 5 min sem voce fazer nada."
Write-Host ""
Write-Host "Se cair, NAO reinstale. Rode:" -ForegroundColor Yellow
Write-Host "  scripts\reiniciar_conector_mt5.cmd"
Write-Host ""
Write-Host "No VPS, DESCONECTE a sessao (botao X do RDP) em vez de Fazer logoff:" -ForegroundColor Yellow
Write-Host "  logoff encerra a sessao e derruba o MetaTrader junto."
Write-Host ""
Write-Host "Volte ao painel e clique em Testar conexao."
