$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
. (Join-Path $PSScriptRoot 'resolve-python.ps1')
$python = $FactoryPython
& $python -m unittest discover -s quality -p 'test_*.py' -v
Write-Host 'Nucleo instalado y pruebas ejecutadas. No se cambiaron credenciales ni FreeLLMAPI.'
