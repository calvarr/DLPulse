#!/usr/bin/env bash
# Run after ``flet build linux`` from the repo root ``yt/``.
# - If ``*.AppDir`` exists (older Flet): use it as-is.
# - Else: assemble AppDir from the flat bundle under ``build/linux/``
#   (binary + data/ + lib/ + …) and write ``build/DLPulse-x86_64.AppImage``.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${1:-.}"
cd "$ROOT"

# Static amd64 ffmpeg/ffprobe (johnvansickle) into AppDir/usr/bin so yt-dlp MP3 merge/extract
# works without a system ffmpeg. Set SKIP_FFMPEG_BUNDLE=1 to skip (saves ~40MB download + ~150MB in AppImage).
bundle_static_ffmpeg_amd64() {
  local apprdir="$1"
  local dest="$apprdir/usr/bin"
  mkdir -p "$dest"
  if [[ "${SKIP_FFMPEG_BUNDLE:-}" == "1" ]]; then
    echo "SKIP_FFMPEG_BUNDLE=1 — skipping bundled ffmpeg."
    return 0
  fi
  if ! command -v curl &>/dev/null; then
    echo "curl not on PATH — cannot download static ffmpeg; install curl or set SKIP_FFMPEG_BUNDLE=1." >&2
    return 0
  fi
  local cache="$ROOT/build/.cache"
  mkdir -p "$cache"
  local arc="$cache/ffmpeg-release-amd64-static.tar.xz"
  local url="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
  if [[ ! -f "$arc" ]] || [[ ! -s "$arc" ]]; then
    echo "Downloading static ffmpeg (amd64) for AppImage bundle…"
    curl -fsSL --retry 3 --retry-delay 2 -o "$arc.part" "$url"
    mv -f "$arc.part" "$arc"
  fi
  local tmp
  tmp="$(mktemp -d)"
  tar -xJf "$arc" -C "$tmp"
  local ff ffpr
  ff="$(find "$tmp" -type f -name ffmpeg -executable 2>/dev/null | head -1)"
  ffpr="$(find "$tmp" -type f -name ffprobe -executable 2>/dev/null | head -1)"
  if [[ -z "$ff" ]] || [[ ! -f "$ff" ]]; then
    rm -rf "$tmp"
    echo "Could not locate ffmpeg in static archive — AppImage will rely on system PATH." >&2
    return 0
  fi
  cp -f "$ff" "$dest/ffmpeg"
  chmod +x "$dest/ffmpeg"
  if [[ -n "$ffpr" ]] && [[ -f "$ffpr" ]]; then
    cp -f "$ffpr" "$dest/ffprobe"
    chmod +x "$dest/ffprobe"
  fi
  rm -rf "$tmp"
  echo "Bundled static ffmpeg into $dest (GPL static build; see upstream johnvansickle.com/ffmpeg)."
}

# Older Flet drops a ready-made .AppDir whose AppRun may not export PATH for bundled tools.
wrap_legacy_apprun_for_bundled_bin() {
  local apprdir="$1"
  local apprun="$apprdir/AppRun"
  [[ -f "$apprun" ]] || return 0
  if grep -q 'DLPULSE_APPIMAGE_BIN_PATH' "$apprun" 2>/dev/null; then
    return 0
  fi
  mv "$apprun" "$apprdir/AppRun.fletorig"
  cat > "$apprun" <<'EOS'
#!/bin/sh
# DLPULSE_APPIMAGE_BIN_PATH — bundled ffmpeg/ffprobe live in usr/bin
SELF="$0"
while [ -L "$SELF" ]; do
  DIR="$(dirname "$SELF")"
  SELF="$(readlink "$SELF")"
  [ "${SELF#/}" = "$SELF" ] && SELF="$DIR/$SELF"
done
HERE="$(cd "$(dirname "$SELF")" && pwd)"
export PATH="$HERE/usr/bin:${PATH:-}"
exec "$HERE/AppRun.fletorig" "$@"
EOS
  chmod +x "$apprun"
  echo "Wrapped legacy AppRun so bundled usr/bin is on PATH."
}

BUNDLE="build/linux"
if [[ ! -d "$BUNDLE" ]]; then
  echo "Missing $BUNDLE — run first: flet build linux"
  exit 1
fi

# Portable flat bundle + later AppDir: imageio ffmpeg under build/linux/bin (static johnvansickle still added below).
if [[ -f "$SCRIPT_DIR/bundle_imageio_ffmpeg_into_linux_bundle.sh" ]]; then
  bash "$SCRIPT_DIR/bundle_imageio_ffmpeg_into_linux_bundle.sh" "$ROOT" || {
    echo "make_appimage: warning — imageio ffmpeg bundle failed (continuing)." >&2
  }
fi

