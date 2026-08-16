param(
    [string]$HostAddress = '0.0.0.0',
    [int]$Port = 8015,
    [string]$BootstrapToken = $env:AGENT_BOOTSTRAP_TOKEN,
    [string]$AdminToken = $env:AGENT_ADMIN_TOKEN
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Runtime not installed. Run scripts\install_windows.ps1 first.'
}
if ([string]::IsNullOrWhiteSpace($BootstrapToken)) {
    $BootstrapToken = ([guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'))
}
if ([string]::IsNullOrWhiteSpace($AdminToken)) {
    $AdminToken = ([guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'))
}
$env:AGENT_HOST = $HostAddress
$env:AGENT_PORT = $Port.ToString()
$env:AGENT_BOOTSTRAP_TOKEN = $BootstrapToken
$env:AGENT_ADMIN_TOKEN = $AdminToken
Write-Host "Worker bootstrap token: $BootstrapToken" -ForegroundColor Yellow
Write-Host "Console admin token: $AdminToken" -ForegroundColor Yellow
Write-Host 'Expose this server only through a trusted LAN, VPN, or HTTPS reverse proxy.' -ForegroundColor Yellow
& $Python (Join-Path $ProjectRoot 'production_server.py')
