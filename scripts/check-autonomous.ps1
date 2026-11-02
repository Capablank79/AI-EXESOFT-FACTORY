$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$python = if (Test-Path '.\.venv\Scripts\python.exe') {'.\.venv\Scripts\python.exe'} else {'python'}
& $python -m unittest discover -s quality -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) {throw 'FAIL: pruebas del nucleo'}
Write-Host '[OK] Exesoft Core + Autonomous Engine v0.5' -ForegroundColor Green
