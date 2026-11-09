# PowerShell script for F2-004 PostgreSQL tests
# Execute as: .\scripts\run_tests_f2-004.ps1

param(
    [string]$EnvFile = "C:\AI-Factory\.env"
)

Write-Host "=== F2-004 PostgreSQL Crucual Tests ===" -ForegroundColor Cyan

# Set environment variables from .env
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$' -and !$($Match[1].Trim().StartsWith('#'))) {
            $key = $Match[1].Trim()
            $value = $Match[2].Trim()
            [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
    Write-Host "Variables de entorno cargadas desde .env" -ForegroundColor Green
} else {
    Write-Host "ERROR: Archivo .env no encontrado en $EnvFile" -ForegroundColor Red
    exit 1
}

# Run Python tests
. (Join-Path $PSScriptRoot 'resolve-python.ps1')
$pythonCmd = $FactoryPython
$env:POSTGRES_DB = 'exesoft_test_f7004'
$env:EXESOFT_TEST_POSTGRES_DB = 'exesoft_test_f7004'
$env:EXESOFT_BACKUP_TEST_ENABLED = '0'
$testScript = "C:\AI-Factory\scripts\run_f2004_tests.py"

Write-Host "Ejecutando pruebas F2-004..." -ForegroundColor Yellow

& $pythonCmd $testScript
$exitCode = $LASTEXITCODE

if ($exitCode -eq 0) {
    Write-Host "PRUEBAS F2-004: TODAS PASADAS" -ForegroundColor Green
} else {
    Write-Host "PRUEBAS F2-004: FALLIDAS ($exitCode)" -ForegroundColor Red
}

exit $exitCode
