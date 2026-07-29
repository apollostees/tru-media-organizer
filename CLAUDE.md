# TRU-MEDIA-ORGANIZER

## Purpose

TRU-MEDIA-ORGANIZER is a local media deduplicator and organizer for photo and video collections. It identifies duplicate files across multiple directories using SHA-256 hashing, lets you safely review and move them, then organizes surviving files into a clean `PHOTO/<YYYY-MM-DD>` / `VIDEO/<YYYY-MM-DD>` folder structure using embedded EXIF or ffprobe metadata dates.

The workflow is intentionally three-step so humans stay in control before anything is moved or deleted.

## Architecture

| File | Role |
|------|------|
| `tru_organizer.py` | CLI backend — all business logic (scan, move, organize) |
| `app.py` | NiceGUI web frontend — wraps the same logic in a browser UI at `http://localhost:8080` |
| `launch.command` | **Recommended launcher** — plain bash script, runs under Terminal.app |
| `requirements.txt` | Runtime deps: `Pillow` (EXIF), `nicegui` (web UI) |
| `disc.png` | App favicon / launcher icon |

### Core logic (`tru_organizer.py`)

**Supported formats**
- Photos: `.jpg .jpeg .png .gif .bmp .tiff .tif .heic .heif` + raw formats (`.cr2 .cr3 .nef .nrw .arw .dng .orf .rw2 .pef .srw .x3f`)
- Videos: `.mp4 .mov .avi .mkv .m4v .wmv .flv .webm .3gp .mts .m2ts .ts .mpg .mpeg .ogv .divx`

**Date extraction priority**
1. EXIF `DateTimeOriginal` / `DateTimeDigitized` / `DateTime` (photos via Pillow — JPEG/TIFF only)
2. `ffprobe` `creation_time` / `date` metadata (videos and other formats)
3. File modification time (`st_mtime`) as fallback

**Deduplication**
- SHA-256 of the first 64 KB of each file (fast, catches identical files; intentionally not a full-file hash to keep speed high on large collections)
- Groups with more than one path = duplicate group
- Output: `duplicates-report.json` + per-directory `.txt` manifest files

### Web UI (`app.py`)

Built with [NiceGUI](https://nicegui.io/). Runs at `http://127.0.0.1:8080`. Three tabs mirror the three CLI steps.

- **Tab 1 — Scan**: textarea for directory paths, chip-picker for `/Volumes/*` drives, progress bar, log output, collapsible duplicate groups
- **Tab 2 — Move Dupes**: loads the JSON report, shows files that will move, requires an explicit "Confirm & Move" click
- **Tab 3 — Organize**: source + destination inputs, progress bar, per-file log

All long-running work (hashing, moving, organizing) runs in a `threading.Thread` and posts results back to the asyncio event loop via a `Queue`.

## NiceGUI 3.x Notes (important)

The venv runs NiceGUI 3.x (installed when project moved to `~/code/`). Several things changed from 2.x:

- **`ui.log.push()` is broken** — silently does nothing in NiceGUI 3.x. All three log areas use `ui.textarea` with a `_log_push(ta, text)` helper instead. Do not switch back to `ui.log`.
- **`ui.textarea` CSS** — `.style('color:...')` only targets the outer Quasar wrapper div, not the actual text input. Use `ui.add_css()` targeting `.q-field__native` and `.q-field__control`. The CSS hook class is `tru-log`.
- **`show=False` in `ui.run()`** — the AppleScript launcher opens the browser itself after polling port 8080. `show=True` opens a second window. Keep it `False`.

## .app Launcher Notes

- `make-app.sh` **must be re-run** any time the project moves to a new directory. It bakes the Python interpreter path and `app.py` path into the compiled AppleScript at build time. Dynamic path detection (`path to me`) proved unreliable — macOS resolved it to the old Google Drive location or `/Applications`.
- The launcher logs to `/tmp/tru-organizer.log` — check there first if the `.app` isn't working.
- **macOS TCC (privacy)** — the `.app` bundle cannot read Desktop, Documents, or Downloads without explicit user permission. Grant access in **System Settings → Privacy & Security → Files & Folders**. The terminal already has this permission. Real-world use (scanning `/Volumes/...` external drives) is unaffected.

## .app Launcher — Lesson Learned (July 2026)

The `.app` launcher approach fundamentally restricts what a Python app can do. macOS sandboxing strips away filesystem access that the terminal takes for granted — including reading into package-format directories like `.fcpbundle` on external drives, even with Full Disk Access granted. Every `.app` "fix" introduced a new breakage. **The canonical way to run this app is `launch.command` (double-click in Finder) or `python3 app.py` from the terminal — both run under Terminal.app's TCC identity.** The `.app` and `make-app.sh` are kept for reference but should not be trusted or developed further.

`launch.command` frees port 8080 if a previous instance is still running, starts `app.py`, polls until NiceGUI is ready, then opens the browser — the same job the `.app`'s AppleScript wrapper did, minus the TCC problem, since it's a plain script that Finder runs via Terminal.app rather than a separate compiled app bundle. `launch.sh` (older, no port-free/browser-open) is superseded by this but kept for reference.

## Current State (June 2026)

### Working
- Full CLI (`scan`, `move`, `organize` subcommands)
- NiceGUI web UI for all three steps
- Mounted-drive chip picker refreshes `/Volumes`
- Hashing worker threads with live progress in the UI
- Collision-safe file naming (`safe_name`) when dest already contains a file of the same name
- Unique manifest names built from directory path segments to avoid collisions across similarly-named folders
- Port reclamation on startup (`_free_port`) so re-launching doesn't fail
- `launch.command` — recommended launcher, runs under Terminal.app's TCC identity
- `.app` launcher with baked paths (rebuild with `bash make-app.sh` after any project move) — kept for reference only, not recommended (see "Lesson Learned")

### Known limitations / future work
- SHA-256 hashes only the **first 64 KB** — files with identical headers but different content would appear as duplicates (rare in practice for photos/videos, but worth noting)
- No undo / restore feature after `move` — files land in `duplicates-<YYYY-MM-DD>/` and must be moved back manually
- EXIF reading relies on Pillow `img._getexif()` (private API) — works on CPython but could break on future Pillow versions; migrate to `img.getexif()` when dropping Pillow < 6
- `ffprobe` must be installed separately (part of FFmpeg); the app silently falls back to mtime if it's missing
- No Windows/Linux support for the drive chip picker (macOS `/Volumes` only)
- No test suite yet
- `.app` cannot access Desktop/Documents/Downloads without TCC permission grant

## Development Setup

```bash
cd /path/to/TRU-MEDIA-ORGANIZER
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# optional, for video date extraction:
brew install ffmpeg

# CLI
python tru_organizer.py scan /Volumes/DriveA /Volumes/DriveB
python tru_organizer.py move --from /Volumes/DriveB
python tru_organizer.py organize /Volumes/DriveA

# Web UI — use launch.command (double-click in Finder, or run directly), not the .app
./launch.command
# or, equivalently:
python app.py   # opens http://localhost:8080 automatically
```

## File Outputs

| File | Created by | Description |
|------|-----------|-------------|
| `<dir>.txt` | `scan` | Manifest of all media filenames in that directory |
| `duplicates-report.json` | `scan` | JSON map of SHA-256 hash → list of file paths |
| `duplicates-<YYYY-MM-DD>/` | `move` | Folder containing moved duplicate copies |
| `PHOTO/<YYYY-MM-DD>/` | `organize` | Date-sorted photo tree |
| `VIDEO/<YYYY-MM-DD>/` | `organize` | Date-sorted video tree |
