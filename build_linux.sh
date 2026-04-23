#!/usr/bin/env bash
# =============================================================================
# build_linux.sh — Build Flet desktop for Linux (must run on Linux).
# From repo root (yt/): ./build_linux.sh [--appimage] [extra flet args]
#
# Checks Python 3.11+, .venv, imports (flet, yt-dlp, …). Runs pip only when
# something is missing or requirements files changed (stamp).
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[build_linux]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
error() { echo -e "${RED}[error]${NC} $*" >&2; }

if [[ "$(uname -s)" != "Linux" ]]; then
    error "This script must be run on Linux."
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
    error "Python 3.11+ not found on PATH. Install python3 and try again."
    exit 1
fi
if ! "$PYTHON" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"; then
    error "Python 3.11 or newer required (found: $("$PYTHON" -c 'import sys; print(sys.version)') )."
    exit 1
fi
info "Python: $PYTHON ($("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"))"

VENV="$ROOT/.venv"
if [[ ! -f "$VENV/bin/python" ]]; then
    info "Creating .venv …"
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
    info "Installing or updating dependencies in .venv …"
    "$VENV_PIP" install --quiet -r "$ROOT/flet_app/requirements.txt"
    "$VENV_PIP" install --quiet -r "$ROOT/desktop_tui/requirements.txt"
    if [[ -f "$ROOT/requirements.txt" ]]; then
        "$VENV_PIP" install --quiet -r "$ROOT/requirements.txt"
    fi
    printf '%s\n' "$DIGEST" > "$STAMP"
else
    info "Dependencies already OK (stamp + imports) — skipping pip install."
fi

if [[ ! -x "$FLET" ]]; then
    error "flet missing in .venv after install."
    exit 1
fi

if [[ ! -f "$ROOT/pyproject.toml" ]]; then
    error "pyproject.toml missing."
    exit 1
fi

if ! command -v ffmpeg &>/dev/null; then
    warn "ffmpeg not on PATH — some yt-dlp merges may fail."
fi

export CFLAGS="${CFLAGS:+$CFLAGS }-Wno-macro-redefined"
export CXXFLAGS="${CXXFLAGS:+$CXXFLAGS }-Wno-macro-redefined"

info "flet build linux …"
"$FLET" build linux --yes "${EXTRA_ARGS[@]}"

BUILD_DIR="$ROOT/build/linux"
info "Done → $BUILD_DIR"

if [[ "$MAKE_APPIMAGE" == true ]]; then
    if [[ -f "$ROOT/packaging/linux/make_appimage.sh" ]]; then
        bash "$ROOT/packaging/linux/make_appimage.sh" "$ROOT" || warn "make_appimage.sh failed."
    elif ! command -v appimagetool &>/dev/null; then
        warn "appimagetool not on PATH."
    else
        APPDIR="$(find "$BUILD_DIR" -maxdepth 3 -type d -name '*.AppDir' -print -quit 2>/dev/null || true)"
        if [[ -n "$APPDIR" ]]; then
            ARCH=x86_64 appimagetool "$APPDIR" "$ROOT/build/DLPulse-x86_64.AppImage"
            info "AppImage: $ROOT/build/DLPulse-x86_64.AppImage"
        else
            warn "No .AppDir found under $BUILD_DIR"
        fi
    fi
fi
