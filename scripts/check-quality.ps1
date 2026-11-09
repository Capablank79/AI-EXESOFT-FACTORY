$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
. (Join-Path $PSScriptRoot 'resolve-python.ps1')
$python = $FactoryPython
& $python -m unittest discover -s quality -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'EXESOFT Quality Engine: pruebas fallidas' }
Write-Host '[OK] Pruebas del nucleo y Quality Engine completadas' -ForegroundColor Green
