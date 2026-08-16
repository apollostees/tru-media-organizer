#!/usr/bin/env python3
"""
TRU-MEDIA-ORGANIZER — native PySide6 interface
Run via:  ./TMO-Qt.command   (double-click in Finder)
     or:  python3 app_qt.py

Alternative to the NiceGUI-based app.py — see CLAUDE.md for why both exist
(ported from the app_qt.py pattern used by AAX-CONVERT / AB-CLEANER).

Unlike those two projects, this UI does not shell out to a CLI subprocess —
app.py already calls tru_organizer's functions in-process and runs long
work in a background thread, so this port does the same via QThread
workers instead of spawning tru_organizer.py.
"""

import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tru_organizer import (
    REPORT_FILE,
    _unique_manifest_name,
    get_media_date,
    hash_file,
    is_photo,
    safe_name,
    scan_media,
)

# ── File logging ───────────────────────────────────────────────────────────────

LOG_PATH = Path(__file__).parent / "app_qt.log"
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("tru_organizer_qt")
log.info("=" * 60)
log.info("TRU-MEDIA-ORGANIZER (Qt) starting")


def _mounted_volumes() -> list[Path]:
    vols = Path("/Volumes")
    if not vols.exists():
        return []
    return sorted(d for d in vols.iterdir() if d.is_dir() and not d.name.startswith("."))


# ── Background workers ────────────────────────────────────────────────────────


class ScanWorker(QThread):
    log_line = Signal(str)
    progress = Signal(int, int)
    finished_scan = Signal(dict, int, str)
    failed = Signal(str)

    def __init__(self, dirs: list[Path], out_dir: Path):
        super().__init__()
        self.dirs = dirs
        self.out_dir = out_dir

    def run(self) -> None:
        try:
            all_files: list[Path] = []
            used_names: set[str] = set()

            for d in self.dirs:
                files = scan_media(d, skip_prefixes=("duplicates-",))
                stem = _unique_manifest_name(d, used_names)
                manifest_path = self.out_dir / f"{stem}.txt"
                with open(manifest_path, "w") as mf:
                    mf.write(f"{d}\n")
                    for f in sorted(files, key=lambda p: p.name.lower()):
                        mf.write(f"{f.name}\n")
                self.log_line.emit(f"[{stem}]  {len(files)} file(s)  →  {manifest_path.name}")
                all_files.extend(files)

            total = len(all_files)
            self.log_line.emit(f"\nHashing {total} file(s)…")
            if total == 0:
                self.log_line.emit("No media files found.")
                self.finished_scan.emit({}, 0, "")
                return

            hash_map: dict[str, list[str]] = {}
            for i, fp in enumerate(all_files, 1):
                try:
                    h = hash_file(fp)
                    hash_map.setdefault(h, []).append(str(fp))
                except Exception as exc:
                    self.log_line.emit(f"  ⚠  {fp.name}: {exc}")
                self.progress.emit(i, total)

            dupes = {h: ps for h, ps in hash_map.items() if len(ps) > 1}
            redundant = sum(len(v) - 1 for v in dupes.values())

            report = {
                "scan_date": datetime.now().isoformat(),
                "scanned_directories": [str(d) for d in self.dirs],
                "duplicates": dupes,
            }
            rpath = self.out_dir / REPORT_FILE
            with open(rpath, "w") as rf:
                json.dump(report, rf, indent=2)

            self.finished_scan.emit(dupes, redundant, str(rpath))
        except Exception as exc:
            log.exception("Scan failed")
            self.failed.emit(str(exc))


class MoveWorker(QThread):
    log_line = Signal(str)
    finished_move = Signal(int, str)
    failed = Signal(str)

    def __init__(self, to_move: list[Path], dest_dir: Path):
        super().__init__()
        self.to_move = to_move
        self.dest_dir = dest_dir

    def run(self) -> None:
        try:
            self.dest_dir.mkdir(exist_ok=True)
            for n, fp in enumerate(self.to_move, 1):
                dest = safe_name(self.dest_dir, fp.name)
                shutil.move(str(fp), str(dest))
                self.log_line.emit(f"  {n:>4}  {fp.name}  →  {self.dest_dir.name}/")
            self.finished_move.emit(len(self.to_move), str(self.dest_dir))
        except Exception as exc:
            log.exception("Move failed")
            self.failed.emit(str(exc))


