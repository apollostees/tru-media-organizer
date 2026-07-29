#!/bin/bash
# TRU Media Organizer launcher — double-click in Finder to start the app.
# Runs under Terminal.app so it keeps Terminal's existing Files & Folders
# access (the compiled .app loses this — see CLAUDE.md's ".app Launcher —
# Lesson Learned" section).

DIR="$(cd "$(dirname "$0")" && pwd)"

# Free port 8080 if something is already using it
PID=$(lsof -ti:8080 2>/dev/null)
if [ -n "$PID" ]; then
    echo "Freeing port 8080 (PID $PID)..."
    kill -9 $PID 2>/dev/null
    sleep 1
fi

echo "Starting TRU Media Organizer..."
"$DIR/.venv/bin/python" "$DIR/app.py" &
APP_PID=$!

# Poll until NiceGUI is ready, then open the browser
until curl -sf --max-time 1 http://127.0.0.1:8080 > /dev/null 2>&1; do
    sleep 1
done
open http://127.0.0.1:8080

echo "TRU Media Organizer is running at http://127.0.0.1:8080"
echo "Close the browser tab or press Ctrl+C here to stop the server."

wait $APP_PID
echo "TRU Media Organizer stopped."
