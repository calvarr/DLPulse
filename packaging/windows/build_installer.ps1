# Rulează după ``flet build windows`` din rădăcina repo-ului ``yt/``.
# Găsește ``runner\Release``, copiază într-un staging path fără spații, rulează NSIS.
$ErrorActionPreference = "Stop"
$Root = if ($args[0]) { $args[0] } else { Get-Location }
Push-Location $Root
try {
    $release = Get-ChildItem -Path "build" -Recurse -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq "Release" -and ($_.FullName -match "[\\/]runner[\\/]Release$") } |
        Select-Object -First 1
    if (-not $release) {
        Write-Host "Căutare Release sub build:"
        Get-ChildItem -Path "build" -Recurse -Directory -ErrorAction SilentlyContinue |
            Select-Object -First 50 -ExpandProperty FullName
        throw "Nu s-a găsit runner\Release."
    }

    $stage = "C:\dlpulse_release_stage"
    if (Test-Path $stage) {
        Remove-Item -Recurse -Force $stage
    }
    Copy-Item -Path $release.FullName -Destination $stage -Recurse

    $exeObj = Get-ChildItem -Path $stage -Filter "*.exe" -File | Select-Object -First 1
    if (-not $exeObj) {
        throw "Nu există .exe în Release."
    }
    $exeName = $exeObj.Name

    $candidates = @(
        "${env:ProgramFiles(x86)}\NSIS\makensis.exe",
        "$env:ProgramFiles\NSIS\makensis.exe",
        "C:\Program Files (x86)\NSIS\makensis.exe"
    )
    $makensis = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $makensis) {
        throw "makensis.exe nu a fost găsit. Instalează NSIS (ex. choco install nsis -y)."
    }

    $nsi = Join-Path $Root "packaging\windows\DLPulse.nsi"
    if (-not (Test-Path $nsi)) {
        throw "Lipsește $nsi"
    }

    $stageNsis = $stage -replace "\\", "/"
    & $makensis /DSOURCE_DIR="$stageNsis" /DEXE_NAME="$exeName" $nsi
    if ($LASTEXITCODE -ne 0) {
        throw "makensis a returnat $LASTEXITCODE"
    }

    $out = Join-Path $Root "build\DLPulse-Setup.exe"
    if (-not (Test-Path $out)) {
        throw "Lipsește output: $out"
    }
    Write-Host "Installer: $out"
}
finally {
    Pop-Location
}
