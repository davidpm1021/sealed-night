$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Virtual environment missing. Running setup first..." -ForegroundColor Yellow
    & "$PSScriptRoot\setup.ps1"
}

if (-not $env:CARD_PROVIDER) { $env:CARD_PROVIDER = "mtgjson" }
Write-Host "Prerelease Night starting at http://localhost:8000" -ForegroundColor Green
Write-Host "Card provider: $env:CARD_PROVIDER"
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
