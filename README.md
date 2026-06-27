# TRU Media Organizer

A local tool for cleaning up photo and video collections. It finds duplicate files across multiple drives, lets you review and confirm before anything moves, then sorts everything into a tidy date-based folder structure.

Runs as a web app in your browser — no cloud, no account, no files leave your machine.

---

## How it works

Three steps, in order:

**① Scan** — point it at one or more directories or drives. It hashes every media file and groups the duplicates. Writes a report you can review before anything is touched.

**② Move Dupes** — load the scan report, choose which drive to clean up, and confirm. Duplicates move into a `duplicates-<date>/` folder rather than being deleted outright.

**③ Organize** — moves files into `PHOTO/YYYY-MM-DD/` and `VIDEO/YYYY-MM-DD/` folders using the date embedded in the file's metadata (EXIF for photos, ffprobe for video). Falls back to the file's modification date if no metadata is found.

Each step is independent. You can run Organize on a drive that's already clean without running Scan first.

---

## Setup

**Requirements:** Python 3.11+, macOS (the drive picker uses `/Volumes`)

**Optional but recommended:** [FFmpeg](https://ffmpeg.org/) for accurate video dates
```bash
brew install ffmpeg
```

**Install:**
```bash
git clone https://github.com/apollostees/tru-media-organizer.git ~/code/tru-media-organizer
cd ~/code/tru-media-organizer
bash make-app.sh
```

`make-app.sh` creates the `.venv`, installs dependencies, and builds `TRU Media Organizer.app`. Double-click the app to launch.

---

## Launch options

**App (recommended):** Double-click `TRU Media Organizer.app` — starts the server and opens `http://localhost:8080` in your browser automatically.

**Terminal:**
```bash
source .venv/bin/activate
python app.py
```

**CLI** (no web UI):
```bash
source .venv/bin/activate
python tru_organizer.py scan /Volumes/DriveA /Volumes/DriveB
python tru_organizer.py move --from /Volumes/DriveB
python tru_organizer.py organize /Volumes/DriveA
```

---

## Supported formats

| Type | Extensions |
|------|-----------|
| Photos | `.jpg .jpeg .png .gif .bmp .tiff .tif .heic .heif` |
| RAW | `.cr2 .cr3 .nef .nrw .arw .dng .orf .rw2 .pef .srw .x3f` |
| Video | `.mp4 .mov .avi .mkv .m4v .wmv .flv .webm .3gp .mts .m2ts .ts .mpg .mpeg .ogv .divx` |

---

## Output files

| File | Created by | What it is |
|------|-----------|------------|
| `<dirname>.txt` | Scan | Manifest of every media filename found in that directory |
| `duplicates-report.json` | Scan | SHA-256 hash → file paths map for all duplicate groups |
| `duplicates-<YYYY-MM-DD>/` | Move | Holding folder for moved duplicates (not deleted) |
| `PHOTO/<YYYY-MM-DD>/` | Organize | Date-sorted photos |
| `VIDEO/<YYYY-MM-DD>/` | Organize | Date-sorted videos |

---

## After editing the code

Re-run `make-app.sh` to sync changes into the app bundle:
```bash
bash make-app.sh
```
