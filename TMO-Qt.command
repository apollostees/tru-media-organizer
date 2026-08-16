#!/bin/bash
# TRU Media Organizer (Qt) launcher — double-click in Finder to start the native app.
# Run:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# once before first use.
#
# Unlike TMO.command (the NiceGUI launcher), this is a native window —
# no port to free, no server to poll for, no browser tab to open. Closing
# the window is what stops the app. Runs under Terminal.app's TCC identity,
# same as TMO.command — see CLAUDE.md's ".app Launcher — Lesson Learned".

DIR="$(cd "$(dirname "$0")" && pwd)"

"$DIR/.venv/bin/python" "$DIR/app_qt.py"