class OrganizeWorker(QThread):
    log_line = Signal(str)
    progress = Signal(int, int)
    finished_organize = Signal(int, int, int)
    failed = Signal(str)

    def __init__(self, files: list[Path], photo_root: Path, video_root: Path):
        super().__init__()
        self.files = files
        self.photo_root = photo_root
        self.video_root = video_root

    def run(self) -> None:
        try:
            total = len(self.files)
            photos = videos = errors = 0
            for i, f in enumerate(self.files, 1):
                try:
                    d = get_media_date(f)
                    date_str = d.strftime("%Y-%m-%d")
                    if is_photo(f):
                        sub = self.photo_root / date_str
                        photos += 1
                    else:
                        sub = self.video_root / date_str
                        videos += 1
                    sub.mkdir(exist_ok=True)
                    dest = safe_name(sub, f.name)
                    shutil.move(str(f), str(dest))
                    self.log_line.emit(f"{date_str}  {f.name}")
                except Exception as exc:
                    errors += 1
                    self.log_line.emit(f"  ⚠  {f.name}: {exc}")
                self.progress.emit(i, total)
            self.finished_organize.emit(photos, videos, errors)
        except Exception as exc:
            log.exception("Organize failed")
            self.failed.emit(str(exc))


# ── Shared state (report handoff between Scan and Move tabs) ───────────────────


class AppState:
    def __init__(self):
        self.last_report_path: str = ""


# ── Scan tab ───────────────────────────────────────────────────────────────────


class ScanTab(QWidget):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self._worker: ScanWorker | None = None

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Directories to scan — one path per line"))
        self.dirs_edit = QPlainTextEdit()
        self.dirs_edit.setPlaceholderText("/Volumes/DriveA/2025-11-10\n/Volumes/DriveB/2025-11-10")
        self.dirs_edit.setFixedHeight(90)
        layout.addWidget(self.dirs_edit)

        pick_row = QHBoxLayout()
        add_folder_btn = QPushButton("Add Folder…")
        add_folder_btn.clicked.connect(self._add_folder)
        pick_row.addWidget(add_folder_btn)
        layout.addLayout(pick_row)

        drives_row = QHBoxLayout()
        drives_row.addWidget(QLabel("Mounted drives:"))
        self.drives_row = QHBoxLayout()
        drives_row.addLayout(self.drives_row)
        refresh_btn = QPushButton("↻")
        refresh_btn.setFixedWidth(28)
        refresh_btn.clicked.connect(self._refresh_drives)
        drives_row.addWidget(refresh_btn)
        drives_row.addStretch(1)
        layout.addLayout(drives_row)
        self._refresh_drives()

        outdir_row = QHBoxLayout()
        self.outdir_edit = QLineEdit()
        self.outdir_edit.setPlaceholderText("Leave blank → first directory above")
        outdir_browse_btn = QPushButton("Browse…")
        outdir_browse_btn.clicked.connect(self._pick_outdir)
        outdir_row.addWidget(QLabel("Output directory"))
        outdir_row.addWidget(self.outdir_edit, stretch=1)
        outdir_row.addWidget(outdir_browse_btn)
        layout.addLayout(outdir_row)

        action_row = QHBoxLayout()
        self.scan_btn = QPushButton("▶  Scan")
        self.scan_btn.clicked.connect(self._start_scan)
        action_row.addWidget(self.scan_btn)
        self.progress_label = QLabel("")
        action_row.addWidget(self.progress_label)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setFixedHeight(150)
        layout.addWidget(self.log_edit)

        layout.addWidget(QLabel("Duplicate groups"))
        self.dupe_tree = QTreeWidget()
        self.dupe_tree.setHeaderHidden(True)
        layout.addWidget(self.dupe_tree, stretch=1)

    def _refresh_drives(self) -> None:
        while self.drives_row.count():
            item = self.drives_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for vol in _mounted_volumes():
            btn = QPushButton(vol.name)
            btn.clicked.connect(lambda checked=False, v=str(vol): self._append_dir(v))
            self.drives_row.addWidget(btn)

    def _append_dir(self, path: str) -> None:
        current = self.dirs_edit.toPlainText().rstrip("\n")
        self.dirs_edit.setPlainText(f"{current}\n{path}".lstrip("\n"))

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select directory to scan")
        if path:
            self._append_dir(path)

    def _pick_outdir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select output directory")
        if path:
            self.outdir_edit.setText(path)

    def _append_log(self, text: str) -> None:
        self.log_edit.moveCursor(self.log_edit.textCursor().MoveOperation.End)
        self.log_edit.insertPlainText(text + "\n")
        self.log_edit.moveCursor(self.log_edit.textCursor().MoveOperation.End)

    def _start_scan(self) -> None:
        if self._worker is not None:
            return

        raw_dirs = [ln.strip() for ln in self.dirs_edit.toPlainText().splitlines() if ln.strip()]
        if not raw_dirs:
            self._append_log("Enter at least one directory.")
            return

        dirs = [Path(d).expanduser().resolve() for d in raw_dirs]
        missing = [d for d in dirs if not d.exists()]
        if missing:
            self._append_log("ERROR: Directory not found:\n  " + "\n  ".join(str(d) for d in missing))
            return

        raw_out = self.outdir_edit.text().strip()
        out_dir = Path(raw_out).expanduser().resolve() if raw_out else dirs[0]
        out_dir.mkdir(parents=True, exist_ok=True)

        self.scan_btn.setEnabled(False)
        self.dupe_tree.clear()
        self.log_edit.clear()
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("")
        self._append_log(f"Output: {out_dir}")

        log.info(f"Scan started: {len(dirs)} dir(s)  out_dir={out_dir}")

        self._worker = ScanWorker(dirs, out_dir)
        self._worker.log_line.connect(self._append_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_scan.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, i: int, total: int) -> None:
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(i)
        self.progress_label.setText(f"{i} / {total}")

    def _on_finished(self, dupes: dict, redundant: int, report_path: str) -> None:
        if report_path:
            self.progress_label.setText(f"{self.progress_bar.maximum()} / {self.progress_bar.maximum()}  ✓")
            self._append_log("")
            self._append_log(
                f"{len(dupes)} duplicate group(s)  ·  {redundant} redundant file(s)"
                if dupes else "No duplicates found."
            )
            self._append_log(f"Report: {report_path}")
            self.state.last_report_path = report_path

        for i, (h, paths) in enumerate(dupes.items(), 1):
            group = QTreeWidgetItem([f"Group {i}  ·  {len(paths)} copies  [{h[:10]}…]"])
            for p in paths:
                group.addChild(QTreeWidgetItem([p]))
            self.dupe_tree.addTopLevelItem(group)

        self.scan_btn.setEnabled(True)
        self._worker.wait()
        self._worker = None

    def _on_failed(self, message: str) -> None:
        log.error(f"Scan failed: {message}")
        self._append_log(f"✗ Scan failed: {message}")
        self.scan_btn.setEnabled(True)
        self._worker.wait()
        self._worker = None

    def terminate_worker(self) -> None:
        if self._worker is not None:
            self._worker.terminate()
            self._worker.wait()


