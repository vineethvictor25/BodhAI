$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'outputs\ui\processes.json'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output 'No managed UI session is recorded.'; exit 0 }
$record = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
if ($record.root -ne $PSScriptRoot) { throw 'The process record belongs to a different application directory.' }
foreach ($role in @('web','api')) {
    $processNumber = [int]$record.$role
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processNumber" -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    $expected = if ($role -eq 'api') { 'langgraph_impl.api:app' } else { Join-Path $PSScriptRoot 'web\server.mjs' }
    if (-not $process.CommandLine.Contains($expected)) { throw "Process $processNumber does not match this UI; it was not stopped." }
    Stop-Process -Id $processNumber -ErrorAction Stop
}
Remove-Item -LiteralPath $pidFile
Write-Output 'QueryDesk stopped.'
