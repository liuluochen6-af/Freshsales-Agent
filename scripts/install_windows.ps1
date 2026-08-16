param(
    [switch]$WorkerOnly
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
    throw 'Python was not found. Install Python 3.11 or 3.12 and add it to PATH.'
}

$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $Python.Source -m venv (Join-Path $ProjectRoot '.venv')
}
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')

if ($WorkerOnly) {
    Write-Host 'The worker will create its device identity and configuration automatically on first start.' -ForegroundColor Cyan
}

Write-Host 'Installation completed. WeChat automation has not been started.' -ForegroundColor Green
