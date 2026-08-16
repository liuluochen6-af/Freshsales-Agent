param(
    [string]$Destination = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Destination)) {
    $Destination = Join-Path $ProjectRoot ('releases\durian-agent-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.zip')
}
if (Test-Path -LiteralPath $Destination) {
    throw "Destination already exists; refusing to overwrite: $Destination"
}
$Parent = Split-Path -Parent $Destination
New-Item -ItemType Directory -Path $Parent -Force | Out-Null

$Tar = Get-Command tar.exe -ErrorAction SilentlyContinue
if (-not $Tar) {
    throw 'tar.exe was not found. Install the Windows archive component.'
}
$Items = @(
    'app.py', 'operations.py', 'inventory.py', 'production_server.py', 'maintenance.py',
    'knowledge_seed.py', 'dialogue_training_corpus.py', 'requirements.txt', 'README.md', 'README_DEPLOYMENT.md',
    'static', 'rpa', 'channels', 'scripts', 'tests', 'config/worker.example.json',
    'LICENSE', 'SECURITY.md', 'CONTRIBUTING.md', '.github'
)
Push-Location $ProjectRoot
try {
    & $Tar.Source -a -c -f $Destination '--exclude=__pycache__' '--exclude=*.pyc' '--exclude=rpa/authorization.json' @Items
    if ($LASTEXITCODE -ne 0) {
        throw "tar.exe failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
Write-Host "Release package created: $Destination" -ForegroundColor Green
Write-Host 'The package excludes the database, DeepSeek key, worker tokens, and customer files.' -ForegroundColor Yellow
$Digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
$ChecksumPath = "$Destination.sha256"
[System.IO.File]::WriteAllText($ChecksumPath, "$Digest  $(Split-Path -Leaf $Destination)`n", [System.Text.UTF8Encoding]::new($false))
Write-Host "SHA256 checksum created: $ChecksumPath" -ForegroundColor Green
