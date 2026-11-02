$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
& $python -m unittest discover -s quality -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'EXESOFT Quality Engine: pruebas fallidas' }
Write-Host '[OK] Pruebas del nucleo y Quality Engine completadas' -ForegroundColor Green
