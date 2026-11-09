$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'resolve-python.ps1')
$python = $FactoryPython
& $python -m unittest discover -s quality -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'Fallo en pruebas: no ejecutar la integración real' }
Write-Host '[OK] Pruebas locales completas.'
Write-Host "Para probar la conexión real ejecutar: & '$python' .\scripts\verify-live.py"
