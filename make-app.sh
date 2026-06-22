#!/bin/bash
# Builds "TRU Media Organizer.app" in the project directory.
# Run this once after cloning, or any time you want to rebuild the launcher.
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$DIR/TRU Media Organizer.app"
ICON_SRC="$DIR/disc.png"
ICONSET="/tmp/tru_organizer.iconset"
ICNS="/tmp/tru_organizer.icns"

# ── 1. Build icon ─────────────────────────────────────────────────────────────
mkdir -p "$ICONSET"
sips -z 16   16   "$ICON_SRC" --out "$ICONSET/icon_16x16.png"      >/dev/null
sips -z 32   32   "$ICON_SRC" --out "$ICONSET/icon_16x16@2x.png"   >/dev/null
sips -z 32   32   "$ICON_SRC" --out "$ICONSET/icon_32x32.png"      >/dev/null
sips -z 64   64   "$ICON_SRC" --out "$ICONSET/icon_32x32@2x.png"   >/dev/null
sips -z 128  128  "$ICON_SRC" --out "$ICONSET/icon_128x128.png"    >/dev/null
sips -z 256  256  "$ICON_SRC" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256  256  "$ICON_SRC" --out "$ICONSET/icon_256x256.png"    >/dev/null
sips -z 512  512  "$ICON_SRC" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512  512  "$ICON_SRC" --out "$ICONSET/icon_512x512.png"    >/dev/null
iconutil -c icns "$ICONSET" -o "$ICNS"

# ── 2. Compile AppleScript applet ────────────────────────────────────────────
# Call the venv python directly — no shell script file execution needed,
# which avoids "Operation not permitted" on Google Drive paths.
# Poll port 8080 instead of a fixed delay so Chrome only opens when ready.
osacompile -o "$APP" - <<'APPLESCRIPT'
on run
    set projDir to do shell script "dirname " & quoted form of (POSIX path of (path to me))
    set pyBin   to projDir & "/.venv/bin/python"
    set appScript to projDir & "/app.py"
    do shell script quoted form of pyBin & " " & quoted form of appScript & " >> /tmp/tru-organizer.log 2>&1 &"

    -- Poll until port 8080 is open (up to 30 s)
    repeat 30 times
        try
            do shell script "nc -z 127.0.0.1 8080"
            exit repeat
        end try
        delay 1
    end repeat

    open location "http://127.0.0.1:8080"
end run
APPLESCRIPT

# ── 3. Patch the bundle — the critical fix ───────────────────────────────────
# osacompile ships Assets.car + CFBundleIconName; macOS renders the stock
# applet icon from the catalog and ignores applet.icns entirely.
# Delete the catalog and the key so it falls back to CFBundleIconFile → applet.icns.
rm -f "$APP/Contents/Resources/Assets.car"
/usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleIconFile applet" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleName TRU Media Organizer" "$APP/Contents/Info.plist" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Add :CFBundleName string 'TRU Media Organizer'" "$APP/Contents/Info.plist"

# ── 4. Install icon ───────────────────────────────────────────────────────────
cp "$ICNS" "$APP/Contents/Resources/applet.icns"

# ── 5. Flush Finder / Launch Services cache ───────────────────────────────────
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$APP" 2>/dev/null || true

echo "Built: $APP"
echo "Double-click it to launch TRU Media Organizer."
