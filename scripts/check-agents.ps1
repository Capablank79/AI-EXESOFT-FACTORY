$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'resolve-python.ps1')
$python = $FactoryPython
& $python -m unittest discover -s quality -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'Pruebas fallidas: Agent Runtime no aprobado' }
Write-Host '[OK] Core + Orchestrator + Agent Runtime tests'
