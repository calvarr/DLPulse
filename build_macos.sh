#!/usr/bin/env bash
# =============================================================================
# build_macos.sh — Build Flet desktop for macOS (must run on a Mac).
# From repo root (yt/): ./build_macos.sh [extra flet args]
#
# Checks Python 3.11+, .venv, imports; pip install only when needed.
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[build_macos]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
error() { echo -e "${RED}[error]${NC} $*" >&2; }

if [[ "$(uname -s)" != "Darwin" ]]; then
    error "macOS build must run on a Mac (Darwin)."
    exit 1
fi

PYTHON=""
for py in python3.12 python3.11 python3; do
    if command -v "$py" &>/dev/null; then
        PYTHON="$py"
        break
    fi
done
if [[ -z "$PYTHON" ]]; then
    error "Python 3.11+ not on PATH (python.org or Homebrew: brew install python@3.12)."
    exit 1
fi
if ! "$PYTHON" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"; then
    error "Python 3.11 or newer required."
    exit 1
fi
info "Python: $PYTHON"

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
    && "$VENV_PY" -c "import flet, yt_dlp, flask, pychromecast, imageio_ffmpeg, flet_video" 2>/dev/null \
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
    info "Dependencies already OK — skipping pip install."
fi

if [[ ! -x "$FLET" ]]; then
    error "flet missing in .venv."
    exit 1
fi

if [[ ! -f "$ROOT/pyproject.toml" ]]; then
    error "pyproject.toml missing."
    exit 1
fi

info "flet build macos …"
export FLET_DESKTOP_FLAVOR="${FLET_DESKTOP_FLAVOR:-full}"
"$FLET" build macos --yes "$@"

export PYTHON="$VENV_PY"
if bash "$ROOT/packaging/macos/bundle_ffmpeg_into_app.sh" "$ROOT"; then
  info "Bundled ffmpeg/ffprobe into the macOS .app (see packaging/macos/bundle_ffmpeg_into_app.sh)."
else
  warn "ffmpeg bundle step failed — check that build/macos contains a .app after flet build."
fi

info "Done → $ROOT/build/macos/"
info "Optional DMG: (cd \"$ROOT\" && bash packaging/macos/make_dmg.sh \"$ROOT\")"
