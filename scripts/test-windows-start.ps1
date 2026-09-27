$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$env:DATA_DIR = Join-Path $root 'data\startup-smoke'
$launcher = Join-Path $root 'start.cmd'
$log = Join-Path $root 'startup-smoke.log'
# Exercise the same entry point users run, including automatic setup.
$process = Start-Process -FilePath 'cmd.exe' -ArgumentList "/c `"`"$launcher`" -Demo -NoBrowser -Port 8766`"" -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput $log -RedirectStandardError "$log.err"
try {
    $ready = $false
    for ($attempt=0; $attempt -lt 180; $attempt++) {
        if ($process.HasExited) { throw "Launcher exited early. $(Get-Content $log -Raw) $(Get-Content "$log.err" -Raw)" }
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:8766/api/health' -TimeoutSec 2
            if ($health.ok -and $health.provider -eq 'demo') { $ready=$true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw 'The one-command launcher never became healthy.' }
    & "$PSScriptRoot\setup.ps1"
    Write-Host 'Windows setup, startup and repeat setup passed.'
} finally {
    if (-not $process.HasExited) { & taskkill /PID $process.Id /T /F | Out-Null }
}
