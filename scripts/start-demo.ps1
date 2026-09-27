param([switch]$NoBrowser, [int]$Port = 8000, [string]$ListenAddress = '127.0.0.1')
& "$PSScriptRoot\start.ps1" -Demo -NoBrowser:$NoBrowser -Port $Port -ListenAddress $ListenAddress
