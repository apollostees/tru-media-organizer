#!/bin/bash
# Activate the project venv and start the web UI
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
source "$DIR/.venv/bin/activate"
exec python "$DIR/app.py"
