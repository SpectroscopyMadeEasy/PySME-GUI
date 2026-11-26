#!/bin/bash
# Build "PySME.app" and a drag-to-Applications disk image.
#
#   macos/build.sh                      # result in dist/
#   UV_BIN=/path/to/uv macos/build.sh   # bundle a specific uv
#
# Needs the Xcode command line tools. The app bundles the GUI code and the "uv"
# installer; Python and PySME are fetched on first launch.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
APP="$DIST/PySME.app"
UV_VERSION="${UV_VERSION:-0.13.10}"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/pysme_gui/__init__.py")"
# macOS allows only numbers in its own version fields: 0.3.0a1 -> 0.3.0
SHORT_VERSION="$(printf '%s' "$VERSION" | sed -E 's/(a|b|rc)[0-9]+$//')"
printf '%s' "$SHORT_VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' ||
    { echo "Unexpected version: $VERSION (use 1.2.3, 1.2.3a1, 1.2.3b1 or 1.2.3rc1)" >&2; exit 1; }
FEEDBACK_EMAIL="$(sed -n 's/^FEEDBACK_EMAIL = "\(.*\)"/\1/p' "$ROOT/pysme_gui/__init__.py")"
ISSUE_REPO="$(sed -n 's/^ISSUE_REPO = "\(.*\)"/\1/p' "$ROOT/pysme_gui/__init__.py")"
BUILD="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo local)-$(date +%Y%m%d%H%M)"

echo "Building PySME $VERSION ($BUILD)"
# A disk image that is handed out must come from its release tag, unchanged.
if ! git -C "$ROOT" tag --points-at HEAD 2>/dev/null | grep -qx "v$VERSION" ||
    [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]; then
    echo "Note: this is a local build, not the release v$VERSION."
fi
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/bin" "$APP/Contents/Resources/app" "$DIST/work"

# 1. uv, for the architecture of the app.
if [ -z "${UV_BIN:-}" ]; then
    UV_BIN="$DIST/work/uv-$UV_VERSION"
    if [ ! -x "$UV_BIN" ]; then
        echo "Downloading uv $UV_VERSION"
        URL="https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-aarch64-apple-darwin.tar.gz"
        curl -fsSL "$URL" -o "$DIST/work/uv.tar.gz"
        curl -fsSL "$URL.sha256" -o "$DIST/work/uv.sha256"
        EXPECTED="$(awk '{print $1}' "$DIST/work/uv.sha256")"
        ACTUAL="$(shasum -a 256 "$DIST/work/uv.tar.gz" | awk '{print $1}')"
        [ "$EXPECTED" = "$ACTUAL" ] || { echo "uv checksum mismatch" >&2; exit 1; }
        tar -xzf "$DIST/work/uv.tar.gz" -C "$DIST/work" uv-aarch64-apple-darwin/uv
        mv "$DIST/work/uv-aarch64-apple-darwin/uv" "$UV_BIN"
    fi
fi
cp "$UV_BIN" "$APP/Contents/Resources/bin/uv"
chmod 755 "$APP/Contents/Resources/bin/uv"

# 2. The GUI code, without caches.
rsync -a --exclude '__pycache__' --exclude '*.pyc' "$ROOT/pysme_gui" "$APP/Contents/Resources/app/"
cp "$ROOT/LICENSE" "$APP/Contents/Resources/LICENSE"

# 3. The native shell and its icon.
swiftc -O -target arm64-apple-macos14.0 "$ROOT/macos/PySMEApp.swift" -o "$APP/Contents/MacOS/PySME"
swift "$ROOT/macos/make_icon.swift" "$ROOT/assets/pysme_logo-ver-light.png" "$DIST/work/PySME.iconset"
iconutil -c icns "$DIST/work/PySME.iconset" -o "$APP/Contents/Resources/PySME.icns"
cp "$ROOT/macos/setup.html" "$APP/Contents/Resources/setup.html"
cp "$DIST/work/PySME.iconset/icon_128x128@2x.png" "$APP/Contents/Resources/setup-icon.png"
sed -e "s/@VERSION@/$VERSION/g" -e "s/@SHORT_VERSION@/$SHORT_VERSION/g" -e "s/@BUILD@/$BUILD/g" -e "s/@FEEDBACK_EMAIL@/$FEEDBACK_EMAIL/g" -e "s|@ISSUE_REPO@|$ISSUE_REPO|g" "$ROOT/macos/Info.plist" > "$APP/Contents/Info.plist"
plutil -lint "$APP/Contents/Info.plist" >/dev/null

# 4. Sign. Without a Developer ID this is an ad-hoc signature (see README).
IDENTITY="${CODESIGN_IDENTITY:--}"
codesign --force --sign "$IDENTITY" "$APP/Contents/Resources/bin/uv"
codesign --force --sign "$IDENTITY" "$APP"
codesign --verify --strict "$APP"

# 5. Disk image with the usual Applications shortcut.
STAGE="$DIST/work/dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="$DIST/PySME-$VERSION-arm64.dmg"
rm -f "$DMG"
hdiutil create -quiet -volname "PySME $VERSION" -srcfolder "$STAGE" -format UDZO "$DMG"
rm -rf "$STAGE"

echo "App: $APP"
echo "DMG: $DMG ($(du -h "$DMG" | awk '{print $1}'))"
