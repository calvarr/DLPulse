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

BIN_DIR="$APP/Contents/Resources/bin"
mkdir -p "$BIN_DIR"
FFMPEG_EXE="$(python - <<'PY'
import imageio_ffmpeg
print(imageio_ffmpeg.get_ffmpeg_exe())
PY
)"
if [[ ! -f "$FFMPEG_EXE" ]]; then
  echo "imageio-ffmpeg did not provide an ffmpeg executable." >&2
  exit 1
fi
cp -f "$FFMPEG_EXE" "$BIN_DIR/ffmpeg"
chmod +x "$BIN_DIR/ffmpeg"
echo "Bundled ffmpeg: $BIN_DIR/ffmpeg"

OUT="build/DLPulse.dmg"
mkdir -p build
rm -f "$OUT"
hdiutil create -volname "DLPulse" -srcfolder "$APP" -ov -format UDZO "$OUT"
echo "DMG: $(pwd)/$OUT"
