#!/usr/bin/env bash
# Run after ``flet build macos`` from the repo root (or pass repo root as $1).
# Bundles ffmpeg/ffprobe into the .app, then writes ``build/DLPulse.dmg``.
set -euo pipefail
ROOT="$(cd "${1:-.}" && pwd)"
cd "$ROOT"

# Prefer project venv on dev machines; GitHub Actions uses setup-python (python on PATH).
PY="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  PY="${PYTHON:-python3}"
fi
export PYTHON="$PY"

bash "$ROOT/packaging/macos/bundle_ffmpeg_into_app.sh" "$ROOT"

APP="$(find build/macos -name '*.app' -print -quit 2>/dev/null || true)"
if [[ -z "$APP" ]]; then
  echo "No .app found under build/macos:" >&2
  find build/macos -maxdepth 8 2>/dev/null | head -80 || true
  exit 1
fi

OUT="build/DLPulse.dmg"
mkdir -p build
rm -f "$OUT"
hdiutil create -volname "DLPulse" -srcfolder "$APP" -ov -format UDZO "$OUT"
echo "DMG: $(pwd)/$OUT"
