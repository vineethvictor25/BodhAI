$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Error 'Run langgraph_impl\setup.ps1 first.'
    exit 1
}
Push-Location $PSScriptRoot
try {
    & $pythonPath -m langgraph_impl @args
    $runExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $runExitCode
