#!/usr/bin/env bash
# =============================================================================
# build_macos.sh — Build Flet desktop pentru macOS (rulează DOAR pe Mac).
# Din rădăcina repo-ului (yt/): ./build_macos.sh [extra flet args]
#
# Verifică Python 3.11+, .venv, importuri; pip install doar dacă e nevoie.
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[build_macos]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
error() { echo -e "${RED}[error]${NC} $*" >&2; }

if [[ "$(uname -s)" != "Darwin" ]]; then
    error "Build macOS trebuie rulat pe un Mac (Darwin)."
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
    error "Python 3.11+ nu e pe PATH (python.org sau Homebrew: brew install python@3.12)."
    exit 1
fi
if ! "$PYTHON" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"; then
    error "Necesită Python 3.11 sau mai nou."
    exit 1
fi
info "Python: $PYTHON"

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
    info "Dependențe deja OK — sar peste pip install."
fi

if [[ ! -x "$FLET" ]]; then
    error "flet lipsește în .venv."
    exit 1
fi

if [[ ! -f "$ROOT/pyproject.toml" ]]; then
    error "pyproject.toml lipsește."
    exit 1
fi

info "flet build macos …"
"$FLET" build macos --yes "$@"

info "Gata → $ROOT/build/macos/"