if ! command -v appimagetool &>/dev/null; then
  echo "appimagetool is not on PATH."
  exit 1
fi

APPDIR=""
LEGACY_APPDIR=false
EXISTING="$(find "$BUNDLE" -maxdepth 5 -type d -name '*.AppDir' -print -quit 2>/dev/null || true)"
if [[ -n "$EXISTING" ]]; then
  APPDIR="$EXISTING"
  LEGACY_APPDIR=true
  echo "Using existing AppDir: $APPDIR"
else
  # New Flet bundle: no .AppDir, Flutter + Python layout next to the binary.
  if [[ ! -d "$BUNDLE/data/flutter_assets" ]]; then
    echo "Neither .AppDir nor a recognized Flet bundle (missing $BUNDLE/data/flutter_assets):"
    find "$BUNDLE" -maxdepth 3 -type d 2>/dev/null | head -40 || true
    exit 1
  fi

  MAIN=""
  for try in dlpulse DLPulse; do
    if [[ -f "$BUNDLE/$try" ]]; then
      if file -b "$BUNDLE/$try" | grep -qiE 'ELF.*executable'; then
        MAIN="$BUNDLE/$try"
        break
      fi
    fi
  done
  if [[ -z "$MAIN" ]]; then
    while IFS= read -r -d '' f; do
      if file -b "$f" | grep -qiE 'ELF.*executable'; then
        MAIN="$f"
        break
      fi
    done < <(find "$BUNDLE" -maxdepth 1 -type f -print0 2>/dev/null || true)
  fi
  if [[ -z "$MAIN" ]] || [[ ! -f "$MAIN" ]]; then
    echo "No ELF executable found in $BUNDLE (maxdepth 1):"
    find "$BUNDLE" -maxdepth 1 -type f -ls 2>/dev/null || true
    exit 1
  fi
  BIN_BASENAME="$(basename "$MAIN")"
  echo "Main binary: $BIN_BASENAME"

  APPDIR="build/DLPulse.AppDir"
  rm -rf "$APPDIR"
  mkdir -p "$APPDIR/usr/bin"
  cp -a "$BUNDLE"/. "$APPDIR/usr/bin/"

  ICON_SRC=""
  for try in flet_app/icon.png flet_app/cofe.png; do
    if [[ -f "$try" ]]; then
      ICON_SRC="$try"
      break
    fi
  done
  if [[ -n "$ICON_SRC" ]]; then
    cp -f "$ICON_SRC" "$APPDIR/dlpulse.png"
  fi

  cat > "$APPDIR/AppRun" <<EOF
#!/bin/sh
# DLPULSE_APPIMAGE_BIN_PATH — bundled ffmpeg in usr/bin for yt-dlp
SELF="\$0"
while [ -L "\$SELF" ]; do
  DIR="\$(dirname "\$SELF")"
  SELF="\$(readlink "\$SELF")"
  [ "\${SELF#/}" = "\$SELF" ] && SELF="\$DIR/\$SELF"
done
HERE="\$(cd "\$(dirname "\$SELF")" && pwd)"
export PATH="\$HERE/usr/bin:\${PATH:-}"
cd "\$HERE/usr/bin" || exit 1
exec "./${BIN_BASENAME}" "\$@"
EOF
  chmod +x "$APPDIR/AppRun"

  ICON_LINE=""
  [[ -f "$APPDIR/dlpulse.png" ]] && ICON_LINE="Icon=dlpulse"

  {
    echo "[Desktop Entry]"
    echo "Version=1.0"
    echo "Type=Application"
    echo "Name=DLPulse"
    echo "Comment=Media downloader with Chromecast support"
    echo "Exec=${BIN_BASENAME} %u"
    [[ -n "$ICON_LINE" ]] && echo "$ICON_LINE"
    echo "Categories=Network;AudioVideo;Utility;"
    echo "Terminal=false"
  } > "$APPDIR/dlpulse.desktop"

  chmod +x "$APPDIR/usr/bin/$BIN_BASENAME" 2>/dev/null || true
  echo "AppDir assembled: $APPDIR"
fi

bundle_static_ffmpeg_amd64 "$APPDIR"
if [[ "$LEGACY_APPDIR" == true ]]; then
  wrap_legacy_apprun_for_bundled_bin "$APPDIR"
fi

OUT="build/DLPulse-x86_64.AppImage"
mkdir -p build
rm -f "$OUT"
export ARCH=x86_64
# Skip AppStream metadata on CI (avoids warnings / failures).
if appimagetool --help 2>&1 | grep -q no-appstream; then
  appimagetool --no-appstream "$APPDIR" "$OUT"
else
  appimagetool "$APPDIR" "$OUT"
fi
echo "AppImage: $(realpath "$OUT" 2>/dev/null || echo "$OUT")"
