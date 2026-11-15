$ErrorActionPreference = 'Stop'
$environmentPath = Join-Path $PSScriptRoot '.venv'
$pythonPath = Join-Path $environmentPath 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    python -m venv $environmentPath
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
$requirementsPath = Join-Path $PSScriptRoot 'requirements-lock.txt'
if (-not (Test-Path -LiteralPath $requirementsPath)) {
    $requirementsPath = Join-Path $PSScriptRoot 'requirements.txt'
}
& $pythonPath -m pip install -r $requirementsPath --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonPath -m pip install --no-deps --editable $PSScriptRoot --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$localEnvPath = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path -LiteralPath $localEnvPath)) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.env.example') -Destination $localEnvPath
}
$encryptionConfig = Join-Path $PSScriptRoot 'encryption.json'
if (-not (Test-Path -LiteralPath $encryptionConfig)) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'encryption.example.json') -Destination $encryptionConfig
}
$modelPolicy = Join-Path $PSScriptRoot 'model_policy.json'
if (-not (Test-Path -LiteralPath $modelPolicy)) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'model_policy.example.json') -Destination $modelPolicy
}
$localDatabasePath = Join-Path $PSScriptRoot 'data\sales.db'
$rowPolicy = Join-Path $PSScriptRoot 'row_security.json'
if (-not (Test-Path -LiteralPath $rowPolicy)) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'row_security.example.json') -Destination $rowPolicy
}
if (-not (Test-Path -LiteralPath $localDatabasePath)) {
    & $pythonPath -m langgraph_impl --init-demo
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
exit 0
