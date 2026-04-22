#!/usr/bin/env bash
# Rulează după ``flet build macos`` din rădăcina repo-ului ``yt/``.
# Găsește primul ``*.app`` sub ``build/macos`` și produce ``build/DLPulse.dmg``.
set -euo pipefail
ROOT="${1:-.}"
cd "$ROOT"

APP="$(find build/macos -name '*.app' -print -quit 2>/dev/null || true)"
if [[ -z "$APP" ]]; then
  echo "Nu s-a găsit niciun .app sub build/macos:"
  find build/macos -maxdepth 8 2>/dev/null | head -80 || true
  exit 1
fi

OUT="build/DLPulse.dmg"
mkdir -p build
rm -f "$OUT"
hdiutil create -volname "DLPulse" -srcfolder "$APP" -ov -format UDZO "$OUT"
echo "DMG: $(pwd)/$OUT"
