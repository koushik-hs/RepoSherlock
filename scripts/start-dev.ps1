#Requires -Version 5.1

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'backend\.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-Host 'RepoSherlock needs the backend virtual environment. Run the setup commands in README.md, then retry.' -ForegroundColor Red
    exit 1
}

& $python -u (Join-Path $PSScriptRoot 'dev_supervisor.py') $PID
exit $LASTEXITCODE
