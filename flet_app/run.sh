#!/usr/bin/env bash
# Start the Flet app from the repo root (yt/).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export FLET_DESKTOP_FLAVOR="${FLET_DESKTOP_FLAVOR:-full}"
export GDK_GL="${GDK_GL:-gles}"
if [[ -x "$ROOT/.venv/bin/flet" ]]; then
  exec "$ROOT/.venv/bin/flet" run flet_app/main.py "$@"
elif command -v flet &>/dev/null; then
  exec flet run flet_app/main.py "$@"
else
  exec python3 -m flet run flet_app/main.py "$@"
fi
