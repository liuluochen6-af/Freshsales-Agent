param(
    [string]$Destination = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Runtime not installed.'
}
if ([string]::IsNullOrWhiteSpace($Destination)) {
    $Destination = Join-Path $ProjectRoot ('backups\durian-agent-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.db')
}
& $Python (Join-Path $ProjectRoot 'maintenance.py') backup --output $Destination
