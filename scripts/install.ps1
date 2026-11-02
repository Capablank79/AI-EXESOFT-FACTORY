$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python 3.12 requerido.' }
python -c "import sys; assert sys.version_info >= (3,12), 'Python 3.12+ requerido'"
if (-not (Test-Path '.venv')) { python -m venv .venv }
& "$Root\.venv\Scripts\python.exe" -m unittest discover -s quality -p 'test_*.py' -v
Write-Host 'Nucleo instalado y pruebas ejecutadas. No se cambiaron credenciales ni FreeLLMAPI.'
