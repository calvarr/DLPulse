#!/usr/bin/env bash
# Copy ffmpeg (and ffprobe when available) into the first Flet-built *.app under build/macos.
# Used by: build_macos.sh, packaging/macos/make_dmg.sh, and GitHub Actions (macOS job).
#
# Usage: bundle_ffmpeg_into_app.sh [REPO_ROOT]
# Optional env: PYTHON — interpreter that has imageio-ffmpeg (default: python3).
set -euo pipefail

ROOT="$(cd "${1:-.}" && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
if ! command -v "$PY" &>/dev/null; then
  PY="python"
fi

APP="$(find build/macos -name '*.app' -print -quit 2>/dev/null || true)"
if [[ -z "$APP" ]]; then
  echo "bundle_ffmpeg_into_app: no .app found under $ROOT/build/macos" >&2
  exit 1
fi

BIN_DIR="$APP/Contents/Resources/bin"
mkdir -p "$BIN_DIR"

# One line so Windows-oriented docs stay valid; macOS CI uses bash.
FFMPEG_EXE="$("$PY" -c "import imageio_ffmpeg as _i; print(_i.get_ffmpeg_exe())")"
if [[ ! -f "$FFMPEG_EXE" ]]; then
  echo "bundle_ffmpeg_into_app: imageio_ffmpeg did not resolve an ffmpeg binary (PYTHON=$PY)." >&2
  exit 1
fi

cp -f "$FFMPEG_EXE" "$BIN_DIR/ffmpeg"
chmod +x "$BIN_DIR/ffmpeg" || true
echo "bundle_ffmpeg_into_app: $BIN_DIR/ffmpeg"

IO_DIR="$(dirname "$FFMPEG_EXE")"
if [[ -f "$IO_DIR/ffprobe" ]]; then
  cp -f "$IO_DIR/ffprobe" "$BIN_DIR/ffprobe"
  chmod +x "$BIN_DIR/ffprobe" || true
  echo "bundle_ffmpeg_into_app: $BIN_DIR/ffprobe (from imageio-ffmpeg)"
elif command -v ffprobe &>/dev/null; then
  cp -f "$(command -v ffprobe)" "$BIN_DIR/ffprobe"
  chmod +x "$BIN_DIR/ffprobe" || true
  echo "bundle_ffmpeg_into_app: $BIN_DIR/ffprobe (from PATH)"
else
  echo "bundle_ffmpeg_into_app: warning — no ffprobe; some yt-dlp steps may still work." >&2
fi
