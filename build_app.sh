#!/bin/bash
# Builds "Download Manager.app" in ./dist.   Usage:  ./build_app.sh [--install]
set -e
cd "$(dirname "$0")"

APP_NAME="Download Manager"
BUNDLE_ID="com.yourname.downloadmanager"   # keep in sync with LABEL in autostart.py

python3 -m pip install --upgrade pyinstaller PySide6
# --- icon: use your own icon.icns or icon.png; otherwise draw a default one ---
if [ -f icon.icns ]; then
  echo "Using your icon.icns"
elif [ -f icon.png ]; then
  echo "Converting your icon.png to icon.icns"
  rm -rf icon.iconset && mkdir icon.iconset
  for s in 16 32 128 256 512; do
    sips -z $s $s icon.png --out icon.iconset/icon_${s}x${s}.png >/dev/null
    sips -z $((s*2)) $((s*2)) icon.png --out icon.iconset/icon_${s}x${s}@2x.png >/dev/null
  done
  iconutil -c icns icon.iconset && rm -rf icon.iconset
else
  python3 make_icon.py
fi

rm -rf build dist ./*.spec
pyinstaller --windowed --noconfirm \
  --name "$APP_NAME" \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --icon icon.icns \
  --hidden-import bridge \
  --hidden-import autostart \
  download_manager_qt.py

if [ -d browser-extension ]; then
  cp -R browser-extension dist/browser-extension
else
  echo "Note: no browser-extension folder found next to this script, so it wasn't copied."
fi

if [ "$1" = "--install" ]; then
  rm -rf "/Applications/$APP_NAME.app"
  cp -R "dist/$APP_NAME.app" /Applications/
  echo "Installed to /Applications/$APP_NAME.app"
fi

echo "Done. App: dist/$APP_NAME.app   Extension folder: dist/browser-extension"
open dist
