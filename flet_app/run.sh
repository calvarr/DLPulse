#!/usr/bin/env bash
# Start the Flet app from the repo root (yt/).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -x "$ROOT/.venv/bin/flet" ]]; then
  exec "$ROOT/.venv/bin/flet" run flet_app "$@"
elif command -v flet &>/dev/null; then
  exec flet run flet_app "$@"
else
  exec python3 -m flet run flet_app "$@"
fi
