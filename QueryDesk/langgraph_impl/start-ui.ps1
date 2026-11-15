param([switch]$OpenBrowser)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$nodeServer = Join-Path $PSScriptRoot 'web\server.mjs'
$indexPath = Join-Path $PSScriptRoot 'web\dist\index.html'
if (-not (Test-Path -LiteralPath $pythonPath) -or -not (Test-Path -LiteralPath $indexPath)) {
    throw 'The UI is not set up yet. Run .\langgraph_impl\setup-ui.ps1 first.'
}
& $pythonPath -c 'import fastapi, uvicorn'
if ($LASTEXITCODE -ne 0) { throw 'Backend dependencies are missing. Run setup-ui.ps1 first.' }
$logDirectory = Join-Path $PSScriptRoot 'outputs\ui'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$pidFile = Join-Path $logDirectory 'processes.json'
if (Test-Path -LiteralPath $pidFile) { throw 'A UI session is already recorded. Run stop-ui.ps1 before starting another.' }
foreach ($port in @(8000,3000)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Stop the existing service before starting the UI."
    }
}
$apiProcess = $null
$webProcess = $null
try {
    $apiProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m','uvicorn','langgraph_impl.api:app','--host','127.0.0.1','--port','8000') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'api.log') -RedirectStandardError (Join-Path $logDirectory 'api-error.log')
    $nodePath = (Get-Command node -ErrorAction Stop).Source
    $webProcess = Start-Process -FilePath $nodePath -ArgumentList @(('"' + $nodeServer + '"')) -WorkingDirectory (Join-Path $PSScriptRoot 'web') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'web.log') -RedirectStandardError (Join-Path $logDirectory 'web-error.log')
    @{ root=$PSScriptRoot; api=$apiProcess.Id; web=$webProcess.Id } | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8
    $ready=$false
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        if ($apiProcess.HasExited -or $webProcess.HasExited) { throw 'A service stopped during startup. Check outputs/ui logs.' }
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:3000/api/health' -TimeoutSec 2
            if ($health.service -eq 'querydesk') { $ready=$true; break }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw 'The UI did not become ready. Check outputs/ui logs.' }
    Write-Output 'QueryDesk is ready: http://127.0.0.1:3000'
    Write-Output 'Stop it with .\langgraph_impl\stop-ui.ps1'
    if ($OpenBrowser) { Start-Process 'http://127.0.0.1:3000' }
} catch {
    if ($webProcess -and -not $webProcess.HasExited) { Stop-Process -Id $webProcess.Id -ErrorAction SilentlyContinue }
    if ($apiProcess -and -not $apiProcess.HasExited) { Stop-Process -Id $apiProcess.Id -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile }
    throw
}
