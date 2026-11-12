# Download Manager

A macOS download manager built with PySide6. It supports queued downloads, parallel segmented transfers when the server supports byte ranges, pause and resume, a menu bar icon, and handoff from a browser extension.

## Requirements

- macOS
- Python 3.11 or newer
- PySide6

## Run from source

```sh
python3 -m pip install PySide6
python3 download_manager_qt.py
```

## Build the macOS app

```sh
./build_app.sh
```

The app bundle is written to `dist/`. Pass `--install` to copy it to `/Applications`. Generated build output is excluded from Git.

## Browser extension

Load the `browser-extension/` directory as an unpacked extension in a Chromium based browser. Start the app to enable the local bridge on `127.0.0.1:9614`, then use the extension toolbar button to toggle download handoff.

## Project files

- `download_manager_qt.py` — PySide6 desktop interface
- `engine.py` — standard library download engine
- `bridge.py` — local browser to app bridge
- `autostart.py` — macOS login item support
- `browser-extension/` — Chromium extension
- `icon.icns` — app icon
