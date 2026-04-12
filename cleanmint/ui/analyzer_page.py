"""
ui/analyzer_page.py — Large File & Duplicate Analyzer UI
"""

import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QComboBox, QTabWidget, QScrollArea,
    QSizePolicy, QAbstractItemView, QMessageBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor

from ui.theme import Theme
from core.analyzer import Analyzer, _human_size

# Locations where a duplicate copy is clearly safe to delete.
# Downloads is intentionally excluded — it may contain files the user
# deliberately saved there and would not expect to be auto-deleted.
_SAFE_ZONES = [
    Path.home() / ".cache",
    Path("/tmp"),
    Path("/var/tmp"),
]


def _safe_copies_to_delete(group) -> list[Path]:
    """
    For a duplicate group, return the copies that are safe to auto-delete.
    Safe = living inside Downloads, .cache, or tmp.
    We only delete safe copies when at least one copy lives OUTSIDE a safe zone
    (so the user always has the file somewhere meaningful after deletion).
    """
    files = [Path(p) for p in group.files]

    def in_safe_zone(p: Path) -> bool:
        return any(str(p).startswith(str(z)) for z in _SAFE_ZONES)

    safe   = [f for f in files if     in_safe_zone(f)]
    unsafe = [f for f in files if not in_safe_zone(f)]

    # Only delete the safe copies when a non-safe copy will remain
    if safe and unsafe:
        return safe
    return []


class NumericTableWidgetItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by numeric UserRole data, not display text.
    Fixes size columns showing '9.6 GB' sorted before '974.6 MB' (string sort bug)."""
    def __lt__(self, other: QTableWidgetItem) -> bool:
        try:
            return (self.data(Qt.ItemDataRole.UserRole) or 0) < (other.data(Qt.ItemDataRole.UserRole) or 0)
        except TypeError:
            return super().__lt__(other)


class AnalyzerWorker(QThread):
    progress = pyqtSignal(str, int)
    files_done   = pyqtSignal(list)
    folders_done = pyqtSignal(list)
    dupes_done   = pyqtSignal(list)
    broken_done  = pyqtSignal(list)
    error        = pyqtSignal(str)

    def __init__(self, scan_root: Path, dupe_method: str):
        super().__init__()
        self._root   = scan_root
        self._method = dupe_method

    def run(self):
        try:
            a = Analyzer(progress_callback=lambda m, p: self.progress.emit(m, p),
                         scan_root=self._root)
            self.progress.emit("Scanning large files…", 5)
            self.files_done.emit(a.largest_files(top_n=100, min_size_mb=1.0))
            self.progress.emit("Scanning folders…", 40)
            self.folders_done.emit(a.largest_folders(top_n=30))
            self.progress.emit("Finding duplicates…", 60)
            self.dupes_done.emit(a.find_duplicates(method=self._method))
            self.progress.emit("Scanning broken symlinks…", 88)
            self.broken_done.emit(a.broken_symlinks())
            self.progress.emit("Done.", 100)
        except Exception as e:
            self.error.emit(str(e))


class AnalyzerPage(QWidget):
    def __init__(self):
        super().__init__()
        self._worker = None
        self._broken_paths: list[Path] = []
        self._dupe_groups = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("Analyzer")
        title.setObjectName("TitleLabel")
        hdr.addWidget(title)
        hdr.addStretch()

        hdr.addWidget(QLabel("Duplicate method:"))
        self._dupe_combo = QComboBox()
        self._dupe_combo.addItems(["Hash (accurate)", "Name + Size (fast)"])
        hdr.addWidget(self._dupe_combo)

        self._scan_btn = QPushButton("↺  Scan Home")
        self._scan_btn.setObjectName("PrimaryBtn")
        self._scan_btn.setFixedHeight(34)
        self._scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scan_btn.clicked.connect(self._start_scan)
        hdr.addWidget(self._scan_btn)
        layout.addLayout(hdr)

        self._status = QLabel("Click 'Scan Home' to analyze your home directory.")
        self._status.setObjectName("MutedLabel")
        layout.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setFixedHeight(6)
        self._progress.hide()
        layout.addWidget(self._progress)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(f"""
            QTabBar::tab {{
                background: {Theme.p().bg_secondary};
                color: {Theme.p().text_secondary};
                padding: 8px 18px;
                border: none;
                border-bottom: 2px solid transparent;
                font-size: 13px;
            }}
            QTabBar::tab:selected {{
                color: {Theme.p().accent};
                border-bottom: 2px solid {Theme.p().accent};
                background: {Theme.p().bg_primary};
            }}
            QTabWidget::pane {{ border: none; }}
        """)

        self._files_tab   = self._make_files_tab()
        self._folders_tab = self._make_folders_tab()
        self._dupes_tab   = self._make_dupes_tab()
        self._broken_tab  = self._make_broken_tab()

        self._tabs.addTab(self._files_tab,   "Large Files")
        self._tabs.addTab(self._folders_tab, "Large Folders")
        self._tabs.addTab(self._dupes_tab,   "Duplicates")
        self._tabs.addTab(self._broken_tab,  "Broken Symlinks")
        layout.addWidget(self._tabs, 1)

    # ── Tab builders ──────────────────────────────────────────

    def _make_files_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        self._files_table = self._make_table(
            ["File", "Size", "Type", "Modified", "Location"]
        )
        self._files_table.cellDoubleClicked.connect(self._open_file_location)
        lay.addWidget(self._files_table)
        return w

    def _make_folders_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        self._folders_table = self._make_table(["Folder", "Size", "Files", "Path"])
        lay.addWidget(self._folders_table)
        return w

    def _make_dupes_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        top_row = QHBoxLayout()
        self._dupes_label = QLabel("")
        self._dupes_label.setObjectName("AccentLabel")
        top_row.addWidget(self._dupes_label)
        top_row.addStretch()
        self._delete_dupes_btn = QPushButton("Delete Copies")
        self._delete_dupes_btn.setObjectName("DangerBtn")
        self._delete_dupes_btn.setFixedHeight(30)
        self._delete_dupes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_dupes_btn.setEnabled(False)
        self._delete_dupes_btn.setToolTip(
            "Keeps one copy of each duplicate group (the first by path). Deletes all other copies."
        )
        self._delete_dupes_btn.clicked.connect(self._delete_duplicate_copies)
        top_row.addWidget(self._delete_dupes_btn)
        lay.addLayout(top_row)

        self._dupes_table = self._make_table(["File", "Size", "Copies", "Wasted", "Paths"])
        lay.addWidget(self._dupes_table)
        return w

    def _make_broken_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        top_row = QHBoxLayout()
        self._broken_label = QLabel("")
        self._broken_label.setObjectName("MutedLabel")
        top_row.addWidget(self._broken_label)
        top_row.addStretch()
        self._delete_broken_btn = QPushButton("Remove All")
        self._delete_broken_btn.setObjectName("DangerBtn")
        self._delete_broken_btn.setFixedHeight(30)
        self._delete_broken_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_broken_btn.setEnabled(False)
        self._delete_broken_btn.setToolTip("Delete all broken symlinks listed below.")
        self._delete_broken_btn.clicked.connect(self._delete_broken_symlinks)
        top_row.addWidget(self._delete_broken_btn)
        lay.addLayout(top_row)

        self._broken_table = self._make_table(["Broken Symlink Path"])
        lay.addWidget(self._broken_table)
        return w

    def _make_table(self, headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setStretchLastSection(True)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.verticalHeader().setVisible(False)
        t.setAlternatingRowColors(False)
        t.setSortingEnabled(True)
        return t

    # ── Scan ─────────────────────────────────────────────────

    def _start_scan(self):
        if self._worker and self._worker.isRunning():
            return
        method = "hash" if self._dupe_combo.currentIndex() == 0 else "name_size"
        self._scan_btn.setEnabled(False)
        self._progress.setValue(0)
        self._progress.show()
        self._status.setText("Scanning…")
        self._worker = AnalyzerWorker(Path.home(), method)
        self._worker.progress.connect(lambda m, p: (self._status.setText(m), self._progress.setValue(p)))
        self._worker.files_done.connect(self._populate_files)
        self._worker.folders_done.connect(self._populate_folders)
        self._worker.dupes_done.connect(self._populate_dupes)
        self._worker.broken_done.connect(self._populate_broken)
        self._worker.error.connect(lambda e: self._status.setText(f"Error: {e}"))
        self._worker.finished.connect(lambda: (self._scan_btn.setEnabled(True), self._progress.hide()))
        self._worker.start()

    # ── Populate tables ───────────────────────────────────────

    def _populate_files(self, entries):
        t = self._files_table
        t.setSortingEnabled(False)
        t.setRowCount(len(entries))
        for row, e in enumerate(entries):
            t.setItem(row, 0, QTableWidgetItem(e.path.name))
            size_item = NumericTableWidgetItem(e.size_human)
            size_item.setData(Qt.ItemDataRole.UserRole, e.size)
            t.setItem(row, 1, size_item)
            t.setItem(row, 2, QTableWidgetItem(e.file_type))
            t.setItem(row, 3, QTableWidgetItem(e.modified_str))
            t.setItem(row, 4, QTableWidgetItem(str(e.path.parent)))
            t.item(row, 0).setData(Qt.ItemDataRole.UserRole + 1, str(e.path))
        t.setSortingEnabled(True)
        self._tabs.setTabText(0, f"Large Files ({len(entries)})")

    def _populate_folders(self, entries):
        t = self._folders_table
        t.setSortingEnabled(False)
        t.setRowCount(len(entries))
        for row, e in enumerate(entries):
            t.setItem(row, 0, QTableWidgetItem(e.path.name))
            size_item = NumericTableWidgetItem(e.size_human)
            size_item.setData(Qt.ItemDataRole.UserRole, e.size)
            t.setItem(row, 1, size_item)
            count_item = NumericTableWidgetItem(str(e.file_count))
            count_item.setData(Qt.ItemDataRole.UserRole, e.file_count)
            t.setItem(row, 2, count_item)
            t.setItem(row, 3, QTableWidgetItem(str(e.path)))
        t.setSortingEnabled(True)
        self._tabs.setTabText(1, f"Large Folders ({len(entries)})")

    def _populate_dupes(self, groups):
        # Split into safe-to-auto-delete vs needs-manual-review
        safe_groups   = [g for g in groups if _safe_copies_to_delete(g)]
        manual_groups = [g for g in groups if not _safe_copies_to_delete(g)]

        # Store only safe groups for the delete action
        self._dupe_groups = safe_groups

        t = self._dupes_table
        t.setSortingEnabled(False)
        t.setRowCount(len(safe_groups))

        if not groups:
            self._dupes_label.setText("No duplicates found.")
        else:
            safe_wasted = sum(
                sum(f.stat().st_size for f in _safe_copies_to_delete(g) if f.exists())
                for g in safe_groups
            )
            parts = []
            if safe_groups:
                parts.append(
                    f"<b>{len(safe_groups)}</b> group(s) with copies in cache/tmp "
                    f"— safe to delete (<b>{_human_size(safe_wasted)}</b> recoverable)"
                )
            if manual_groups:
                parts.append(
                    f"<span style='color:{Theme.p().text_muted}'>"
                    f"{len(manual_groups)} group(s) in personal folders — review manually</span>"
                )
            self._dupes_label.setText(" &nbsp;·&nbsp; ".join(parts))

        for row, g in enumerate(safe_groups):
            to_del = _safe_copies_to_delete(g)
            name = Path(g.files[0]).name if g.files else "?"
            t.setItem(row, 0, QTableWidgetItem(name))

            size_item = NumericTableWidgetItem(_human_size(g.size))
            size_item.setData(Qt.ItemDataRole.UserRole, g.size)
            t.setItem(row, 1, size_item)

            copies_item = NumericTableWidgetItem(str(len(g.files)))
            copies_item.setData(Qt.ItemDataRole.UserRole, len(g.files))
            t.setItem(row, 2, copies_item)

            wasted = sum(f.stat().st_size for f in to_del if f.exists())
            wasted_item = NumericTableWidgetItem(_human_size(wasted))
            wasted_item.setData(Qt.ItemDataRole.UserRole, wasted)
            t.setItem(row, 3, wasted_item)

            # Show: keep (bold) → delete (muted)
            keep_paths  = [p for p in g.files if Path(p) not in to_del]
            paths_text  = (
                "Keep: " + ", ".join(str(p) for p in keep_paths) +
                "  |  Delete: " + ", ".join(str(p) for p in to_del)
            )
            t.setItem(row, 4, QTableWidgetItem(paths_text))

        t.setSortingEnabled(True)
        self._tabs.setTabText(2, f"Duplicates ({len(safe_groups)} safe)")
        self._delete_dupes_btn.setEnabled(bool(safe_groups))

    def _populate_broken(self, paths):
        self._broken_paths = [Path(p) for p in paths]
        t = self._broken_table
        t.setRowCount(len(paths))
        for row, p in enumerate(paths):
            t.setItem(row, 0, QTableWidgetItem(str(p)))
        self._broken_label.setText(
            f"{len(paths)} broken symlink(s) found." if paths else "No broken symlinks."
        )
        self._tabs.setTabText(3, f"Broken Symlinks ({len(paths)})")
        self._delete_broken_btn.setEnabled(bool(paths))

    # ── Actions ───────────────────────────────────────────────

    def _open_file_location(self, row, col):
        item = self._files_table.item(row, 0)
        if item:
            path_str = item.data(Qt.ItemDataRole.UserRole + 1)
            if path_str:
                subprocess.Popen(["xdg-open", str(Path(path_str).parent)])

    def _delete_broken_symlinks(self):
        paths = self._broken_paths
        if not paths:
            return

        confirm = QMessageBox(self.window())
        confirm.setWindowTitle("Remove Broken Symlinks")
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText(
            f"<b>Remove {len(paths)} broken symlink(s)?</b><br><br>"
            "These are dead shortcuts — they point to files that no longer exist. "
            "Removing them is safe and frees up clutter.<br><br>"
            "<b>Note:</b> Only the shortcut link is removed, not any actual file."
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        confirm.button(QMessageBox.StandardButton.Ok).setText("Remove All")
        if confirm.exec() != QMessageBox.StandardButton.Ok:
            return

        removed, errors = 0, []
        for p in paths:
            try:
                p.unlink(missing_ok=True)
                removed += 1
            except OSError as e:
                errors.append(f"{p}: {e}")

        self._broken_paths = []
        self._broken_table.setRowCount(0)
        self._delete_broken_btn.setEnabled(False)

        if errors:
            self._broken_label.setText(
                f"Removed {removed} symlink(s). {len(errors)} error(s): {errors[0]}"
            )
        else:
            self._broken_label.setText(
                f"Done — removed {removed} broken symlink(s)."
            )
        self._tabs.setTabText(3, "Broken Symlinks (0)")

    def _delete_duplicate_copies(self):
        groups = self._dupe_groups   # already filtered to safe groups only
        if not groups:
            return

        to_delete: list[Path] = []
        for g in groups:
            to_delete.extend(_safe_copies_to_delete(g))

        total_freed = sum(f.stat().st_size for f in to_delete if f.exists())

        confirm = QMessageBox(self.window())
        confirm.setWindowTitle("Delete Duplicate Copies")
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText(
            f"<b>Delete {len(to_delete)} file(s) from Downloads / cache?</b><br><br>"
            f"This will free approximately <b>{_human_size(total_freed)}</b>.<br><br>"
            "<b>What happens:</b><br>"
            "• Only copies inside .cache or tmp are deleted<br>"
            "• Downloads and personal folders are never touched<br>"
            "• The copy in your personal folders is always kept"
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        confirm.button(QMessageBox.StandardButton.Ok).setText("Delete Copies")
        if confirm.exec() != QMessageBox.StandardButton.Ok:
            return

        from core.safety import validate_delete
        deleted, skipped, errors = 0, 0, []
        for p in to_delete:
            ok, reason = validate_delete(p)
            if not ok:
                skipped += 1
                errors.append(f"Blocked: {p} — {reason}")
                continue
            try:
                p.unlink(missing_ok=True)
                deleted += 1
            except OSError as e:
                skipped += 1
                errors.append(f"{p}: {e}")

        self._dupe_groups = []
        self._dupes_table.setRowCount(0)
        self._delete_dupes_btn.setEnabled(False)

        summary = f"Done — deleted {deleted} file(s)."
        if skipped:
            summary += f" {skipped} skipped."
        self._dupes_label.setText(summary)
        self._tabs.setTabText(2, "Duplicates (0 safe)")

        if errors:
            err_box = QMessageBox(self.window())
            err_box.setWindowTitle("Some files could not be deleted")
            err_box.setIcon(QMessageBox.Icon.Warning)
            err_box.setText("\n".join(errors[:10]))
            err_box.exec()
