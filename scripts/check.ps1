$Root=Split-Path -Parent $PSScriptRoot
Set-Location $Root
& "$Root\.venv\Scripts\python.exe" -m unittest discover -s quality -p 'test_*.py' -v
if (Test-NetConnection 127.0.0.1 -Port 3001 -InformationLevel Quiet -WarningAction SilentlyContinue) {
 Write-Host '[OK] FreeLLMAPI escucha en puerto 3001 (no verifica autenticacion)'
} else { Write-Host '[AVISO] FreeLLMAPI no escucha en puerto 3001' }
