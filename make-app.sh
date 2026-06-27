#!/bin/bash
# Builds "TRU Media Organizer.app" in the project directory.
# Run this once after cloning, or any time you want to rebuild the launcher.
#
# The venv lives at ~/.tru-media-organizer-venv (outside Google Drive)
# because Google Drive corrupts symlinks, breaking the venv python binary.
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$DIR/TRU Media Organizer.app"
ICON_SRC="$DIR/disc.png"
ICONSET="/tmp/tru_organizer.iconset"
ICNS="/tmp/tru_organizer.icns"
VENV="$HOME/.tru-media-organizer-venv"

# ── 1. Create / refresh venv outside Google Drive ────────────────────────────
if [ ! -f "$VENV/bin/python" ]; then
    echo "Creating venv at $VENV …"
    python3 -m venv "$VENV"
fi
echo "Installing dependencies …"
"$VENV/bin/pip" install -q -r "$DIR/requirements.txt"

# ── 2. Build icon ─────────────────────────────────────────────────────────────
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

# ── 3. Compile AppleScript applet ────────────────────────────────────────────
# The venv python path is baked in at build time (not resolved at runtime)
# so the applet never touches the Google Drive path for the interpreter.
# Port polling ensures Chrome only opens once the server is actually ready.
PYTHON_BIN="$VENV/bin/python"

osacompile -o "$APP" - <<APPLESCRIPT
on run
    set projDir to do shell script "dirname " & quoted form of (POSIX path of (path to me))
    set pyBin     to "$PYTHON_BIN"
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

# ── 4. Patch the bundle (icon fix) ───────────────────────────────────────────
# osacompile ships Assets.car + CFBundleIconName; macOS renders the stock
# applet icon from the catalog and ignores applet.icns entirely.
# Delete the catalog and the key so it falls back to CFBundleIconFile → applet.icns.
rm -f "$APP/Contents/Resources/Assets.car"
/usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleIconFile applet" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleName TRU Media Organizer" "$APP/Contents/Info.plist" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Add :CFBundleName string 'TRU Media Organizer'" "$APP/Contents/Info.plist"

# ── 5. Install icon ───────────────────────────────────────────────────────────
cp "$ICNS" "$APP/Contents/Resources/applet.icns"

# ── 6. Flush Finder / Launch Services cache ───────────────────────────────────
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$APP" 2>/dev/null || true

echo "Built: $APP"
echo "Venv : $VENV"
echo "Double-click the app to launch TRU Media Organizer."
