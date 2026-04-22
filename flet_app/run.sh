#!/usr/bin/env bash
# Pornește aplicația Flet din rădăcina repo-ului (yt/).
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
