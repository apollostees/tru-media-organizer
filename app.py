#!/usr/bin/env python3
"""
TRU-MEDIA-ORGANIZER  ·  Local web interface
Run:  python app.py
Then: http://localhost:8080
"""

import asyncio
import json
import os
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from nicegui import ui


def _free_port(port: int = 8080) -> None:
    """Kill any process already bound to `port` so we can reclaim it."""
    try:
        result = subprocess.run(
            ['lsof', '-ti', f':{port}'], capture_output=True, text=True
        )
        for pid in result.stdout.split():
            if pid.isdigit():
                os.kill(int(pid), 9)
    except Exception:
        pass


def _mounted_volumes() -> list[Path]:
    """Return directories currently mounted under /Volumes."""
    vols = Path('/Volumes')
    if not vols.exists():
        return []
    return sorted(
        d for d in vols.iterdir()
        if d.is_dir() and not d.name.startswith('.')
    )

from tru_organizer import (
    hash_file, scan_media, is_photo, is_video,
    get_media_date, safe_name, _unique_manifest_name, REPORT_FILE,
)

# ── Page ──────────────────────────────────────────────────────────────────────

@ui.page('/')
def index():

    # Per-session mutable state (fresh on each browser tab)
    scan_state = {'report_path': ''}
    move_state = {'to_move': [], 'dest_dir': None}

    # ── Header ────────────────────────────────────────────────────────────────
    with ui.header().classes('items-center gap-3 px-6 py-3').style('background:#1e3a5f'):
        ui.icon('photo_library', size='xl', color='white')
        ui.label('TRU MEDIA ORGANIZER').classes('text-white text-xl font-bold tracking-widest')
        ui.space()
        ui.label('local media deduplicator & organizer').classes('text-blue-200 text-sm italic')

    # ── Tabs ──────────────────────────────────────────────────────────────────
    with ui.tabs().classes('w-full').style('background:#1e3a5f; color:white') as tabs:
        t1 = ui.tab('scan',     label='① Scan',       icon='search')
        t2 = ui.tab('move',     label='② Move Dupes', icon='drive_file_move')
        t3 = ui.tab('organize', label='③ Organize',   icon='folder_special')

    with ui.tab_panels(tabs, value=t1).classes('w-full p-0 bg-gray-50'):

        # ── ① SCAN ────────────────────────────────────────────────────────────
        with ui.tab_panel(t1).classes('p-6'):
            with ui.card().classes('w-full max-w-3xl mx-auto shadow-md'):

                with ui.card_section():
                    ui.label('Step 1 — Scan for Duplicates').classes('text-lg font-bold')
                    ui.label(
                        'Scans directories, writes a manifest .txt per directory, '
                        'detects duplicate files by SHA-256 hash, and saves a JSON report.'
                    ).classes('text-sm text-gray-500 mt-1')

                ui.separator()

                with ui.card_section():
                    dirs_ta = ui.textarea(
                        label='Directories to scan — one path per line',
                        placeholder='/Volumes/DriveA/2025-11-10\n/Volumes/DriveB/2025-11-10',
                    ).classes('w-full font-mono text-sm').props('outlined rows=4 autogrow')

                    # Mounted-drive picker
                    with ui.row().classes('w-full items-center gap-2 flex-wrap mt-1'):
                        ui.label('Mounted drives:').classes('text-xs text-gray-400')
                        drives_chips = ui.row().classes('gap-1 flex-wrap')

                        def refresh_drives():
                            drives_chips.clear()
                            with drives_chips:
                                for vol in _mounted_volumes():
                                    ui.chip(
                                        vol.name,
                                        icon='storage',
                                        on_click=lambda v=str(vol): dirs_ta.set_value(
                                            (dirs_ta.value.rstrip('\n') + '\n' + v).lstrip('\n')
                                        ),
                                    ).props('outline color=primary dense clickable')

                        refresh_drives()
                        ui.button(icon='refresh', on_click=refresh_drives).props(
                            'flat round dense color=grey'
                        ).tooltip('Refresh drive list')

                    out_in = ui.input(
                        label='Output directory  (manifests & report)',
                        placeholder='Leave blank → first directory above',
                    ).classes('w-full font-mono text-sm mt-2').props('outlined')

                with ui.card_section():
                    with ui.row().classes('items-center gap-3 w-full'):
                        scan_btn = ui.button('▶  Scan', icon='search').props('color=primary unelevated')
                        scan_prog_label = ui.label('').classes('text-sm text-gray-400')
                    scan_prog = ui.linear_progress(value=0, show_value=False).classes('w-full')
                    scan_prog.visible = False

                with ui.card_section():
                    scan_log = ui.log(max_lines=2000).classes('w-full h-52 font-mono text-xs').style(
                        'background:#0f172a; color:#4ade80; border-radius:4px'
                    )

            dupe_panel = ui.column().classes('w-full max-w-3xl mx-auto mt-3')

        # ── ② MOVE ────────────────────────────────────────────────────────────
        with ui.tab_panel(t2).classes('p-6'):
            with ui.card().classes('w-full max-w-3xl mx-auto shadow-md'):

                with ui.card_section():
                    ui.label('Step 2 — Move Duplicates').classes('text-lg font-bold')
                    ui.label(
                        'Load the scan report, choose which directory to clean up, '
                        'then confirm before any files are moved.'
                    ).classes('text-sm text-gray-500 mt-1')

                ui.separator()

                with ui.card_section():
                    report_in = ui.input(
                        label='Path to duplicates-report.json',
                        placeholder='/path/to/duplicates-report.json',
                    ).classes('w-full font-mono text-sm').props('outlined')

                    ui.button(
                        'Load path from last scan', icon='history',
                        on_click=lambda: (
                            report_in.set_value(scan_state['report_path'])
                            if scan_state['report_path']
                            else ui.notify('No scan has been run yet.', type='warning')
                        ),
                    ).props('flat dense color=primary')

                    from_in = ui.input(
                        label='Move duplicates FROM this directory',
                        placeholder='/Volumes/DriveB/2025-11-10',
                    ).classes('w-full font-mono text-sm mt-2').props('outlined')

                with ui.card_section():
                    find_btn = ui.button('Find files to move', icon='manage_search').props('color=secondary unelevated')

                move_list_area   = ui.column().classes('w-full px-4 pb-2')
                confirm_btn_area = ui.row().classes('px-4 pb-2 gap-3')

                with ui.card_section():
                    move_log = ui.log(max_lines=500).classes('w-full h-40 font-mono text-xs').style(
                        'background:#0f172a; color:#4ade80; border-radius:4px'
                    )

        # ── ③ ORGANIZE ────────────────────────────────────────────────────────
        with ui.tab_panel(t3).classes('p-6'):
            with ui.card().classes('w-full max-w-3xl mx-auto shadow-md'):

                with ui.card_section():
                    ui.label('Step 3 — Organize into PHOTO / VIDEO').classes('text-lg font-bold')
                    ui.label(
                        'Moves files into PHOTO/<YYYY-MM-DD>/ and VIDEO/<YYYY-MM-DD>/ '
                        'using EXIF or ffprobe metadata dates. Skips duplicates-* folders. '
                        'Can be run independently of Steps 1–2.'
                    ).classes('text-sm text-gray-500 mt-1')

                ui.separator()

                with ui.card_section():
                    src_in = ui.input(
                        label='Source directory',
                        placeholder='/Volumes/DriveA',
                    ).classes('w-full font-mono text-sm').props('outlined')

                    dest_in = ui.input(
                        label='Create PHOTO/ and VIDEO/ inside  (blank = same as source)',
                        placeholder='/Volumes/Organized',
                    ).classes('w-full font-mono text-sm mt-2').props('outlined')

                with ui.card_section():
                    with ui.row().classes('items-center gap-3 w-full'):
                        org_btn = ui.button('▶  Organize', icon='folder_special').props('color=primary unelevated')
                        org_prog_label = ui.label('').classes('text-sm text-gray-400')
                    org_prog = ui.linear_progress(value=0, show_value=False).classes('w-full')
                    org_prog.visible = False

                with ui.card_section():
                    org_log = ui.log(max_lines=2000).classes('w-full h-52 font-mono text-xs').style(
                        'background:#0f172a; color:#4ade80; border-radius:4px'
                    )

    # ── SCAN handler ──────────────────────────────────────────────────────────
    async def do_scan():
        raw_dirs = [ln.strip() for ln in dirs_ta.value.strip().splitlines() if ln.strip()]
        if not raw_dirs:
            ui.notify('Enter at least one directory.', type='warning')
            return

        dirs = [Path(d).expanduser().resolve() for d in raw_dirs]
        for d in dirs:
            if not d.exists():
                ui.notify(f'Directory not found: {d}', type='negative')
                return

        raw_out = out_in.value.strip()
        out_dir = Path(raw_out).expanduser().resolve() if raw_out else dirs[0]
        out_dir.mkdir(parents=True, exist_ok=True)

        scan_btn.disable()
        scan_log.clear()
        dupe_panel.clear()
        scan_prog.visible = True
        scan_prog.set_value(0)
        scan_prog_label.set_text('')

        scan_log.push(f"Output: {out_dir}\n")

        # Scan directories + write manifests (fast, sync)
        all_files: list[tuple[Path, Path]] = []
        used_names: set[str] = set()

        for d in dirs:
            files = scan_media(d, skip_prefixes=('duplicates-',))
            stem = _unique_manifest_name(d, used_names)
            manifest_path = out_dir / f"{stem}.txt"
            with open(manifest_path, 'w') as mf:
                mf.write(f"{d}\n")
                for f in sorted(files, key=lambda p: p.name.lower()):
                    mf.write(f"{f.name}\n")
            scan_log.push(f"[{stem}]  {len(files)} file(s)  →  {manifest_path.name}")
            for f in files:
                all_files.append((f, d))

        total = len(all_files)
        scan_log.push(f"\nHashing {total} file(s)…")

        if total == 0:
            scan_log.push("No media files found.")
            scan_btn.enable()
            scan_prog.visible = False
            return

        # Hash in worker thread; stream progress back via asyncio queue
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        hash_map: dict[str, list[str]] = {}

        def _hash_worker():
            for i, (fp, _) in enumerate(all_files):
                try:
                    h = hash_file(fp)
                    loop.call_soon_threadsafe(q.put_nowait, ('ok', i + 1, fp.name, h, str(fp)))
                except Exception as exc:
                    loop.call_soon_threadsafe(q.put_nowait, ('err', fp.name, str(exc)))
            loop.call_soon_threadsafe(q.put_nowait, ('done',))

        threading.Thread(target=_hash_worker, daemon=True).start()

        while True:
            msg = await q.get()
            if msg[0] == 'done':
                break
            if msg[0] == 'ok':
                _, i, name, h, path = msg
                hash_map.setdefault(h, []).append(path)
                scan_prog.set_value(i / total)
                scan_prog_label.set_text(f"{i} / {total}")
            elif msg[0] == 'err':
                scan_log.push(f"  ⚠  {msg[1]}: {msg[2]}")

        scan_prog.set_value(1.0)
        scan_prog_label.set_text(f"{total} / {total}  ✓")

        dupes     = {h: ps for h, ps in hash_map.items() if len(ps) > 1}
        redundant = sum(len(v) - 1 for v in dupes.values())

        scan_log.push('')
        scan_log.push(
            f"{len(dupes)} duplicate group(s)  ·  {redundant} redundant file(s)"
            if dupes else "No duplicates found."
        )

        # Save JSON report
        report = {
            'scan_date': datetime.now().isoformat(),
            'scanned_directories': [str(d) for d in dirs],
            'duplicates': dupes,
        }
        rpath = out_dir / REPORT_FILE
        with open(rpath, 'w') as rf:
            json.dump(report, rf, indent=2)
        scan_log.push(f"Report: {rpath}")

        scan_state['report_path'] = str(rpath)

        # Render duplicate groups below the card
        if dupes:
            with dupe_panel:
                with ui.card().classes('w-full max-w-3xl mx-auto shadow-md'):
                    with ui.card_section():
                        ui.label(
                            f"{len(dupes)} Duplicate Group(s)  ·  {redundant} redundant file(s)"
                        ).classes('font-semibold')
                    ui.separator()
                    for i, (h, paths) in enumerate(dupes.items(), 1):
                        with ui.expansion(
                            f"Group {i}  ·  {len(paths)} copies  [{h[:10]}…]"
                        ).classes('w-full font-mono text-sm'):
                            for p in paths:
                                ui.label(p).classes('text-xs text-gray-500 pl-4 py-0.5')

        scan_btn.enable()
        ui.notify(
            f"Scan complete — {len(dupes)} group(s), {redundant} redundant file(s)."
            if dupes else "Scan complete — no duplicates found.",
            type='positive',
        )

    scan_btn.on_click(do_scan)

    # ── FIND FILES TO MOVE handler ────────────────────────────────────────────
    async def do_find():
        move_list_area.clear()
        confirm_btn_area.clear()
        move_log.clear()
        move_state['to_move'] = []
        move_state['dest_dir'] = None

        rpath_raw   = report_in.value.strip()
        from_raw    = from_in.value.strip()

        if not rpath_raw:
            ui.notify('Enter a report path.', type='warning')
            return
        if not from_raw:
            ui.notify('Enter the source directory.', type='warning')
            return

        rpath    = Path(rpath_raw).expanduser().resolve()
        from_dir = Path(from_raw).expanduser().resolve()

        if not rpath.exists():
            ui.notify(f'Report not found: {rpath}', type='negative')
            return
        if not from_dir.exists():
            ui.notify(f'Directory not found: {from_dir}', type='negative')
            return

        with open(rpath) as rf:
            report = json.load(rf)

        dupes: dict[str, list[str]] = report.get('duplicates', {})
        if not dupes:
            ui.notify('No duplicates in report.', type='info')
            return

        to_move: list[Path] = []
        for paths in dupes.values():
            objs    = [Path(p) for p in paths]
            in_src  = [p for p in objs if p.is_relative_to(from_dir)]
            out_src = [p for p in objs if not p.is_relative_to(from_dir)]
            if not in_src:
                continue
            to_move.extend(in_src if out_src else in_src[1:])

        if not to_move:
            ui.notify('No duplicates found in that directory.', type='warning')
            return

        today    = datetime.now().strftime('%Y-%m-%d')
        dest_dir = from_dir / f"duplicates-{today}"

        move_state['to_move']  = to_move
        move_state['dest_dir'] = dest_dir

        with move_list_area:
            ui.label(
                f"{len(to_move)} file(s) will move  →  {dest_dir.name}/"
            ).classes('font-semibold pt-2 pb-1')
            with ui.scroll_area().classes('w-full border rounded').style('max-height:200px'):
                for p in to_move:
                    ui.label(str(p)).classes('font-mono text-xs text-gray-500 px-2 py-0.5')

        with confirm_btn_area:
            ui.button(
                f'⚠  Confirm & Move {len(to_move)} File(s)',
                icon='drive_file_move',
                on_click=do_move,
            ).props('color=negative unelevated')

    async def do_move():
        to_move  = move_state['to_move']
        dest_dir = move_state['dest_dir']
        if not to_move or not dest_dir:
            return

        # Remove confirm button to prevent double-click
        confirm_btn_area.clear()
        dest_dir.mkdir(exist_ok=True)

        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _move_worker():
            for n, fp in enumerate(to_move, 1):
                dest = safe_name(dest_dir, fp.name)
                shutil.move(str(fp), str(dest))
                loop.call_soon_threadsafe(q.put_nowait, ('ok', n, fp.name))
            loop.call_soon_threadsafe(q.put_nowait, ('done', len(to_move)))

        threading.Thread(target=_move_worker, daemon=True).start()

        while True:
            msg = await q.get()
            if msg[0] == 'done':
                _, count = msg
                move_log.push(f"\nDone — {count} file(s) moved to {dest_dir}")
                ui.notify(f'{count} file(s) moved to {dest_dir.name}/', type='positive')
                break
            elif msg[0] == 'ok':
                _, n, name = msg
                move_log.push(f"  {n:>4}  {name}  →  {dest_dir.name}/")

    find_btn.on_click(do_find)

    # ── ORGANIZE handler ──────────────────────────────────────────────────────
    async def do_organize():
        src_raw  = src_in.value.strip()
        dest_raw = dest_in.value.strip()

        if not src_raw:
            ui.notify('Enter a source directory.', type='warning')
            return

        src = Path(src_raw).expanduser().resolve()
        out = Path(dest_raw).expanduser().resolve() if dest_raw else src

        if not src.exists():
            ui.notify(f'Not found: {src}', type='negative')
            return

        photo_root = out / 'PHOTO'
        video_root = out / 'VIDEO'
        photo_root.mkdir(parents=True, exist_ok=True)
        video_root.mkdir(parents=True, exist_ok=True)

        org_btn.disable()
        org_log.clear()
        org_prog.visible = True
        org_prog.set_value(0)
        org_prog_label.set_text('')

        org_log.push(f"Source : {src}")
        org_log.push(f"PHOTO/ → {photo_root}")
        org_log.push(f"VIDEO/ → {video_root}\n")

        files = scan_media(src, skip_prefixes=('duplicates-', 'PHOTO', 'VIDEO'))
        files = [f for f in files if photo_root not in f.parents and video_root not in f.parents]
        total = len(files)

        if total == 0:
            org_log.push('No media files found.')
            org_btn.enable()
            org_prog.visible = False
            return

        org_log.push(f"{total} file(s) to organize…\n")

        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _org_worker():
            photos = videos = errors = 0
            for i, f in enumerate(files):
                try:
                    d        = get_media_date(f)
                    date_str = d.strftime('%Y-%m-%d')
                    if is_photo(f):
                        sub = photo_root / date_str
                        photos += 1
                    else:
                        sub = video_root / date_str
                        videos += 1
                    sub.mkdir(exist_ok=True)
                    dest = safe_name(sub, f.name)
                    shutil.move(str(f), str(dest))
                    loop.call_soon_threadsafe(q.put_nowait, ('ok', i + 1, f.name, date_str))
                except Exception as exc:
                    errors += 1
                    loop.call_soon_threadsafe(q.put_nowait, ('err', f.name, str(exc)))
            loop.call_soon_threadsafe(q.put_nowait, ('done', photos, videos, errors))

        threading.Thread(target=_org_worker, daemon=True).start()

        while True:
            msg = await q.get()
            if msg[0] == 'done':
                _, photos, videos, errors = msg
                org_log.push('')
                org_log.push(f"Done.  Photos: {photos}  ·  Videos: {videos}  ·  Errors: {errors}")
                org_prog.set_value(1.0)
                org_prog_label.set_text(f"{total} / {total}  ✓")
                ui.notify(f'Organized {photos + videos} file(s).', type='positive')
                break
            elif msg[0] == 'ok':
                _, i, name, date_str = msg
                org_prog.set_value(i / total)
                org_prog_label.set_text(f"{i} / {total}")
                org_log.push(f"{date_str}  {name}")
            elif msg[0] == 'err':
                org_log.push(f"  ⚠  {msg[1]}: {msg[2]}")

        org_btn.enable()

    org_btn.on_click(do_organize)


# ── Launch ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    PORT = 8080
    _free_port(PORT)   # reclaim port if a previous instance is still running
    ui.run(
        host='127.0.0.1',
        port=PORT,
        title='TRU Media Organizer',
        favicon=str(Path(__file__).parent / 'disc.png'),
        dark=False,
        reload=False,
        show=True,
        reconnect_timeout=300,
    )
