# Resolve and validate the repository's Python environment for PowerShell tasks.
$factoryRoot = Split-Path -Parent $PSScriptRoot
$FactoryPython = Join-Path $factoryRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $FactoryPython -PathType Leaf)) {
    throw "Required project interpreter is missing: $FactoryPython"
}

$pythonVersionText = & $FactoryPython --version 2>&1
if ($LASTEXITCODE -ne 0 -or $pythonVersionText -notmatch '^Python\s+(\d+)\.(\d+)') {
    throw 'Project Python interpreter failed its version check.'
}
$pythonVersion = [version]("$($Matches[1]).$($Matches[2])")
if ($pythonVersion -lt [version]'3.12') {
    throw "Python 3.12+ is required; found $pythonVersionText"
}
