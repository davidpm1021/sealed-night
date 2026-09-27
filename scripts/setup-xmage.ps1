param(
    [switch]$InstallDependencies
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$mageBenchCommit = "78e18d68688ff6496f2506e4cae54629b5ba994f"
$vendor = Join-Path $root ".vendor"
$mageBench = Join-Path $vendor "mage-bench"

function Need-Command([string]$Name) {
    return -not (Get-Command $Name -ErrorAction SilentlyContinue)
}

if ($InstallDependencies) {
    if (Need-Command "java") {
        Write-Host "Installing Temurin JDK 21..." -ForegroundColor Yellow
        winget install --id EclipseAdoptium.Temurin.21.JDK -e --accept-source-agreements --accept-package-agreements
    }
    if (Need-Command "mvn") {
        Write-Host "Installing Maven..." -ForegroundColor Yellow
        winget install --id Apache.Maven -e --accept-source-agreements --accept-package-agreements
    }
}

if (Need-Command "git") {
    throw "Git is required. Install Git and rerun this script."
}
if (Need-Command "java") {
    throw "Java 21 is required. Rerun with: .\scripts\setup-xmage.ps1 -InstallDependencies"
}
if (Need-Command "mvn") {
    throw "Maven is required. Rerun with: .\scripts\setup-xmage.ps1 -InstallDependencies"
}

$javaVersion = (& java -version 2>&1 | Select-Object -First 1)
if ($javaVersion -notmatch 'version "(\d+)' -or [int]$Matches[1] -lt 21) {
    throw 'Java 21 or newer is required. Install it and open a new terminal before retrying.'
}
Write-Host "Java: $javaVersion"
Write-Host "Maven: $(& mvn -version | Select-Object -First 1)"

New-Item -ItemType Directory -Force -Path $vendor | Out-Null
if (-not (Test-Path $mageBench)) {
    git clone https://github.com/GregorStocks/mage-bench.git $mageBench
    if ($LASTEXITCODE -ne 0) { throw "Could not clone mage-bench." }
}

Push-Location $mageBench
try {
    git fetch origin
    if ($LASTEXITCODE -ne 0) { throw "Could not fetch mage-bench." }
    git checkout --detach $mageBenchCommit
    if ($LASTEXITCODE -ne 0) { throw "Could not check out the pinned engine revision." }

    Write-Host "Building the XMage server, observer, and headless bridge..." -ForegroundColor Cyan
    mvn -q -pl Mage.Server,Mage.Client.Observer,Mage.Client.Bridge -am -DskipTests install
    if ($LASTEXITCODE -ne 0) { throw "mage-bench/XMage build failed." }

    $actual = (git rev-parse HEAD).Trim()
    if ($actual -ne $mageBenchCommit) {
        throw "Unexpected mage-bench revision $actual"
    }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "XMage runtime installed and pinned to $mageBenchCommit" -ForegroundColor Green
Write-Host "The prerelease app can now launch the rules engine locally."
