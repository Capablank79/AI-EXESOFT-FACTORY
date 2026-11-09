$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'resolve-python.ps1')
$python = $FactoryPython
& $python -m unittest discover -s quality -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) {throw 'FAIL: pruebas del nucleo'}
Write-Host '[OK] Exesoft Core + Autonomous Engine v0.5' -ForegroundColor Green
