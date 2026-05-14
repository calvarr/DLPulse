#!/usr/bin/env bash
# After ``flet build linux``, copy ffmpeg (and ffprobe when present) from imageio-ffmpeg into
# ``build/linux/bin/`` so the flat bundle finds tools via ffmpeg_tools (exe_dir/bin).
# AppImage builds still add johnvansickle static ffmpeg in make_appimage.sh (overwrites top-level usr/bin/ffmpeg).
#
# Usage: bundle_imageio_ffmpeg_into_linux_bundle.sh [REPO_ROOT]
# Optional env: PYTHON (default: python3).
set -euo pipefail
ROOT="$(cd "${1:-.}" && pwd)"
cd "$ROOT"

BUNDLE="build/linux"
if [[ ! -d "$BUNDLE" ]]; then
  echo "bundle_imageio_ffmpeg_into_linux_bundle: missing $BUNDLE — skip." >&2
  exit 0
fi

PY="${PYTHON:-python3}"
if ! command -v "$PY" &>/dev/null; then
  PY="python"
fi

BIN="$BUNDLE/bin"
mkdir -p "$BIN"
SRC="$("$PY" -c "import imageio_ffmpeg as _i; print(_i.get_ffmpeg_exe())")"
if [[ ! -f "$SRC" ]]; then
  echo "bundle_imageio_ffmpeg_into_linux_bundle: imageio_ffmpeg did not resolve ffmpeg (PYTHON=$PY)." >&2
  exit 1
fi
cp -f "$SRC" "$BIN/ffmpeg"
chmod +x "$BIN/ffmpeg" || true
echo "bundle_imageio_ffmpeg_into_linux_bundle: $BIN/ffmpeg"

IO="$(dirname "$SRC")"
if [[ -f "$IO/ffprobe" ]]; then
  cp -f "$IO/ffprobe" "$BIN/ffprobe"
  chmod +x "$BIN/ffprobe" || true
  echo "bundle_imageio_ffmpeg_into_linux_bundle: $BIN/ffprobe (imageio folder)"
elif command -v ffprobe &>/dev/null; then
  cp -f "$(command -v ffprobe)" "$BIN/ffprobe"
  chmod +x "$BIN/ffprobe" || true
  echo "bundle_imageio_ffmpeg_into_linux_bundle: $BIN/ffprobe (PATH)"
else
  echo "bundle_imageio_ffmpeg_into_linux_bundle: warning — no ffprobe." >&2
fi
