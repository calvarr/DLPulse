#!/usr/bin/env bash
# =============================================================================
# build_linux.sh — Build Flet desktop pentru Linux (rulează pe Linux).
# Din rădăcina repo-ului (yt/): ./build_linux.sh [--appimage] [extra flet args]
#
# Verifică Python 3.11+, .venv, importuri (flet, yt-dlp, …). Instalează pip doar
# dacă lipsește ceva sau s-au schimbat fișierele requirements (stamp).
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[build_linux]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
error() { echo -e "${RED}[error]${NC} $*" >&2; }

if [[ "$(uname -s)" != "Linux" ]]; then
    error "Acest script trebuie rulat pe Linux."
    exit 1
fi

MAKE_APPIMAGE=false
EXTRA_ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--appimage" ]]; then
        MAKE_APPIMAGE=true
    else
        EXTRA_ARGS+=("$arg")
    fi
done

PYTHON=""
for py in python3.12 python3.11 python3; do
    if command -v "$py" &>/dev/null; then
        PYTHON="$py"
        break
    fi
done
if [[ -z "$PYTHON" ]]; then
    error "Python 3.11+ nu e pe PATH. Instalează python3 și încearcă din nou."
    exit 1
fi
if ! "$PYTHON" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"; then
    error "Necesită Python 3.11 sau mai nou (găsit: $("$PYTHON" -c 'import sys; print(sys.version)') )."
    exit 1
fi
info "Python: $PYTHON ($("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"))"

VENV="$ROOT/.venv"
if [[ ! -f "$VENV/bin/python" ]]; then
    info "Creez .venv …"
    "$PYTHON" -m venv "$VENV"
fi
VENV_PY="$VENV/bin/python"
VENV_PIP="$VENV/bin/pip"
FLET="$VENV/bin/flet"
STAMP="$VENV/.yt_build_deps_stamp"

req_digest() {
    "$PYTHON" -c "
import hashlib
from pathlib import Path
h = hashlib.sha256()
root = Path('$ROOT')
for rel in ('flet_app/requirements.txt', 'desktop_tui/requirements.txt', 'requirements.txt'):
    p = root / rel
    if p.is_file():
        h.update(rel.encode())
        h.update(p.read_bytes())
print(h.hexdigest())
"
}

DIGEST="$(req_digest)"
NEED_INSTALL=1
if [[ -f "$STAMP" ]] && [[ "$(cat "$STAMP" 2>/dev/null | head -1)" == "$DIGEST" ]] \
    && "$VENV_PY" -c "import flet, yt_dlp, flask, pychromecast" 2>/dev/null \
    && [[ -x "$FLET" ]]; then
    NEED_INSTALL=0
fi

if [[ "$NEED_INSTALL" -eq 1 ]]; then
    info "Instalez sau actualizez dependențe în .venv …"
    "$VENV_PIP" install --quiet -r "$ROOT/flet_app/requirements.txt"
    "$VENV_PIP" install --quiet -r "$ROOT/desktop_tui/requirements.txt"
    if [[ -f "$ROOT/requirements.txt" ]]; then
        "$VENV_PIP" install --quiet -r "$ROOT/requirements.txt"
    fi
    printf '%s\n' "$DIGEST" > "$STAMP"
else
    info "Dependențe deja OK (stamp + importuri) — sar peste pip install."
fi

if [[ ! -x "$FLET" ]]; then
    error "flet lipsește în .venv după instalare."
    exit 1
fi

if [[ ! -f "$ROOT/pyproject.toml" ]]; then
    error "pyproject.toml lipsește."
    exit 1
fi

if ! command -v ffmpeg &>/dev/null; then
    warn "ffmpeg nu e pe PATH — unele merge-uri yt-dlp pot eșua."
fi

export CFLAGS="${CFLAGS:+$CFLAGS }-Wno-macro-redefined"
export CXXFLAGS="${CXXFLAGS:+$CXXFLAGS }-Wno-macro-redefined"

info "flet build linux …"
"$FLET" build linux --yes "${EXTRA_ARGS[@]}"

BUILD_DIR="$ROOT/build/linux"
info "Gata → $BUILD_DIR"

if [[ "$MAKE_APPIMAGE" == true ]]; then
    if ! command -v appimagetool &>/dev/null; then
        warn "appimagetool nu e pe PATH."
    else
        APPDIR="$BUILD_DIR/dlpulse.AppDir"
        if [[ -d "$APPDIR" ]]; then
            appimagetool "$APPDIR" "$ROOT/build/DLPulse-x86_64.AppImage"
            info "AppImage: $ROOT/build/DLPulse-x86_64.AppImage"
        else
            warn "Nu există $APPDIR — verifică structura din $BUILD_DIR"
        fi
    fi
fi
