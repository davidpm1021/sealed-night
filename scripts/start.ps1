param([switch]$Demo, [switch]$WithEngine, [switch]$NoBrowser,
      [ValidateRange(1,65535)][int]$Port = 8000,
      [string]$ListenAddress = '127.0.0.1')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
    & "$PSScriptRoot\setup.ps1"
    if ($WithEngine) { & "$PSScriptRoot\setup-xmage.ps1" }
    if ($Demo) { $env:CARD_PROVIDER = 'demo' }
    elseif (-not $env:CARD_PROVIDER) { $env:CARD_PROVIDER = 'mtgjson' }
    $url = "http://localhost:$Port"
    Write-Host "Prerelease Night: $url ($env:CARD_PROVIDER)" -ForegroundColor Green
    Write-Host 'Keep this window open. Stop with Ctrl+C. Event data is saved in data/events.'
    if ($ListenAddress -eq '0.0.0.0') { Write-Host 'LAN mode: friends use this PC hostname or LAN IP and the same port.' }
    if (-not $NoBrowser) { Start-Process $url }
    # One process owns the event store and engine sessions; reload/workers are unsafe here.
    & .\.venv\Scripts\python.exe -m uvicorn app.main:app --host $ListenAddress --port $Port
    if ($LASTEXITCODE -ne 0) { throw "Server exited with code $LASTEXITCODE. Check that port $Port is free." }
} finally { Pop-Location }
