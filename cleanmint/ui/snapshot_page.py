"""
ui/snapshot_page.py — Snapshot Manager UI

Lets users take, browse, export, compare, and delete system snapshots.
"""

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.snapshot import SnapshotEngine, SnapshotMeta
from ui.theme import Theme


# ── Background workers ─────────────────────────────────────────────────────


class TakeSnapshotWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(object)   # SnapshotMeta
    error    = pyqtSignal(str)

    def __init__(self, label: str):
        super().__init__()
        self._label = label

    def run(self):
        try:
            engine = SnapshotEngine()
            meta = engine.take(
                label=self._label,
                progress_callback=lambda m, p: self.progress.emit(m, p),
            )
            self.finished.emit(meta)
        except Exception as e:
            self.error.emit(str(e))


class ListSnapshotsWorker(QThread):
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def run(self):
        try:
            self.finished.emit(SnapshotEngine().list_snapshots())
        except Exception as e:
            self.error.emit(str(e))


class DiffWorker(QThread):
    finished = pyqtSignal(dict, str, str)   # diff, name_a, name_b
    error    = pyqtSignal(str)

    def __init__(self, name_a: str, name_b: str):
        super().__init__()
        self._a = name_a
        self._b = name_b

    def run(self):
        try:
            diff = SnapshotEngine().diff(self._a, self._b)
            self.finished.emit(diff, self._a, self._b)
        except Exception as e:
            self.error.emit(str(e))


# ── Diff dialog ────────────────────────────────────────────────────────────


