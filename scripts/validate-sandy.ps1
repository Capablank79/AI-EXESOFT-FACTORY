# EXESOFT AI FACTORY - Sandy CLI validation (Windows PowerShell 5.1)
# Does not alter AI-Factory source files. Uses a dedicated temporary directory.
$ErrorActionPreference = 'Stop'
$whoami = Join-Path $env:SystemRoot 'System32\whoami.exe'
$expectedUser = Split-Path -Leaf $env:USERPROFILE
if (-not (Test-Path -LiteralPath $whoami)) { throw 'No se pudo verificar la identidad anfitriona.' }
$actualUser = (& $whoami).Trim().Split('\')[-1]
if ($actualUser -ine $expectedUser) { throw "Contexto no anfitrión: token=$actualUser perfil=$expectedUser. Ejecuta desde PowerShell normal de JLLV." }
$sandy = if ($env:SANDY_EXE) { $env:SANDY_EXE } else { Join-Path $env:USERPROFILE 'Sandy-Evaluation\sandy.exe' }
if (-not (Test-Path -LiteralPath $sandy)) { throw "No existe Sandy CLI: $sandy" }
$statusText = & $sandy --status --json
if ($LASTEXITCODE -ne 0) { throw 'Sandy --status falló; se abortan las pruebas sin modificar ACL.' }
$status = ($statusText -join "`n") | ConvertFrom-Json
$summary = $status.summary
$dirty = @('stale_instances','incomplete_instances','stale_retry_containers','incomplete_retry_containers','orphaned_tasks') |
    Where-Object { [int]$summary.$_ -gt 0 }
if ($dirty.Count) { throw "Sandy no está limpio ($($dirty -join ', ')); se abortan las pruebas sin ejecutar --cleanup." }
Write-Warning 'Riesgo: estas pruebas crean AppContainers y permisos ACL temporales en una carpeta TEMP propia y en el runtime Python. Una interrupción puede dejar recuperación pendiente; el adaptador solo usa sandy --cleanup si el estado demuestra que todos los recursos stale pertenecen a esa carpeta.'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = if (Test-Path (Join-Path $root '.venv\Scripts\python.exe')) { Join-Path $root '.venv\Scripts\python.exe' } else { 'python' }
& $python -B -m quality.sandy_validation
$validationExit = $LASTEXITCODE
if ($validationExit -ne 0) { throw "Sandy validation failed ($validationExit). Review the validation output and Sandy status." }
exit 0

<# Retired inline validator; replaced by quality.sandy_validation. Kept inert during migration.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir
$python = if (Test-Path (Join-Path $root '.venv\Scripts\python.exe')) { Join-Path $root '.venv\Scripts\python.exe' } else { 'python' }
$work = Join-Path ([IO.Path]::GetTempPath()) ('exesoft-sandy-validation-' + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $work -Force)
$testFile = Join-Path $work 'validate.py'
$stdoutFile = Join-Path $work 'stdout.json'
$stderrFile = Join-Path $work 'stderr.log'
Write-Host '=== EXESOFT AI FACTORY - Sandy Validation ===' -ForegroundColor Cyan
Write-Host "Root: $root"
Write-Host "Evidence: $work"

$validationScript = @'
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
expected = ['Python Execution', 'Allowed Write', 'Write Denial', 'Network Block', 'Timeout', 'SHA-256', 'Cleanup']
results = {'tests': [], 'passed': 0, 'failed': 0}

def record(name, ok, detail=''):
    results['tests'].append({'name': name, 'status': 'PASS' if ok else 'FAIL', 'detail': str(detail)[:500]})
    results['passed' if ok else 'failed'] += 1

def execute(adapter, workspace, command, timeout=15):
    return adapter.execute(str(workspace), command, timeout=timeout)

def code(result):
    return getattr(result, 'returncode', None)

def detail(result):
    return 'exit=%r; error=%r; stdout=%r; stderr=%r' % (
        code(result), getattr(result, 'error', None),
        str(getattr(result, 'stdout', ''))[:120], str(getattr(result, 'stderr', ''))[:120])

base = pathlib.Path(tempfile.mkdtemp(prefix='exesoft-sandy-tests-'))
workspace = base / 'allowed'
protected = base / 'protected'
workspace.mkdir()
protected.mkdir()
try:
    from engine.sandy_adapter import SandySandboxAdapter
    adapter = SandySandboxAdapter()
    python_exe = sys.executable
    # Test 1: actual Python code in sandbox. Do not interpret launch failure as success.
    marker = workspace / 'python_ok.txt'
    program = workspace / 'python_test.py'
    program.write_text("from pathlib import Path\nPath('python_ok.txt').write_text('python-isolated')\nprint('SANDY_PYTHON_OK')\n", encoding='utf-8')
    try:
        r = execute(adapter, workspace, 'python python_test.py', timeout=20)
        ok = code(r) == 0 and marker.is_file() and marker.read_text(encoding='utf-8') == 'python-isolated' and 'SANDY_PYTHON_OK' in str(getattr(r, 'stdout', ''))
        record('Python Execution', ok, detail(r))
    except Exception as e:
        record('Python Execution', False, repr(e))

    # Test 2: write only in explicitly allowed workspace.
    allowed = workspace / 'write_ok.txt'
    try:
        r = execute(adapter, workspace, 'cmd /c "echo allowed>write_ok.txt"', timeout=10)
        ok = code(r) == 0 and allowed.is_file()
        record('Allowed Write', ok, detail(r))
    except Exception as e:
        record('Allowed Write', False, repr(e))

    # Test 3: writing to a sibling directory must fail, not merely be absent due to a launch error.
    denied = protected / 'blocked.txt'
    try:
        r = execute(adapter, workspace, 'cmd /c "echo forbidden>\"%s\""' % denied, timeout=10)
        # Require evidence that the sandbox actually ran and denied access; a random failure is not proof.
        stderr = str(getattr(r, 'stderr', ''))
        stdout = str(getattr(r, 'stdout', ''))
        msg = (stdout + '\n' + stderr).lower()
        denied_message = ('acceso denegado' in msg or 'access is denied' in msg or 'permission denied' in msg)
        record('Write Denial', not denied.exists() and code(r) not in (None, 0) and denied_message, detail(r))
    except Exception as e:
        record('Write Denial', False, repr(e))

    # Test 4: active outbound TCP attempt. Fail closed if the program cannot run.
    network_script = workspace / 'network_test.py'
    network_script.write_text("import socket,sys\ns=socket.socket();s.settimeout(3)\ntry:\n s.connect(('1.1.1.1',443));print('NETWORK_CONNECTED');sys.exit(12)\nexcept (PermissionError,OSError) as e:\n print('NETWORK_DENIED',type(e).__name__,getattr(e,'winerror',None));sys.exit(13)\nfinally:\n s.close()\n", encoding='utf-8')
    try:
        r = execute(adapter, workspace, 'python network_test.py', timeout=12)
        out = str(getattr(r, 'stdout', ''))
        # A generic connection failure (including network outage) is inconclusive, not proof of sandbox policy.
        record('Network Block', code(r) == 13 and 'NETWORK_DENIED PermissionError' in out, detail(r))
    except Exception as e:
        record('Network Block', False, repr(e))

    # Test 5: real timeout, not Windows 'timeout' command which may exit due to disabled stdin.
    sleep_script = workspace / 'sleep_test.py'
    sleep_script.write_text('import time\ntime.sleep(12)\nprint("UNEXPECTED_COMPLETION")\n', encoding='utf-8')
    try:
        start = time.monotonic()
        r = execute(adapter, workspace, 'python sleep_test.py', timeout=2)
        elapsed = time.monotonic() - start
        err = str(getattr(r, 'error', '')).lower()
        text = (str(getattr(r, 'stdout', '')) + str(getattr(r, 'stderr', ''))).lower()
        record('Timeout', ('timeout' in err or 'timed out' in err or 'timeout' in text) and elapsed < 10, detail(r) + '; elapsed=%.2f' % elapsed)
    except Exception as e:
        record('Timeout', False, repr(e))

    # Test 6: the adapter must supply a SHA-256 digest for an actually executed command.
    try:
        r = execute(adapter, workspace, 'cmd /c echo SANDY_HASH_TEST', timeout=10)
        value = getattr(r, 'sha256', None)
        ok = code(r) == 0 and isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdefABCDEF' for c in value)
        record('SHA-256', ok, detail(r))
    except Exception as e:
        record('SHA-256', False, repr(e))
finally:
    try:
        shutil.rmtree(base)
        # Use the installed Sandy executable; status must show no residual instances.
        sandy = pathlib.Path.home() / 'Sandy-Evaluation' / 'sandy.exe'
        if not sandy.exists():
            record('Cleanup', False, 'sandy.exe not found at established local path')
        else:
            cp = subprocess.run([str(sandy), '--status'], capture_output=True, text=True, timeout=10)
            good = cp.returncode == 0 and 'no active instances or recovery state' in (cp.stdout + cp.stderr).lower() and not base.exists()
            record('Cleanup', good, (cp.stdout + cp.stderr)[:500])
    except Exception as e:
        record('Cleanup', False, repr(e))

# Seven names in order are required; no exceptions accepted as passes.
if [t['name'] for t in results['tests']] != expected:
    raise RuntimeError('Missing or reordered validation tests: %r' % results['tests'])
print(json.dumps(results, ensure_ascii=True))
sys.exit(0 if results['passed'] == len(expected) and results['failed'] == 0 else 1)
'@

try {
    [IO.File]::WriteAllText($testFile, $validationScript, (New-Object System.Text.UTF8Encoding($false)))
    # Resolve Python command and invoke with separate stdout/stderr files; works in Windows PowerShell 5.1.
    $pythonCommand = Get-Command $python -ErrorAction Stop
    $pythonPath = $pythonCommand.Source
    & $pythonPath $testFile $root 1> $stdoutFile 2> $stderrFile
    $exitCode = $LASTEXITCODE
    $raw = if (Test-Path $stdoutFile) { [IO.File]::ReadAllText($stdoutFile).Trim() } else { '' }
    $errors = if (Test-Path $stderrFile) { [IO.File]::ReadAllText($stderrFile).Trim() } else { '' }
    if ([string]::IsNullOrWhiteSpace($raw)) { throw "Python returned no JSON (exit=$exitCode). $errors" }
    try { $results = ConvertFrom-Json -InputObject $raw -ErrorAction Stop } catch { throw "Invalid JSON from Python (exit=$exitCode): $raw. $errors" }
    if ($null -eq $results -or $null -eq $results.tests -or $null -eq $results.passed -or $null -eq $results.failed) {
        throw "JSON missing mandatory fields. $raw"
    }
    $required = @('Python Execution','Allowed Write','Write Denial','Network Block','Timeout','SHA-256','Cleanup')
    $tests = @($results.tests)
    if ($tests.Count -ne $required.Count) { throw "Expected 7 tests but received $($tests.Count)." }
    $passed = 0; $failed = 0
    for ($i = 0; $i -lt $required.Count; $i++) {
        $test = $tests[$i]
        if ($test.name -cne $required[$i]) { throw "Missing or wrong test #${i}: $($test.name)" }
        if ($test.status -ceq 'PASS') { $passed++ }
        elseif ($test.status -ceq 'FAIL') { $failed++ }
        else { throw "Invalid status for $($test.name)" }
    }
    if ($results.passed -isnot [int] -or $results.failed -isnot [int] -or $results.passed -ne $passed -or $results.failed -ne $failed) { throw 'Inconsistent counters.' }
    Write-Host "`n=== VALIDATION RESULTS ===" -ForegroundColor Cyan
    foreach ($test in $tests) {
        $color = if ($test.status -eq 'PASS') { 'Green' } else { 'Red' }
        Write-Host "  $($test.name): $($test.status) - $($test.detail)" -ForegroundColor $color
    }
    Write-Host "`nPassed: $passed | Failed: $failed" -ForegroundColor Cyan
    if ($null -eq $exitCode -or $exitCode -ne 0 -or $failed -ne 0) { exit 1 }
    exit 0
} catch {
    Write-Host "VALIDATION FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Diagnostic files: $work" -ForegroundColor Yellow
    exit 1
}
#>
