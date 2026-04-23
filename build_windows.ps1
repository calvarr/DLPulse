# =============================================================================
# build_windows.ps1 — Build Flet desktop for Windows (native PowerShell).
# From repo root (yt/): .\build_windows.ps1 [extra flet args]
#
# Checks Python 3.11+, .venv, imports; pip install only when needed.
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
if (-not $py) { Err "python or py not found on PATH (install Python 3.11+ from python.org)."; exit 1 }

$verOk = & $py -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
if ($LASTEXITCODE -ne 0) { Err "Python 3.11 or newer required. Found: $(& $py -c 'import sys; print(sys.version)' 2>$null)"; exit 1 }
Info "Python: $py"

$Venv = "$Root\.venv"
$VenvPy = "$Venv\Scripts\python.exe"
$VenvPip = "$Venv\Scripts\pip.exe"
$FletExe = "$Venv\Scripts\flet.exe"
$Stamp = "$Venv\.yt_build_deps_stamp"

if (-not (Test-Path $VenvPy)) {
    Info "Creating .venv …"
    & $py -m venv $Venv
}

$digest = Get-ReqDigest
$needInstall = $true
if ((Test-Path $Stamp) -and ((Get-Content $Stamp -Raw -ErrorAction SilentlyContinue).Trim() -eq $digest)) {
    $imp = & $VenvPy -c "import flet, yt_dlp, flask, pychromecast" 2>$null
    if ($LASTEXITCODE -eq 0 -and (Test-Path $FletExe)) { $needInstall = $false }
}

if ($needInstall) {
    Info "Installing or updating dependencies in .venv …"
    foreach ($req in @("flet_app\requirements.txt", "desktop_tui\requirements.txt", "requirements.txt")) {
        $p = Join-Path $Root $req
        if (Test-Path $p) { & $VenvPip install --quiet -r $p }
    }
    [System.IO.File]::WriteAllText($Stamp, $digest)
} else {
    Info "Dependencies already OK (stamp + imports) — skipping pip install."
}

if (-not (Test-Path $FletExe)) { Err "flet missing in .venv after install."; exit 1 }

$bridgeCode = "import runpy`nrunpy.run_module('flet_app.main', run_name='__main__')`n"
Set-Content -Path "$Root\main.py" -Value $bridgeCode -Encoding UTF8

Info "Running: flet build windows …"
# --no-rich-output: avoids UnicodeEncodeError (cp1252) from Rich Live on Windows console.
$fletArgs = @("build", "windows", "--module-name", "main", "--yes", "--no-rich-output")
if ($ExtraArgs) { $fletArgs += $ExtraArgs }

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:FLET_DISPLAY_LEVEL = "info"
$process = Start-Process -FilePath $FletExe -ArgumentList $fletArgs -NoNewWindow -Wait -PassThru
$fletCode = $process.ExitCode

if ($fletCode -ne 0) {
    $cmakeInstall = "$Root\build\flutter\build\windows\x64\cmake_install.cmake"
    if (Test-Path $cmakeInstall) {
        $content = Get-Content $cmakeInstall -Raw
        if ($content -match "vcruntime140_1\.dll") {
            Warn "Patching CMake for vcruntime140_1.dll …"
            $content = $content -replace '(?i)C:/Windows/System32/vcruntime140_1\.dll', 'C:/Program Files/Python313/vcruntime140_1.dll'
            Set-Content -Path $cmakeInstall -Value $content -Encoding UTF8

            $cmakeExe = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
            if (Test-Path $cmakeExe) {
                Push-Location "$Root\build\flutter\build\windows\x64"
                & $cmakeExe -DBUILD_TYPE=Release -P cmake_install.cmake
                if ($LASTEXITCODE -eq 0) { $fletCode = 0; Info "Assembled successfully after patch." }
                Pop-Location
            }
        }
    }
}
if ($fletCode -ne 0) { Err "flet build failed (exit code $fletCode)."; exit $fletCode }
Info "Done → $Root\build\windows\"
