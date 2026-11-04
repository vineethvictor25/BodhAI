param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port $Port --workers 1
