#!/usr/bin/env bash
# Start the Flet app from the repo root (yt/).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export FLET_DESKTOP_FLAVOR="${FLET_DESKTOP_FLAVOR:-full}"
# Do NOT default GDK_GL to gles here: on X11 it breaks flet-video (media_kit) → gray video.
# main.py::_apply_linux_gl_env() is inactive by default (same as plain ``python vd.py``);
# set DLPULSE_LEGACY_LINUX_GL_ENV=1 before launch to restore old GDK / FLET_SW_GL helpers.
# If the internal player is still gray: FLET_SW_GL=1 "$0" "$@"  (software GL, slower)
if [[ -x "$ROOT/.venv/bin/flet" ]]; then
  exec "$ROOT/.venv/bin/flet" run flet_app/main.py "$@"
elif command -v flet &>/dev/null; then
  exec flet run flet_app/main.py "$@"
else
  exec python3 -m flet run flet_app/main.py "$@"
fi
