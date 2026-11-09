# PowerShell script for F2-004 PostgreSQL tests
# Execute as: .\scripts\run_tests_f2004.ps1
#
# NOTA: Asegúrate de que las variables de entorno PostgreSQL estén configuradas:
#   $env:POSTGRES_APP_PASSWORD = "amor2032"
#   (o ejecuta desde el directorio con .env accesible)

Write-Host "=== F2-004 PostgreSQL Tests ===" -ForegroundColor Cyan

# Set env vars from .env if available
$envPath = "C:\AI-Factory\.env"
if (Test-Path $envPath) {
    Write-Host "Cargando variables desde .env..." -ForegroundColor Yellow
    Get-Content $envPath | ForEach-Object {
        $line = $_.Trim()
        if ($line -and !$line.StartsWith('#') -and $line.Contains('=')) {
            $parts = $line.Split('=', 2)
            if ($parts.Count -eq 2) {
                $key = $parts[0].Trim()
                $value = $parts[1].Trim()
                if ($key -and $value) {
                    # Skip password variables for security
                    if ($key -notlike '*PASSWORD*' -and $key -notlike '*API_KEY*') {
                        [System.Environment]::SetEnvironmentVariable($key, $value, "Process") | Out-Null
                    }
                }
            }
        }
    }
    Write-Host "Variables cargadas (sin credenciales)" -ForegroundColor Green
}

# Ensure Python is available
. (Join-Path $PSScriptRoot 'resolve-python.ps1')
$pythonCmd = $FactoryPython
$env:POSTGRES_DB = 'exesoft_test_f7004'
$env:EXESOFT_TEST_POSTGRES_DB = 'exesoft_test_f7004'
$env:EXESOFT_BACKUP_TEST_ENABLED = '0'

# Run existing tests first
Write-Host "`n1. Ejecutando pruebas base..." -ForegroundColor Yellow
$existingTests = "C:\AI-Factory\scripts\run_f2004_tests.py"
& $pythonCmd $existingTests
$baseExitCode = $LASTEXITCODE

# Run pending tests
Write-Host "`n2. Ejecutando pruebas pendientes..." -ForegroundColor Yellow
$pendingTests = "C:\AI-Factory\scripts\test_f2004_pending.py"
& $pythonCmd $pendingTests
$pendingExitCode = $LASTEXITCODE

# Summary
Write-Host "`n=== RESUMEN ===" -ForegroundColor Cyan
if ($baseExitCode -eq 0) {
    Write-Host "Pruebas base: PASADAS" -ForegroundColor Green
} else {
    Write-Host "Pruebas base: FALLIDAS" -ForegroundColor Red
}

if ($pendingExitCode -eq 0) {
    Write-Host "Pruebas pendientes: PASADAS" -ForegroundColor Green
} else {
    Write-Host "Pruebas pendientes: FALLIDAS" -ForegroundColor Red
}

$combinedExitCode = if ($baseExitCode -ne 0 -or $pendingExitCode -ne 0) { 1 } else { 0 }
exit $combinedExitCode
