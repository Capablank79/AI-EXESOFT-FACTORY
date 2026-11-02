$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (!(Test-Path $python)) { $python = 'python' }
& $python -m unittest discover -s quality -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'Pruebas fallidas: Runtime no aprobado' }
Write-Host '[OK] Core + Runtime validados'
