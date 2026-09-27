$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        $pythonOnPath = $false
        if (Get-Command python -ErrorAction SilentlyContinue) {
            & python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
            $pythonOnPath = $LASTEXITCODE -eq 0
        }
        if ($pythonOnPath) {
            & python -m venv .venv
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3.12 -m venv .venv
        } else { throw 'Install Python 3.12 or newer from python.org (include the launcher), then rerun start.cmd.' }
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment. Install Python 3.12 or newer and retry.' }
    }
    & $python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'The project environment needs Python 3.12 or newer.' }
    $stamp = Join-Path $root '.venv\requirements.sha256'
    $digest = (& $python -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('requirements.txt').read_bytes()).hexdigest())").Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the dependency manifest.' }
    if (-not (Test-Path -LiteralPath $stamp) -or (Get-Content -LiteralPath $stamp -Raw).Trim() -ne $digest) {
        & $python -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your network and rerun start.cmd.' }
        Set-Content -LiteralPath $stamp -Value $digest
    }
    Write-Host 'Python environment ready.' -ForegroundColor Green
} finally { Pop-Location }
