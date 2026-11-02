# EXESOFT: independent native Sandy v0.9996 verification; Windows PowerShell 5.1.
# Does not modify C:\AI-Factory. All writing confined to a fresh temporary folder.
$ErrorActionPreference = 'Stop'
$sandy = Join-Path $env:USERPROFILE 'Sandy-Evaluation\sandy.exe'
if (-not (Test-Path -LiteralPath $sandy)) { throw "No existe Sandy: $sandy" }
$base = Join-Path $env:TEMP ('exesoft-sandy-check-' + [guid]::NewGuid().ToString('N'))
$inside = Join-Path $base 'permitida'
$outside = Join-Path $base 'protegida'
New-Item -ItemType Directory -Path $inside, $outside -Force | Out-Null
$config = Join-Path $base 'sandy.toml'
$toml = "[sandbox]`ntoken = 'appcontainer'`nworkdir = '$inside'`n`n[allow.deep]`nall = ['$inside']`n`n[environment]`ninherit = false`n`n[limit]`ntimeout = 10`n"
[IO.File]::WriteAllText($config, $toml, (New-Object System.Text.UTF8Encoding($false)))
$checks = New-Object 'System.Collections.Generic.List[object]'
function Record-Test([string]$name, [bool]$ok, [string]$info) {
    $script:checks.Add([pscustomobject]@{Prueba=$name; Resultado=$(if($ok){'PASS'}else{'FAIL'}); Detalle=$info})
}
try {
    & $sandy --dry-run -c $config -x "$env:WINDIR\System32\cmd.exe" 1>$null 2>$null
    $dry = $LASTEXITCODE
    Record-Test 'TOML valido' ($dry -eq 0) "exit=$dry"
    if ($dry -ne 0) { throw 'TOML no valido; se suspenden las ejecuciones.' }
    & $sandy -c $config -x "$env:WINDIR\System32\cmd.exe" /c exit 0 2>$null
    $code = $LASTEXITCODE
    Record-Test 'AppContainer real' ($code -eq 0) "exit=$code"
    $allowedFile = Join-Path $inside 'allowed.txt'
    & $sandy -c $config -x "$env:WINDIR\System32\cmd.exe" /c "echo SI > `"$allowedFile`"" 2>$null
    $code = $LASTEXITCODE
    Record-Test 'Escritura autorizada' (($code -eq 0) -and (Test-Path -LiteralPath $allowedFile)) "exit=$code"
    $deniedFile = Join-Path $outside 'denied.txt'
    & $sandy -c $config -x "$env:WINDIR\System32\cmd.exe" /c "echo NO > `"$deniedFile`"" 2>$null
    $code = $LASTEXITCODE
    Record-Test 'Escritura denegada' (($code -ne 0) -and ($code -lt 125) -and -not (Test-Path -LiteralPath $deniedFile)) "exit=$code"
    # Real watchdog timeout via PowerShell sleep; exit code 130 reserved by Sandy for timeout.
    $timeoutConfig = Join-Path $base 'timeout.toml'
    [IO.File]::WriteAllText($timeoutConfig, ($toml -replace 'timeout = 10','timeout = 2'), (New-Object System.Text.UTF8Encoding($false)))
    & $sandy -c $timeoutConfig -x "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command 'Start-Sleep -Seconds 8' 2>$null
    $code = $LASTEXITCODE
    Record-Test 'Timeout real' ($code -eq 130) "exit=$code (130=timeout Sandy)"
} catch {
    Record-Test 'Excepcion controlada' $false $_.Exception.Message
} finally {
    $statusOutput = @(& $sandy --status 2>&1)
    $statusCode = $LASTEXITCODE
    Record-Test 'Sin instancias pendientes' (($statusCode -eq 0) -and (($statusOutput -join "`n") -match 'no active instances or recovery state')) "exit=$statusCode"
    $checks | Format-Table -AutoSize | Out-Host
    $failures = @($checks | Where-Object Resultado -eq 'FAIL').Count
    Write-Host "PASS=$(@($checks | Where-Object Resultado -eq 'PASS').Count) FAIL=$failures"
    Write-Host "Temporal: $base"
    if ($failures -gt 0) { exit 1 }
    exit 0
}
