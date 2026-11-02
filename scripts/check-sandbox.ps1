$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$python = if (Test-Path '.\.venv\Scripts\python.exe') { '.\.venv\Scripts\python.exe' } else { 'python' }
& $python -m unittest discover -s quality -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'FAIL: pruebas de Exesoft' }
Write-Host '[OK] Exesoft Quality + Worktree + Fail-Closed v0.6' -ForegroundColor Green
