#!/usr/bin/env bash
# Run after ``flet build macos`` from the repo root ``yt/``.
# Find the first ``*.app`` under ``build/macos`` and write ``build/DLPulse.dmg``.
set -euo pipefail
ROOT="${1:-.}"
cd "$ROOT"

APP="$(find build/macos -name '*.app' -print -quit 2>/dev/null || true)"
if [[ -z "$APP" ]]; then
  echo "No .app found under build/macos:"
  find build/macos -maxdepth 8 2>/dev/null | head -80 || true
  exit 1
fi

OUT="build/DLPulse.dmg"
mkdir -p build
rm -f "$OUT"
hdiutil create -volname "DLPulse" -srcfolder "$APP" -ov -format UDZO "$OUT"
echo "DMG: $(pwd)/$OUT"
