#!/usr/bin/env bash
# Rulează după ``flet build linux`` din rădăcina repo-ului ``yt/``.
# Caută ``*.AppDir`` sub ``build/linux`` și produce ``build/DLPulse-x86_64.AppImage``.
set -euo pipefail
ROOT="${1:-.}"
cd "$ROOT"

if ! command -v appimagetool &>/dev/null; then
  echo "appimagetool nu e pe PATH."
  exit 1
fi

APPDIR="$(find build/linux -maxdepth 4 -type d -name '*.AppDir' -print -quit 2>/dev/null || true)"
if [[ -z "${APPDIR}" ]]; then
  echo "Nu s-a găsit niciun .AppDir sub build/linux:"
  find build/linux -maxdepth 5 -type d 2>/dev/null | head -80 || true
  exit 1
fi

OUT="build/DLPulse-x86_64.AppImage"
mkdir -p build
rm -f "$OUT"
export ARCH=x86_64
appimagetool "$APPDIR" "$OUT"
echo "AppImage: $(realpath "$OUT" 2>/dev/null || echo "$OUT")"
