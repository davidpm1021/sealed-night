function Initialize-XMageCheckout {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string]$Revision,
        [string]$Repository = 'https://github.com/GregorStocks/mage-bench.git'
    )

    $checkoutPath = [System.IO.Path]::GetFullPath($Directory)
    $parentPath = Split-Path $checkoutPath -Parent
    $backupPath = $null
    if (Test-Path -LiteralPath $checkoutPath) {
        if (-not (Test-Path -LiteralPath (Join-Path $checkoutPath '.git'))) {
            throw "Engine folder is not a Git checkout: $checkoutPath. Move it aside before retrying."
        }
        # A failed initial checkout leaves files but no index. Preserve those
        # files, then reuse the downloaded objects in a fresh checkout.
        if (-not (Test-Path -LiteralPath (Join-Path $checkoutPath '.git/index'))) {
            $backupPath = "$checkoutPath.incomplete-$([Guid]::NewGuid().ToString('N'))"
            if ((Split-Path $backupPath -Parent) -ne $parentPath) {
                throw 'The engine backup must stay in the same parent directory.'
            }
            Write-Host "Preserving incomplete engine checkout at $backupPath" -ForegroundColor Yellow
            Move-Item -LiteralPath $checkoutPath -Destination $backupPath
        }
    }

    if (-not (Test-Path -LiteralPath $checkoutPath)) {
        # Configure long paths before ANY files are checked out, and skip the
        # default branch: only the pinned revision is needed for this app.
        $cloneArgs = @('clone', '--no-checkout', '--config', 'core.longpaths=true')
        if ($backupPath) {
            $cloneArgs += @('--reference-if-able', $backupPath, '--dissociate')
        }
        & git @cloneArgs $Repository $checkoutPath
        if ($LASTEXITCODE -ne 0) { throw 'Could not clone mage-bench. Rerun setup to retry.' }
    }

    & git -C $checkoutPath config core.longpaths true
    if ($LASTEXITCODE -ne 0) { throw 'Could not enable long paths for the engine checkout.' }
    & git -C $checkoutPath fetch origin
    if ($LASTEXITCODE -ne 0) { throw 'Could not fetch mage-bench.' }
    & git -C $checkoutPath checkout --detach $Revision
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not check out the pinned engine revision. Existing files have been preserved.'
    }
}
