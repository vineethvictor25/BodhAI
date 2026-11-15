$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'setup.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Push-Location (Join-Path $PSScriptRoot 'web')
try {
    if (Test-Path -LiteralPath 'package-lock.json') { npm ci --no-fund --no-audit }
    else { npm install --no-fund --no-audit }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    npm run build
    $setupExitCode = $LASTEXITCODE
} finally { Pop-Location }
exit $setupExitCode