# ── Move tab ───────────────────────────────────────────────────────────────────


class MoveTab(QWidget):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self._worker: MoveWorker | None = None
        self._to_move: list[Path] = []
        self._dest_dir: Path | None = None

        layout = QVBoxLayout(self)

        report_row = QHBoxLayout()
        self.report_edit = QLineEdit()
        self.report_edit.setPlaceholderText("/path/to/duplicates-report.json")
        report_browse_btn = QPushButton("Browse…")
        report_browse_btn.clicked.connect(self._pick_report)
        report_row.addWidget(QLabel("Report"))
        report_row.addWidget(self.report_edit, stretch=1)
        report_row.addWidget(report_browse_btn)
        layout.addLayout(report_row)

        load_last_btn = QPushButton("Load path from last scan")
        load_last_btn.clicked.connect(self._load_last)
        layout.addWidget(load_last_btn)

        from_row = QHBoxLayout()
        self.from_edit = QLineEdit()
        self.from_edit.setPlaceholderText("/Volumes/DriveB/2025-11-10")
        from_browse_btn = QPushButton("Browse…")
        from_browse_btn.clicked.connect(self._pick_from_dir)
        from_row.addWidget(QLabel("Move duplicates FROM"))
        from_row.addWidget(self.from_edit, stretch=1)
        from_row.addWidget(from_browse_btn)
        layout.addLayout(from_row)

        self.find_btn = QPushButton("Find files to move")
        self.find_btn.clicked.connect(self._find)
        layout.addWidget(self.find_btn)

        self.move_summary = QLabel("")
        layout.addWidget(self.move_summary)
        self.move_list = QListWidget()
        layout.addWidget(self.move_list, stretch=1)

        self.confirm_btn = QPushButton("")
        self.confirm_btn.setVisible(False)
        self.confirm_btn.clicked.connect(self._start_move)
        layout.addWidget(self.confirm_btn)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setFixedHeight(150)
        layout.addWidget(self.log_edit)

    def _append_log(self, text: str) -> None:
        self.log_edit.moveCursor(self.log_edit.textCursor().MoveOperation.End)
        self.log_edit.insertPlainText(text + "\n")
        self.log_edit.moveCursor(self.log_edit.textCursor().MoveOperation.End)

    def _pick_report(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select report", filter="JSON Files (*.json)")
        if path:
            self.report_edit.setText(path)

    def _pick_from_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select directory to move duplicates from")
        if path:
            self.from_edit.setText(path)

    def _load_last(self) -> None:
        if self.state.last_report_path:
            self.report_edit.setText(self.state.last_report_path)
        else:
            self._append_log("No scan has been run yet.")

    def _find(self) -> None:
        self.move_list.clear()
        self.confirm_btn.setVisible(False)
        self._to_move = []
        self._dest_dir = None

        rpath_raw = self.report_edit.text().strip()
        from_raw = self.from_edit.text().strip()
        if not rpath_raw:
            self._append_log("Enter a report path.")
            return
        if not from_raw:
            self._append_log("Enter the source directory.")
            return

        rpath = Path(rpath_raw).expanduser().resolve()
        from_dir = Path(from_raw).expanduser().resolve()
        if not rpath.exists():
            self._append_log(f"ERROR: Report not found: {rpath}")
            return
        if not from_dir.exists():
            self._append_log(f"ERROR: Directory not found: {from_dir}")
            return

        with open(rpath) as rf:
            report = json.load(rf)
        dupes: dict[str, list[str]] = report.get("duplicates", {})
        if not dupes:
            self._append_log("No duplicates in report.")
            return

        to_move: list[Path] = []
        for paths in dupes.values():
            objs = [Path(p) for p in paths]
            in_src = [p for p in objs if p.is_relative_to(from_dir)]
            out_src = [p for p in objs if not p.is_relative_to(from_dir)]
            if not in_src:
                continue
            to_move.extend(in_src if out_src else in_src[1:])

        if not to_move:
            self._append_log("No duplicates found in that directory.")
            return

        today = datetime.now().strftime("%Y-%m-%d")
        dest_dir = from_dir / f"duplicates-{today}"

        self._to_move = to_move
        self._dest_dir = dest_dir

        self.move_summary.setText(f"{len(to_move)} file(s) will move  →  {dest_dir.name}/")
        self.move_list.addItems(str(p) for p in to_move)

        self.confirm_btn.setText(f"⚠  Confirm & Move {len(to_move)} File(s)")
        self.confirm_btn.setVisible(True)

    def _start_move(self) -> None:
        if self._worker is not None or not self._to_move or not self._dest_dir:
            return

        self.confirm_btn.setVisible(False)
        self.find_btn.setEnabled(False)
        log.info(f"Move started: {len(self._to_move)} file(s)  dest={self._dest_dir}")

        self._worker = MoveWorker(self._to_move, self._dest_dir)
        self._worker.log_line.connect(self._append_log)
        self._worker.finished_move.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_finished(self, count: int, dest_dir: str) -> None:
        self._append_log(f"\nDone — {count} file(s) moved to {dest_dir}")
        self.find_btn.setEnabled(True)
        self._worker.wait()
        self._worker = None

    def _on_failed(self, message: str) -> None:
        log.error(f"Move failed: {message}")
        self._append_log(f"✗ Move failed: {message}")
        self.find_btn.setEnabled(True)
        self._worker.wait()
        self._worker = None

    def terminate_worker(self) -> None:
        if self._worker is not None:
            self._worker.terminate()
            self._worker.wait()


# ── Organize tab ───────────────────────────────────────────────────────────────


class OrganizeTab(QWidget):
    def __init__(self):
        super().__init__()
        self._worker: OrganizeWorker | None = None

        layout = QVBoxLayout(self)

        src_row = QHBoxLayout()
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("/Volumes/DriveA")
        src_browse_btn = QPushButton("Browse…")
        src_browse_btn.clicked.connect(self._pick_src)
        src_row.addWidget(QLabel("Source directory"))
        src_row.addWidget(self.src_edit, stretch=1)
        src_row.addWidget(src_browse_btn)
        layout.addLayout(src_row)

        dest_row = QHBoxLayout()
        self.dest_edit = QLineEdit()
        self.dest_edit.setPlaceholderText("Blank = same as source")
        dest_browse_btn = QPushButton("Browse…")
        dest_browse_btn.clicked.connect(self._pick_dest)
        dest_row.addWidget(QLabel("Create PHOTO/ and VIDEO/ inside"))
        dest_row.addWidget(self.dest_edit, stretch=1)
        dest_row.addWidget(dest_browse_btn)
        layout.addLayout(dest_row)

        action_row = QHBoxLayout()
        self.organize_btn = QPushButton("▶  Organize")
        self.organize_btn.clicked.connect(self._start)
        action_row.addWidget(self.organize_btn)
        self.progress_label = QLabel("")
        action_row.addWidget(self.progress_label)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        layout.addWidget(self.log_edit, stretch=1)

    def _append_log(self, text: str) -> None:
        self.log_edit.moveCursor(self.log_edit.textCursor().MoveOperation.End)
        self.log_edit.insertPlainText(text + "\n")
        self.log_edit.moveCursor(self.log_edit.textCursor().MoveOperation.End)

    def _pick_src(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select source directory")
        if path:
            self.src_edit.setText(path)

    def _pick_dest(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select destination directory")
        if path:
            self.dest_edit.setText(path)

    def _start(self) -> None:
        if self._worker is not None:
            return

        src_raw = self.src_edit.text().strip()
        if not src_raw:
            self._append_log("Enter a source directory.")
            return

        src = Path(src_raw).expanduser().resolve()
        dest_raw = self.dest_edit.text().strip()
        out = Path(dest_raw).expanduser().resolve() if dest_raw else src
        if not src.exists():
            self._append_log(f"ERROR: Not found: {src}")
            return

        photo_root = out / "PHOTO"
        video_root = out / "VIDEO"
        photo_root.mkdir(parents=True, exist_ok=True)
        video_root.mkdir(parents=True, exist_ok=True)

        files = scan_media(src, skip_prefixes=("duplicates-", "PHOTO", "VIDEO"))
        files = [f for f in files if photo_root not in f.parents and video_root not in f.parents]
        total = len(files)

        self.log_edit.clear()
        self._append_log(f"Source : {src}")
        self._append_log(f"PHOTO/ → {photo_root}")
        self._append_log(f"VIDEO/ → {video_root}\n")

        if total == 0:
            self._append_log("No media files found.")
            return

        self._append_log(f"{total} file(s) to organize…\n")
        self.organize_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(0)
        self.progress_label.setText("")

        log.info(f"Organize started: {total} file(s)  src={src}  dest={out}")

        self._worker = OrganizeWorker(files, photo_root, video_root)
        self._worker.log_line.connect(self._append_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_organize.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, i: int, total: int) -> None:
        self.progress_bar.setValue(i)
        self.progress_label.setText(f"{i} / {total}")

    def _on_finished(self, photos: int, videos: int, errors: int) -> None:
        self._append_log("")
        self._append_log(f"Done.  Photos: {photos}  ·  Videos: {videos}  ·  Errors: {errors}")
        self.progress_label.setText(f"{self.progress_bar.maximum()} / {self.progress_bar.maximum()}  ✓")
        self.organize_btn.setEnabled(True)
        self._worker.wait()
        self._worker = None

    def _on_failed(self, message: str) -> None:
        log.error(f"Organize failed: {message}")
        self._append_log(f"✗ Organize failed: {message}")
        self.organize_btn.setEnabled(True)
        self._worker.wait()
        self._worker = None

    def terminate_worker(self) -> None:
        if self._worker is not None:
            self._worker.terminate()
            self._worker.wait()


# ── Main window ────────────────────────────────────────────────────────────────


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TRU Media Organizer")
        self.resize(760, 640)

        state = AppState()
        self.scan_tab = ScanTab(state)
        self.move_tab = MoveTab(state)
        self.organize_tab = OrganizeTab()

        tabs = QTabWidget()
        tabs.addTab(self.scan_tab, "① Scan")
        tabs.addTab(self.move_tab, "② Move Dupes")
        tabs.addTab(self.organize_tab, "③ Organize")

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)

    def closeEvent(self, event) -> None:
        self.scan_tab.terminate_worker()
        self.move_tab.terminate_worker()
        self.organize_tab.terminate_worker()
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
