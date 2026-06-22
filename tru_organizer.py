#!/usr/bin/env python3
"""
TRU-MEDIA-ORGANIZER

  scan      Step 1 — Scan directories, write manifests, detect duplicates
  move      Step 2 — Move duplicates to a dated folder after confirmation
  organize  Step 3 — Organize into PHOTO/<YYYY-MM-DD> and VIDEO/<YYYY-MM-DD>

Usage examples:
  python tru_organizer.py scan /Volumes/DriveA /Volumes/DriveB
  python tru_organizer.py move --from /Volumes/DriveB
  python tru_organizer.py organize /Volumes/DriveA
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, date
from pathlib import Path

try:
    from PIL import Image
    from PIL.ExifTags import TAGS
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ── File types ────────────────────────────────────────────────────────────────

PHOTO_EXT = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp',
    '.tiff', '.tif', '.heic', '.heif',
    '.raw', '.cr2', '.cr3', '.nef', '.nrw',
    '.arw', '.dng', '.orf', '.rw2', '.pef', '.srw', '.x3f',
}
VIDEO_EXT = {
    '.mp4', '.mov', '.avi', '.mkv', '.m4v',
    '.wmv', '.flv', '.webm', '.3gp', '.mts',
    '.m2ts', '.ts', '.mpg', '.mpeg', '.ogv', '.divx',
}
MEDIA_EXT = PHOTO_EXT | VIDEO_EXT

REPORT_FILE = 'duplicates-report.json'

# ── Utilities ─────────────────────────────────────────────────────────────────

def is_photo(p: Path) -> bool:
    return p.suffix.lower() in PHOTO_EXT

def is_video(p: Path) -> bool:
    return p.suffix.lower() in VIDEO_EXT

def is_media(p: Path) -> bool:
    return p.suffix.lower() in MEDIA_EXT

def hash_file(path: Path, chunk: int = 65536) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        h.update(f.read(chunk))
    return h.hexdigest()

def safe_name(dest_dir: Path, filename: str) -> Path:
    """Return a collision-free path inside dest_dir."""
    p = Path(filename)
    candidate = dest_dir / filename
    n = 1
    while candidate.exists():
        candidate = dest_dir / f"{p.stem}_{n}{p.suffix}"
        n += 1
    return candidate

def scan_media(directory: Path, skip_prefixes: tuple[str, ...] = ()) -> list[Path]:
    """Recursively collect media files, skipping dirs whose names start with skip_prefixes."""
    files = []
    for root, dirs, filenames in os.walk(directory):
        root_path = Path(root)
        # Prune dirs in-place to skip e.g. 'duplicates-' folders
        dirs[:] = [
            d for d in dirs
            if not any(d.startswith(pfx) for pfx in skip_prefixes)
        ]
        for name in filenames:
            fp = root_path / name
            if fp.is_file() and is_media(fp):
                files.append(fp)
    return sorted(files)

# ── Date extraction ───────────────────────────────────────────────────────────

_EXIF_DATE_TAGS = {'DateTimeOriginal', 'DateTimeDigitized', 'DateTime'}

def _exif_date(path: Path) -> date | None:
    if not HAS_PIL:
        return None
    if path.suffix.lower() not in {'.jpg', '.jpeg', '.tiff', '.tif'}:
        return None
    try:
        img = Image.open(path)
        exif = img._getexif()
        if not exif:
            return None
        for tag_id, val in exif.items():
            if TAGS.get(tag_id) in _EXIF_DATE_TAGS:
                return datetime.strptime(val[:19], '%Y:%m:%d %H:%M:%S').date()
    except Exception:
        pass
    return None

_FFPROBE_DATE_KEYS = ('creation_time', 'date', 'DATE', 'com.apple.quicktime.creationdate')
_FFPROBE_FMTS = (
    '%Y-%m-%dT%H:%M:%S.%f%z',
    '%Y-%m-%dT%H:%M:%S%z',
    '%Y-%m-%dT%H:%M:%SZ',
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d',
    '%Y%m%d',
)

def _ffprobe_date(path: Path) -> date | None:
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_format', '-show_streams', str(path)],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        candidates = []
        candidates += list(data.get('format', {}).get('tags', {}).items())
        for stream in data.get('streams', []):
            candidates += list(stream.get('tags', {}).items())
        for key, val in candidates:
            if key in _FFPROBE_DATE_KEYS:
                val = val.strip()
                for fmt in _FFPROBE_FMTS:
                    try:
                        return datetime.strptime(val[:len(fmt)], fmt).date()
                    except ValueError:
                        continue
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        pass
    return None

def get_media_date(path: Path) -> date:
    d = _exif_date(path) if is_photo(path) else _ffprobe_date(path)
    return d or datetime.fromtimestamp(path.stat().st_mtime).date()

# ── STEP 1: scan ─────────────────────────────────────────────────────────────

def _unique_manifest_name(directory: Path, used: set[str]) -> str:
    """
    Build a manifest stem from the directory path that is unique within `used`.
    Walks up the path tree prepending parent segments until the name is unique.
    e.g.  2025-11-10  →  DriveA-2025-11-10  →  Photos-DriveA-2025-11-10  …
    """
    parts = list(directory.parts)  # absolute path segments
    # Start with just the leaf name, keep prepending parent segments on collision
    for depth in range(1, len(parts) + 1):
        candidate = '-'.join(p for p in parts[-depth:] if p not in ('/', ''))
        # Sanitise characters that are awkward in filenames
        candidate = candidate.replace('/', '-').replace(':', '').strip('-')
        if candidate not in used:
            used.add(candidate)
            return candidate
    # Absolute fallback — shouldn't happen
    fallback = str(directory).replace('/', '-').strip('-')
    used.add(fallback)
    return fallback


def cmd_scan(args: argparse.Namespace) -> None:
    dirs = [Path(d).expanduser().resolve() for d in args.directories]
    out  = Path(args.output).expanduser().resolve() if args.output else Path.cwd()
    out.mkdir(parents=True, exist_ok=True)

    hr = '─' * 60
    print(f"\n{hr}")
    print("  STEP 1 — SCAN FOR DUPLICATES")
    print(hr)

    all_files: list[tuple[Path, Path]] = []   # (file, source_dir)
    used_names: set[str] = set()

    for d in dirs:
        if not d.exists():
            print(f"\n  [!] Not found: {d}")
            continue
        files = scan_media(d, skip_prefixes=('duplicates-',))

        manifest_stem = _unique_manifest_name(d, used_names)
        print(f"\n  [{manifest_stem}]  {len(files)} media file(s)")

        # Write manifest — full path on first line, then sorted filenames
        manifest_path = out / f"{manifest_stem}.txt"
        with open(manifest_path, 'w') as mf:
            mf.write(f"{d}\n")
            for f in sorted(files, key=lambda p: p.name.lower()):
                mf.write(f"{f.name}\n")
        print(f"  Manifest: {manifest_path}")

        for f in files:
            all_files.append((f, d))

    total = len(all_files)
    print(f"\n  Total: {total} media file(s) across {len(dirs)} director(y/ies)")

    if total == 0:
        print("\n  Nothing to hash. Done.")
        return

    print("\n  Hashing files (may take a while for large collections)…")
    hash_map: dict[str, list[str]] = {}
    errors = []
    for i, (fp, _) in enumerate(all_files):
        print(f"\r  {i+1:>{len(str(total))}}/{total}  {fp.name[:55]:<55}", end='', flush=True)
        try:
            h = hash_file(fp)
            hash_map.setdefault(h, []).append(str(fp))
        except (IOError, PermissionError) as e:
            errors.append((fp, str(e)))
    print()

    dupes = {h: paths for h, paths in hash_map.items() if len(paths) > 1}

    if errors:
        print(f"\n  Warnings — could not read {len(errors)} file(s):")
        for fp, err in errors:
            print(f"    {fp.name}: {err}")

    if not dupes:
        print("\n  No duplicates found.")
        # Still save an empty report so 'move' can reference it
    else:
        redundant = sum(len(v) - 1 for v in dupes.values())
        print(f"\n  Found {len(dupes)} duplicate group(s)  ({redundant} redundant file(s)):\n")
        for i, (h, paths) in enumerate(dupes.items(), 1):
            print(f"  Group {i}  [{h[:10]}…]")
            for p in paths:
                print(f"    {p}")
            print()

    report = {
        'scan_date': datetime.now().isoformat(),
        'scanned_directories': [str(d) for d in dirs],
        'duplicates': dupes,
    }
    report_path = out / REPORT_FILE
    with open(report_path, 'w') as rf:
        json.dump(report, rf, indent=2)
    print(f"  Report saved: {report_path}")

    if dupes:
        print()
        print("  To verify: diff the .txt manifest files, then run:")
        print(f"  python tru_organizer.py move --from <directory> --report {report_path}")


# ── STEP 2: move ──────────────────────────────────────────────────────────────

def cmd_move(args: argparse.Namespace) -> None:
    report_path = Path(args.report).expanduser().resolve()
    source_dir  = Path(args.from_dir).expanduser().resolve()

    hr = '─' * 60
    print(f"\n{hr}")
    print("  STEP 2 — MOVE DUPLICATES")
    print(hr)

    if not report_path.exists():
        print(f"\n  [!] Report not found: {report_path}")
        print("  Run 'scan' first.")
        sys.exit(1)

    with open(report_path) as rf:
        report = json.load(rf)

    dupes: dict[str, list[str]] = report.get('duplicates', {})
    if not dupes:
        print("\n  No duplicates in report. Nothing to move.")
        return

    to_move: list[Path] = []
    for h, paths in dupes.items():
        path_objs = [Path(p) for p in paths]
        in_src  = [p for p in path_objs if p.is_relative_to(source_dir)]
        out_src = [p for p in path_objs if not p.is_relative_to(source_dir)]

        if not in_src:
            continue  # this group has no files in the chosen dir

        if out_src:
            # Copies exist elsewhere — safe to move ALL copies from source_dir
            to_move.extend(in_src)
        else:
            # All copies are inside source_dir — keep first, move the rest
            to_move.extend(in_src[1:])

    if not to_move:
        print(f"\n  No duplicates found inside: {source_dir}")
        print("  (All duplicate pairs may reside outside this directory.)")
        return

    today = datetime.now().strftime('%Y-%m-%d')
    dest_dir = source_dir / f"duplicates-{today}"

    print(f"\n  Source : {source_dir}")
    print(f"  Dest   : {dest_dir}")
    print(f"  Files  : {len(to_move)}\n")
    for p in to_move:
        print(f"    {p}")

    print(f"\n  Move these {len(to_move)} file(s) to {dest_dir.name}?")
    confirm = input("  [y/N] ").strip().lower()
    if confirm != 'y':
        print("  Aborted — no files moved.")
        return

    dest_dir.mkdir(exist_ok=True)
    moved = 0
    for src in to_move:
        dest = safe_name(dest_dir, src.name)
        shutil.move(str(src), str(dest))
        print(f"  Moved: {src.name}  →  {dest_dir.name}/")
        moved += 1

    print(f"\n  Done. {moved} file(s) moved to: {dest_dir}")


# ── STEP 3: organize ──────────────────────────────────────────────────────────

def cmd_organize(args: argparse.Namespace) -> None:
    source_dir = Path(args.source).expanduser().resolve()

    hr = '─' * 60
    print(f"\n{hr}")
    print("  STEP 3 — ORGANIZE INTO PHOTO / VIDEO")
    print(hr)

    if not source_dir.exists():
        print(f"\n  [!] Not found: {source_dir}")
        sys.exit(1)

    print(f"\n  Source: {source_dir}")
    print(f"  Where should PHOTO/ and VIDEO/ directories be created?")
    print(f"  Press Enter to use the source directory.")
    raw = input("  Output directory: ").strip()
    out_dir = Path(raw).expanduser().resolve() if raw else source_dir

    photo_root = out_dir / 'PHOTO'
    video_root = out_dir / 'VIDEO'
    photo_root.mkdir(parents=True, exist_ok=True)
    video_root.mkdir(parents=True, exist_ok=True)

    print(f"\n  PHOTO/ → {photo_root}")
    print(f"  VIDEO/ → {video_root}")

    # Collect files, skipping duplicates-* folders, PHOTO/, VIDEO/
    files = scan_media(
        source_dir,
        skip_prefixes=('duplicates-', 'PHOTO', 'VIDEO'),
    )
    # Also skip anything already inside the output PHOTO/VIDEO trees
    files = [
        f for f in files
        if photo_root not in f.parents and video_root not in f.parents
    ]

    if not files:
        print("\n  No media files found to organize.")
        return

    print(f"\n  Organizing {len(files)} file(s)…\n")

    photo_count = video_count = skip_count = 0
    errors: list[tuple[Path, str]] = []

    for i, f in enumerate(files):
        label = f"{i+1}/{len(files)}  {f.name[:50]}"
        print(f"\r  {label:<60}", end='', flush=True)
        try:
            d = get_media_date(f)
            date_str = d.strftime('%Y-%m-%d')
            if is_photo(f):
                dest_sub = photo_root / date_str
                photo_count += 1
            else:
                dest_sub = video_root / date_str
                video_count += 1
            dest_sub.mkdir(exist_ok=True)
            dest = safe_name(dest_sub, f.name)
            shutil.move(str(f), str(dest))
        except Exception as e:
            errors.append((f, str(e)))
            skip_count += 1

    print()
    print(f"\n  Done.")
    print(f"  Photos  : {photo_count}")
    print(f"  Videos  : {video_count}")
    if errors:
        print(f"  Errors  : {len(errors)}")
        for fp, err in errors:
            print(f"    {fp.name}: {err}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog='tru_organizer',
        description='TRU-MEDIA-ORGANIZER — deduplicate and organize photo/video collections.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tru_organizer.py scan /Volumes/DriveA /Volumes/DriveB\n"
            "  python tru_organizer.py move --from /Volumes/DriveB\n"
            "  python tru_organizer.py organize /Volumes/DriveA\n"
        ),
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # scan
    p_scan = sub.add_parser('scan', help='Step 1: scan for duplicates and write manifests')
    p_scan.add_argument('directories', nargs='+', metavar='DIR',
                        help='One or more directories (or drives) to scan')
    p_scan.add_argument('--output', '-o', metavar='DIR',
                        help='Where to write manifest .txt files and report (default: current directory)')
    p_scan.set_defaults(func=cmd_scan)

    # move
    p_move = sub.add_parser('move', help='Step 2: move duplicates after human confirmation')
    p_move.add_argument('--from', dest='from_dir', required=True, metavar='DIR',
                        help='Directory whose duplicate copies should be moved out')
    p_move.add_argument('--report', default=REPORT_FILE, metavar='FILE',
                        help=f'Path to the JSON report from scan (default: {REPORT_FILE})')
    p_move.set_defaults(func=cmd_move)

    # organize
    p_org = sub.add_parser('organize', help='Step 3: organize into PHOTO/VIDEO by date')
    p_org.add_argument('source', metavar='DIR', help='Directory to organize')
    p_org.set_defaults(func=cmd_organize)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
