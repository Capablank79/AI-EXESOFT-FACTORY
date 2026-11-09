# PowerShell script for F3-004 Consistency Tests
# Execute as: .\scripts\run_tests_f3004.ps1

Write-Host "=== F3-004 Consistency Tests ===" -ForegroundColor Cyan

# Load .env
$envPath = "C:\AI-Factory\.env"
if (Test-Path $envPath) {
    Write-Host "Cargando variables de configuración..." -ForegroundColor Yellow
    Get-Content $envPath | ForEach-Object {
        $line = $_.Trim()
        if ($line -and !$line.StartsWith('#') -and $line.Contains('=')) {
            $parts = $line.Split('=', 2)
            if ($parts.Count -eq 2) {
                $key = $parts[0].Trim()
                $value = $parts[1].Trim()
                if ($key -and $value) {
                    [System.Environment]::SetEnvironmentVariable($key, $value, "Process") | Out-Null
                }
            }
        }
    }
    Write-Host "Variables configuradas" -ForegroundColor Green
}

# Ensure Python is available
. (Join-Path $PSScriptRoot 'resolve-python.ps1')
$pythonCmd = $FactoryPython
$env:POSTGRES_DB = 'exesoft_test_f7004'
$env:EXESOFT_TEST_POSTGRES_DB = 'exesoft_test_f7004'
$env:EXESOFT_BACKUP_TEST_ENABLED = '0'

Write-Host "`nEjecutando pruebas F3-004..." -ForegroundColor Yellow

$testFile = "C:\AI-Factory\tests\test_f3004_consistency.py"
$logFile = "$env:TEMP\f3004_test_output.log"

& $pythonCmd -m pytest $testFile -v --tb=short 2>&1 | Tee-Object -FilePath $logFile

$testExitCode = $LASTEXITCODE

# Summary
Write-Host "`n=== RESUMEN F3-004 ===" -ForegroundColor Cyan
if ($testExitCode -eq 0) {
    Write-Host "Pruebas: PASADAS" -ForegroundColor Green
} else {
    Write-Host "Pruebas: FALLIDAS" -ForegroundColor Red
    if (Test-Path $logFile) {
        Write-Host "`nÚltimos errores:" -ForegroundColor Yellow
        Get-Content $logFile -Tail 30
    }
}

if (Test-Path $logFile) {
    $passed = Select-String -Path $logFile -Pattern "passed" -AllMatches
    $failed = Select-String -Path $logFile -Pattern "failed" -AllMatches
    if ($passed -or $failed) {
        Write-Host "`nResultados: $($passed.Matches.Count) passed, $($failed.Matches.Count) failed" -ForegroundColor Cyan
    }
}

exit $testExitCode
