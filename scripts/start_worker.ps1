param(
    [switch]$Live,
    [switch]$Once,
    [string]$ServerUrl = '',
    [string]$WechatAccount = '',
    [string]$BootstrapToken = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Config = Join-Path $ProjectRoot 'config\worker.json'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Runtime not installed. Run scripts\install_windows.ps1 -WorkerOnly first.'
}
if (-not [string]::IsNullOrWhiteSpace($ServerUrl)) { $env:AGENT_SERVER_URL = $ServerUrl }
if (-not [string]::IsNullOrWhiteSpace($WechatAccount)) { $env:AGENT_WECHAT_ACCOUNT = $WechatAccount }
if (-not [string]::IsNullOrWhiteSpace($BootstrapToken)) { $env:AGENT_BOOTSTRAP_TOKEN = $BootstrapToken }
if (-not (Test-Path -LiteralPath $Config)) {
    Write-Host 'No worker configuration found. This computer will be identified and configured automatically.' -ForegroundColor Cyan
}

$Arguments = @('-m', 'rpa.worker_agent', '--config', $Config)
if ($Live) { $Arguments += '--live' }
if ($Once) { $Arguments += '--once' }
if (-not $Live) {
    Write-Host 'Configuration check mode: WeChat will not be read and no message will be sent.' -ForegroundColor Yellow
}
Push-Location $ProjectRoot
try {
    & $Python @Arguments
} finally {
    Pop-Location
}
