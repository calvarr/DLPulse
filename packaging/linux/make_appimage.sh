#!/usr/bin/env bash
# Run after ``flet build linux`` from the repo root ``yt/``.
# - If ``*.AppDir`` exists (older Flet): use it as-is.
# - Else: assemble AppDir from the flat bundle under ``build/linux/``
#   (binary + data/ + lib/ + …) and write ``build/DLPulse-x86_64.AppImage``.
set -euo pipefail
ROOT="${1:-.}"
cd "$ROOT"

if ! command -v appimagetool &>/dev/null; then
  echo "appimagetool is not on PATH."
  exit 1
fi

BUNDLE="build/linux"
if [[ ! -d "$BUNDLE" ]]; then
  echo "Missing $BUNDLE — run first: flet build linux"
  exit 1
fi

APPDIR=""
EXISTING="$(find "$BUNDLE" -maxdepth 5 -type d -name '*.AppDir' -print -quit 2>/dev/null || true)"
if [[ -n "$EXISTING" ]]; then
  APPDIR="$EXISTING"
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
SELF="\$0"
while [ -L "\$SELF" ]; do
  DIR="\$(dirname "\$SELF")"
  SELF="\$(readlink "\$SELF")"
  [ "\${SELF#/}" = "\$SELF" ] && SELF="\$DIR/\$SELF"
done
HERE="\$(cd "\$(dirname "\$SELF")" && pwd)"
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