class DiffDialog(QDialog):
    def __init__(self, diff: dict, label_a: str, label_b: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Snapshot Comparison")
        self.setMinimumSize(640, 480)
        self.setStyleSheet(Theme.stylesheet())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        title = QLabel(f'Changes from  "{label_a}"  →  "{label_b}"')
        title.setObjectName("TitleLabel")
        title.setWordWrap(True)
        lay.addWidget(title)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setFont(QFont("Monospace", 10))

        lines = []
        for category in ("apt", "snap", "flatpak"):
            added   = diff[category]["added"]
            removed = diff[category]["removed"]
            if not added and not removed:
                continue
            lines.append(f"── {category.upper()} {'─' * 50}")
            for pkg in added:
                lines.append(f"  + {pkg}")
            for pkg in removed:
                lines.append(f"  - {pkg}")
            lines.append("")

        text.setPlainText(
            "\n".join(lines) if lines
            else "No differences found between the two snapshots."
        )
        lay.addWidget(text, 1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)


# ── Main page ──────────────────────────────────────────────────────────────


class SnapshotPage(QWidget):
    def __init__(self):
        super().__init__()
        self._worker: QThread | None = None
        self._snapshots: list[SnapshotMeta] = []
        self._build_ui()
        QTimer.singleShot(200, self._load_snapshots)

    def _build_ui(self):
        p = Theme.p()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        # ── Header ─────────────────────────────────────────────────────────
        hdr = QHBoxLayout()

        title = QLabel("Snapshots")
        title.setObjectName("TitleLabel")
        hdr.addWidget(title)
        hdr.addStretch()

        self._take_btn = QPushButton("⊕  Take Snapshot")
        self._take_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._take_btn.setFixedHeight(34)
        self._take_btn.setStyleSheet(
            f"QPushButton {{ background: {p.accent}; color: #fff; border: none;"
            f"  border-radius: 6px; font-weight: 600; padding: 0 16px; }}"
            f"QPushButton:hover {{ background: {p.accent}cc; }}"
            f"QPushButton:disabled {{ background: {p.accent}55; }}"
        )
        self._take_btn.clicked.connect(self._take_snapshot)
        hdr.addWidget(self._take_btn)

        layout.addLayout(hdr)

        sub = QLabel(
            "Capture a full list of your installed packages and generate a restore "
            "script you can run on any fresh Ubuntu install."
        )
        sub.setObjectName("SubtitleLabel")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        # ── Progress (hidden until active) ─────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setObjectName("MutedLabel")
        self._status.hide()
        layout.addWidget(self._status)

        # ── Action bar ─────────────────────────────────────────────────────
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self._export_btn = QPushButton("⬇  Export Restore Script")
        self._export_btn.setObjectName("SecondaryBtn")
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.setFixedHeight(30)
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export_script)
        action_row.addWidget(self._export_btn)

        self._diff_btn = QPushButton("⇄  Compare Two")
        self._diff_btn.setObjectName("SecondaryBtn")
        self._diff_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._diff_btn.setFixedHeight(30)
        self._diff_btn.setEnabled(False)
        self._diff_btn.clicked.connect(self._compare_snapshots)
        action_row.addWidget(self._diff_btn)

        self._delete_btn = QPushButton("✕  Delete")
        self._delete_btn.setObjectName("DangerBtn")
        self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_btn.setFixedHeight(30)
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._delete_snapshot)
        action_row.addWidget(self._delete_btn)

        action_row.addStretch()
        layout.addLayout(action_row)

        # ── Snapshots table ────────────────────────────────────────────────
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Label", "Date", "apt pkgs", "snaps", "flatpaks"]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for col in (1, 2, 3, 4):
            self._table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self._table, 1)

        # ── Empty state ────────────────────────────────────────────────────
        self._empty = QLabel(
            'No snapshots yet.\nClick "⊕ Take Snapshot" to capture your current setup.'
        )
        self._empty.setObjectName("MutedLabel")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.hide()
        layout.addWidget(self._empty)

    # ── Data loading ───────────────────────────────────────────────────────

    def _load_snapshots(self):
        w = ListSnapshotsWorker()
        w.finished.connect(self._populate)
        w.error.connect(lambda e: self._set_status(f"Error: {e}"))
        w.start()
        self._worker = w

    def _populate(self, snapshots: list):
        self._snapshots = snapshots
        self._table.setRowCount(0)

        if not snapshots:
            self._table.hide()
            self._empty.show()
            self._on_selection()
            return

        self._empty.hide()
        self._table.show()

        for meta in snapshots:
            row = self._table.rowCount()
            self._table.insertRow(row)

            try:
                dt = datetime.fromisoformat(meta.created_at)
                date_str = dt.strftime("%d %b %Y  %H:%M")
            except Exception:
                date_str = meta.created_at[:16]

            for col, val in enumerate([
                meta.label,
                date_str,
                str(meta.apt_count),
                str(meta.snap_count),
                str(meta.flatpak_count),
            ]):
                item = QTableWidgetItem(val)
                item.setData(Qt.ItemDataRole.UserRole, meta.name)
                if col >= 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col, item)

        self._on_selection()

    # ── Actions ────────────────────────────────────────────────────────────

    def _take_snapshot(self):
        label, ok = QInputDialog.getText(
            self, "New Snapshot", "Label for this snapshot (leave blank for auto):"
        )
        if not ok:
            return

        self._take_btn.setEnabled(False)
        self._progress.setValue(0)
        self._progress.show()
        self._set_status("Starting…")

        self._worker = TakeSnapshotWorker(label.strip())
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_snapshot_done)
        self._worker.error.connect(self._on_snapshot_error)
        self._worker.start()

    def _on_progress(self, msg: str, pct: int):
        self._progress.setValue(pct)
        self._set_status(msg)

    def _on_snapshot_done(self, meta: SnapshotMeta):
        self._progress.setValue(100)
        self._set_status(f'Snapshot "{meta.label}" saved.')
        self._take_btn.setEnabled(True)
        QTimer.singleShot(3000, self._hide_progress)
        self._load_snapshots()

    def _on_snapshot_error(self, err: str):
        self._progress.hide()
        self._status.hide()
        self._take_btn.setEnabled(True)
        QMessageBox.warning(self, "Snapshot Error", err)

    def _export_script(self):
        meta = self._selected_meta()
        if not meta:
            return

        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Export Restore Script",
            str(Path.home() / f"restore_{meta.name}.sh"),
            "Shell Scripts (*.sh);;All Files (*)",
        )
        if not dest:
            return

        try:
            SnapshotEngine().export_restore_script(meta.name, Path(dest))
            QMessageBox.information(
                self,
                "Script Exported",
                f"Restore script saved to:\n{dest}\n\n"
                "Copy this file to your new machine and run:\n"
                "  bash restore.sh",
            )
        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))

    def _compare_snapshots(self):
        if len(self._snapshots) < 2:
            QMessageBox.information(
                self, "Compare",
                "You need at least two snapshots to compare."
            )
            return

        selected = self._selected_meta()
        if not selected:
            return

        others = [s for s in self._snapshots if s.name != selected.name]
        other_labels = [s.label for s in others]

        chosen_label, ok = QInputDialog.getItem(
            self,
            "Compare Snapshots",
            f'Compare "{selected.label}" against:',
            other_labels, 0, False,
        )
        if not ok:
            return

        other = others[other_labels.index(chosen_label)]
        self._set_status("Comparing snapshots…")

        self._worker = DiffWorker(selected.name, other.name)
        self._worker.finished.connect(
            lambda diff, _a, _b: self._show_diff(diff, selected.label, other.label)
        )
        self._worker.error.connect(
            lambda e: QMessageBox.warning(self, "Compare Error", e)
        )
        self._worker.start()

    def _show_diff(self, diff: dict, label_a: str, label_b: str):
        self._set_status("")
        DiffDialog(diff, label_a, label_b, self).exec()

    def _delete_snapshot(self):
        meta = self._selected_meta()
        if not meta:
            return

        reply = QMessageBox.question(
            self,
            "Delete Snapshot",
            f'Delete snapshot "{meta.label}"?\n\nThis cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            SnapshotEngine().delete(meta.name)
            self._load_snapshots()
        except Exception as e:
            QMessageBox.warning(self, "Delete Error", str(e))

    # ── Helpers ────────────────────────────────────────────────────────────

    def _selected_meta(self) -> SnapshotMeta | None:
        items = self._table.selectedItems()
        if not items:
            return None
        name = items[0].data(Qt.ItemDataRole.UserRole)
        return next((s for s in self._snapshots if s.name == name), None)

    def _on_selection(self):
        has = bool(self._table.selectedItems())
        self._export_btn.setEnabled(has)
        self._delete_btn.setEnabled(has)
        self._diff_btn.setEnabled(has and len(self._snapshots) >= 2)

    def _set_status(self, msg: str):
        self._status.setText(msg)
        self._status.setVisible(bool(msg))

    def _hide_progress(self):
        self._progress.hide()
        self._status.hide()
