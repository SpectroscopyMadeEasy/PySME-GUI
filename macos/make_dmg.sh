#!/bin/sh
# Pack a built app into the installer disk image.
#   macos/make_dmg.sh PySME.app path/to/uv "Volume name" output.dmg
# Also usable to repack a released app without rebuilding it.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=$1 UV=$2 VOLUME=$3 DMG=$4
DMGBUILD_VERSION="${DMGBUILD_VERSION:-1.6.5}"   # needed only for building

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
swift "$ROOT/macos/make_dmg_background.swift" "$WORK"

rm -f "$DMG"
"$UV" tool run --from "dmgbuild==$DMGBUILD_VERSION" dmgbuild \
    -s "$ROOT/macos/dmg_settings.py" \
    -D app="$APP" \
    -D background="$WORK/background.png" \
    -D volume_icon="$APP/Contents/Resources/PySME.icns" \
    "$VOLUME" "$DMG"

# The icon of the .dmg file itself. macOS keeps it beside the file's contents, so
# it shows on this Mac but is usually lost when the file is downloaded.
swift - "$APP/Contents/Resources/PySME.icns" "$DMG" <<'SWIFT'
import Cocoa
let arguments = CommandLine.arguments
guard let icon = NSImage(contentsOfFile: arguments[1]),
      NSWorkspace.shared.setIcon(icon, forFile: arguments[2], options: [])
else {
    FileHandle.standardError.write(Data("could not set the icon of the disk image file\n".utf8))
    exit(1)
}
SWIFT
