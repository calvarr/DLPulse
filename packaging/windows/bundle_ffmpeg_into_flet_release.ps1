# After ``flet build windows``, copy ffmpeg (and ffprobe when available) into
# ``build\...\runner\Release\bin`` so portable zips and the NSIS staging tree include them.
#
# Usage: pwsh -File bundle_ffmpeg_into_flet_release.ps1 [REPO_ROOT]
# Optional env: PYTHON — interpreter with imageio-ffmpeg (default: python).
param(
    [Parameter(Position = 0)]
    [string]$RepoRoot = ""
)

$ErrorActionPreference = "Stop"
if (-not $RepoRoot) { $RepoRoot = (Get-Location).Path }
Push-Location $RepoRoot
try {
    $py = if ($env:PYTHON) { $env:PYTHON } else { "python" }

    $release = Get-ChildItem -Path "build" -Recurse -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq "Release" -and ($_.FullName -match "[\\/]runner[\\/]Release$") } |
        Select-Object -First 1

    if (-not $release) {
        Write-Warning "bundle_ffmpeg_into_flet_release: no runner\Release under build — skip."
        exit 0
    }

    $binDir = Join-Path $release.FullName "bin"
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null

    $ffmpeg = (& $py -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" 2>$null).Trim()
    if (-not $ffmpeg -or -not (Test-Path $ffmpeg)) {
        throw "imageio-ffmpeg did not provide an ffmpeg executable (PYTHON=$py)."
    }
    Copy-Item -Path $ffmpeg -Destination (Join-Path $binDir "ffmpeg.exe") -Force
    Write-Host "bundle_ffmpeg_into_flet_release: $(Join-Path $binDir 'ffmpeg.exe')"

    $ioDir = Split-Path -Parent $ffmpeg
    $ffprobeSrc = Join-Path $ioDir "ffprobe.exe"
    if (Test-Path $ffprobeSrc) {
        Copy-Item -Path $ffprobeSrc -Destination (Join-Path $binDir "ffprobe.exe") -Force
        Write-Host "bundle_ffmpeg_into_flet_release: $(Join-Path $binDir 'ffprobe.exe') (imageio folder)"
    }
    elseif (Get-Command ffprobe -ErrorAction SilentlyContinue) {
        $fp = (Get-Command ffprobe).Source
        Copy-Item -Path $fp -Destination (Join-Path $binDir "ffprobe.exe") -Force
        Write-Host "bundle_ffmpeg_into_flet_release: $(Join-Path $binDir 'ffprobe.exe') (PATH)"
    }
    else {
        Write-Warning "bundle_ffmpeg_into_flet_release: no ffprobe — some yt-dlp merges may still work."
    }
}
finally {
    Pop-Location
}
