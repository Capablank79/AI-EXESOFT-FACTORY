$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'resolve-python.ps1')
$python = $FactoryPython
& $python -m unittest discover -s quality -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'FAIL: pruebas de Exesoft' }
Write-Host '[OK] Exesoft Quality + Worktree + Fail-Closed v0.6' -ForegroundColor Green
