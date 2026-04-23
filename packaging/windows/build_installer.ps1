# Run after ``flet build windows`` from the repo root ``yt/``.
# Find ``runner\Release``, copy to a staging path without spaces, run NSIS.
$ErrorActionPreference = "Stop"
$Root = if ($args[0]) { $args[0] } else { Get-Location }
Push-Location $Root
try {
    $release = Get-ChildItem -Path "build" -Recurse -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq "Release" -and ($_.FullName -match "[\\/]runner[\\/]Release$") } |
        Select-Object -First 1
    if (-not $release) {
        Write-Host "Searching for Release under build:"
        Get-ChildItem -Path "build" -Recurse -Directory -ErrorAction SilentlyContinue |
            Select-Object -First 50 -ExpandProperty FullName
        throw "runner\Release not found."
    }

    $stage = "C:\dlpulse_release_stage"
    if (Test-Path $stage) {
        Remove-Item -Recurse -Force $stage
    }
    Copy-Item -Path $release.FullName -Destination $stage -Recurse

    $exeObj = Get-ChildItem -Path $stage -Filter "*.exe" -File | Select-Object -First 1
    if (-not $exeObj) {
        throw "No .exe in Release."
    }
    $exeName = $exeObj.Name

    $candidates = @(
        "${env:ProgramFiles(x86)}\NSIS\makensis.exe",
        "$env:ProgramFiles\NSIS\makensis.exe",
        "C:\Program Files (x86)\NSIS\makensis.exe"
    )
    $makensis = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $makensis) {
        throw "makensis.exe not found. Install NSIS (e.g. choco install nsis -y)."
    }

    $nsi = Join-Path $Root "packaging\windows\DLPulse.nsi"
    if (-not (Test-Path $nsi)) {
        throw "Missing $nsi"
    }

    $stageNsis = $stage -replace "\\", "/"
    & $makensis /DSOURCE_DIR="$stageNsis" /DEXE_NAME="$exeName" $nsi
    if ($LASTEXITCODE -ne 0) {
        throw "makensis exited with $LASTEXITCODE"
    }

    $out = Join-Path $Root "build\DLPulse-Setup.exe"
    if (-not (Test-Path $out)) {
        throw "Missing output: $out"
    }
    Write-Host "Installer: $out"
}
finally {
    Pop-Location
}
