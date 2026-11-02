$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (!(Test-Path $python)) { $python = 'python' }
& $python -m unittest discover -s quality -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'Fallo en pruebas: no ejecutar la integración real' }
Write-Host '[OK] Pruebas locales completas.'
Write-Host 'Para probar la conexión real ejecutar: python .\scripts\verify-live.py'
