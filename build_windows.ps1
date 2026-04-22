# =============================================================================
# build_windows.ps1 — Build Flet desktop pentru Windows (PowerShell nativ).
# Din rădăcina repo-ului (yt/): .\build_windows.ps1 [extra flet args]
#
# Verifică Python 3.11+, .venv, importuri; pip install doar dacă e nevoie.
# =============================================================================

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$Root = $PSScriptRoot
Set-Location $Root

function Info { param([string]$msg) Write-Host "[build_windows] $msg" -ForegroundColor Green }
function Warn { param([string]$msg) Write-Host "[warn]  $msg" -ForegroundColor Yellow }
function Err  { param([string]$msg) Write-Host "[error] $msg" -ForegroundColor Red }

function Get-ReqDigest {
    $sb = [System.Text.StringBuilder]::new()
    foreach ($rel in @("flet_app\requirements.txt", "desktop_tui\requirements.txt", "requirements.txt")) {
        $p = Join-Path $Root $rel
        if (Test-Path $p) {
            [void]$sb.Append($rel)
            [void]$sb.Append((Get-FileHash -Algorithm SHA256 -Path $p).Hash)
        }
    }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($sb.ToString())
    $sha = [System.Security.Cryptography.SHA256]::Create()
    -join ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") })
}

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $py) { Err "Nu găsesc python sau py pe PATH (instalează Python 3.11+ de la python.org)."; exit 1 }

$verOk = & $py -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
if ($LASTEXITCODE -ne 0) { Err "Necesită Python 3.11 sau mai nou. Găsit: $(& $py -c 'import sys; print(sys.version)' 2>$null)"; exit 1 }
Info "Python: $py"

$Venv = "$Root\.venv"
$VenvPy = "$Venv\Scripts\python.exe"
$VenvPip = "$Venv\Scripts\pip.exe"
$FletExe = "$Venv\Scripts\flet.exe"
$Stamp = "$Venv\.yt_build_deps_stamp"

if (-not (Test-Path $VenvPy)) {
    Info "Creez .venv …"
    & $py -m venv $Venv
}

$digest = Get-ReqDigest
$needInstall = $true
if ((Test-Path $Stamp) -and ((Get-Content $Stamp -Raw -ErrorAction SilentlyContinue).Trim() -eq $digest)) {
    $imp = & $VenvPy -c "import flet, yt_dlp, flask, pychromecast" 2>$null
    if ($LASTEXITCODE -eq 0 -and (Test-Path $FletExe)) { $needInstall = $false }
}

if ($needInstall) {
    Info "Instalez sau actualizez dependențe în .venv …"
    foreach ($req in @("flet_app\requirements.txt", "desktop_tui\requirements.txt", "requirements.txt")) {
        $p = Join-Path $Root $req
        if (Test-Path $p) { & $VenvPip install --quiet -r $p }
    }
    [System.IO.File]::WriteAllText($Stamp, $digest)
} else {
    Info "Dependențe deja OK (stamp + importuri) — sar peste pip install."
}

if (-not (Test-Path $FletExe)) { Err "flet lipsește în .venv după instalare."; exit 1 }

$bridgeCode = "import runpy`nrunpy.run_module('flet_app.main', run_name='__main__')`n"
Set-Content -Path "$Root\main.py" -Value $bridgeCode -Encoding UTF8

Info "Pornesc: flet build windows …"
$fletArgs = @("build", "windows", "--module-name", "main", "--yes")
if ($ExtraArgs) { $fletArgs += $ExtraArgs }

$env:FLET_DISPLAY_LEVEL = "info"
$process = Start-Process -FilePath $FletExe -ArgumentList $fletArgs -NoNewWindow -Wait -PassThru
$fletCode = $process.ExitCode

if ($fletCode -ne 0) {
    $cmakeInstall = "$Root\build\flutter\build\windows\x64\cmake_install.cmake"
    if (Test-Path $cmakeInstall) {
        $content = Get-Content $cmakeInstall -Raw
        if ($content -match "vcruntime140_1\.dll") {
            Warn "Patch CMake pentru vcruntime140_1.dll …"
            $content = $content -replace '(?i)C:/Windows/System32/vcruntime140_1\.dll', 'C:/Program Files/Python313/vcruntime140_1.dll'
            Set-Content -Path $cmakeInstall -Value $content -Encoding UTF8

            $cmakeExe = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
            if (Test-Path $cmakeExe) {
                Push-Location "$Root\build\flutter\build\windows\x64"
                & $cmakeExe -DBUILD_TYPE=Release -P cmake_install.cmake
                if ($LASTEXITCODE -eq 0) { $fletCode = 0; Info "Asamblat cu succes după patch." }
                Pop-Location
            }
        }
    }
}
if ($fletCode -ne 0) { Err "flet build a eșuat (cod $fletCode)."; exit $fletCode }
Info "Gata → $Root\build\windows\"
