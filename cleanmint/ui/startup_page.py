"""
ui/startup_page.py — Startup Manager UI
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QAbstractItemView, QCheckBox,
    QDialog, QDialogButtonBox, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor

from ui.theme import Theme
from core.startup import StartupManager, StartupEntry


class StartupWorker(QThread):
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def run(self):
        try:
            mgr = StartupManager()
            self.finished.emit(mgr.list_entries())
        except Exception as e:
            self.error.emit(str(e))


# ── Safety badge widget ────────────────────────────────────────────────────

_SAFETY_LABELS = {
    "keep":    ("Keep",    "#22c55e"),   # green
    "caution": ("Caution", "#f97316"),   # orange
    "safe":    ("Safe",    "#3b82f6"),   # blue
    "unknown": ("Unknown", "#94a3b8"),   # slate
}


def _make_badge(safety: str) -> QLabel:
    text, colour = _SAFETY_LABELS.get(safety, ("Unknown", "#94a3b8"))
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setFixedHeight(22)
    lbl.setStyleSheet(
        f"background: {colour}22; color: {colour}; border: 1px solid {colour}66;"
        f"border-radius: 11px; font-size: 11px; font-weight: 600; padding: 0 8px;"
    )
    return lbl


def _badge_cell(safety: str) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(6, 4, 6, 4)
    lay.addWidget(_make_badge(safety))
    lay.addStretch()
    return w


# ── Detail popup ───────────────────────────────────────────────────────────

class EntryDetailDialog(QDialog):
    def __init__(self, entry: StartupEntry, parent=None):
        super().__init__(parent)
        self.setWindowTitle(entry.name)
        self.setMinimumWidth(460)
        self.setStyleSheet(Theme.stylesheet())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(12)

        # Name + badge row
        row = QHBoxLayout()
        name_lbl = QLabel(entry.name)
        name_lbl.setFont(QFont("Inter", 14, QFont.Weight.Bold))
        row.addWidget(name_lbl)
        row.addStretch()
        row.addWidget(_make_badge(entry.safety))
        lay.addLayout(row)

        # Divider
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("Separator")
        lay.addWidget(line)

        # Safety explanation
        p = Theme.p()
        safety_colors = {"keep": p.success, "caution": p.warning, "safe": p.info, "unknown": p.text_muted}
        colour = safety_colors.get(entry.safety, p.text_muted)

        if entry.safety_detail:
            safety_lbl = QLabel(entry.safety_detail)
            safety_lbl.setWordWrap(True)
            safety_lbl.setStyleSheet(f"color: {colour};")
            lay.addWidget(safety_lbl)

        # Description (if different from safety detail)
        if entry.description and entry.description != entry.safety_detail:
            desc_lbl = QLabel(entry.description)
            desc_lbl.setWordWrap(True)
            desc_lbl.setObjectName("MutedLabel")
            lay.addWidget(desc_lbl)

        # Metadata grid
        meta_frame = QFrame()
        meta_frame.setObjectName("Card")
        meta_lay = QVBoxLayout(meta_frame)
        meta_lay.setContentsMargins(12, 10, 12, 10)
        meta_lay.setSpacing(6)

        source_labels = {"xdg_user": "User autostart", "xdg_system": "System autostart", "systemd_user": "systemd user service"}
        for label, value in [
            ("Source",  source_labels.get(entry.source, entry.source)),
            ("Status",  "Enabled" if entry.enabled else "Disabled"),
            ("Command", entry.exec_cmd or "—"),
        ]:
            if not value:
                continue
            h = QHBoxLayout()
            h.setSpacing(8)
            key_lbl = QLabel(f"{label}:")
            key_lbl.setFixedWidth(68)
            key_lbl.setObjectName("MutedLabel")
            val_lbl = QLabel(value)
            val_lbl.setWordWrap(True)
            val_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            h.addWidget(key_lbl)
            h.addWidget(val_lbl, 1)
            meta_lay.addLayout(h)

        lay.addWidget(meta_frame)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)


# ── Main page ──────────────────────────────────────────────────────────────

class StartupPage(QWidget):
    def __init__(self):
        super().__init__()
        self._manager = StartupManager()
        self._entries: list[StartupEntry] = []
        self._worker = None
        self._build_ui()
        QTimer.singleShot(300, self._load_entries)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        hdr = QHBoxLayout()
        title = QLabel("Startup Apps")
        title.setObjectName("TitleLabel")
        hdr.addWidget(title)
        hdr.addStretch()
        self._refresh_btn = QPushButton("↺  Refresh")
        self._refresh_btn.setObjectName("SecondaryBtn")
        self._refresh_btn.setFixedHeight(34)
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self._load_entries)
        hdr.addWidget(self._refresh_btn)
        layout.addLayout(hdr)

        sub = QLabel(
            "Apps and services that start automatically with your session. "
            "Click any row to see what it does and whether it is safe to disable. "
            "Disabling unused ones can improve boot time."
        )
        sub.setObjectName("SubtitleLabel")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        self._status = QLabel("Loading…")
        self._status.setObjectName("MutedLabel")
        layout.addWidget(self._status)

        # Legend
        legend = QHBoxLayout()
        legend.setSpacing(16)
        for safety in ("keep", "safe", "caution", "unknown"):
            badge = _make_badge(safety)
            badge.setFixedWidth(72)
            hints = {
                "keep":    "Essential — do not disable",
                "safe":    "Safe to disable",
                "caution": "Disable with care",
                "unknown": "Tap row to learn more",
            }
            hint = QLabel(hints[safety])
            hint.setObjectName("MutedLabel")
            legend.addWidget(badge)
            legend.addWidget(hint)
        legend.addStretch()
        layout.addLayout(legend)

        # Table — 5 columns: Enabled | Name | Source | Safety | (hidden detail)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["On/Off", "Name", "Source", "Safety"])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.setColumnWidth(0, 64)
        self._table.setColumnWidth(2, 110)
        self._table.setColumnWidth(3, 100)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._table.cellClicked.connect(self._on_row_clicked)
        self._table.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self._table, 1)

        info = QLabel(
            "ℹ  Disabling system entries creates an override in ~/.config/autostart — "
            "it does not modify system files."
        )
        info.setObjectName("MutedLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

    def _load_entries(self):
        if self._worker and self._worker.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._status.setText("Loading startup entries…")
        self._table.setRowCount(0)

        self._worker = StartupWorker()
        self._worker.finished.connect(self._populate)
        self._worker.error.connect(lambda e: self._status.setText(f"Error: {e}"))
        self._worker.finished.connect(lambda: self._refresh_btn.setEnabled(True))
        self._worker.start()

    def _populate(self, entries: list):
        self._entries = entries
        self._table.setRowCount(len(entries))

        source_labels = {
            "xdg_user":    "User",
            "xdg_system":  "System",
            "systemd_user":"systemd",
        }
        p = Theme.p()

        for row, entry in enumerate(entries):
            # Col 0: Toggle checkbox
            chk = QCheckBox()
            chk.setChecked(entry.enabled)
            chk.setCursor(Qt.CursorShape.PointingHandCursor)
            chk.stateChanged.connect(lambda state, e=entry: self._toggle(e, state))
            cell = QWidget()
            cell_lay = QHBoxLayout(cell)
            cell_lay.setContentsMargins(8, 0, 0, 0)
            cell_lay.addWidget(chk)
            self._table.setCellWidget(row, 0, cell)

            # Col 1: Name
            name_item = QTableWidgetItem(entry.name)
            name_item.setFont(QFont("Inter", 12))
            self._table.setItem(row, 1, name_item)

            # Col 2: Source
            src_label = source_labels.get(entry.source, entry.source)
            src_item = QTableWidgetItem(src_label)
            src_item.setForeground(QColor(p.text_muted))
            self._table.setItem(row, 2, src_item)

            # Col 3: Safety badge
            self._table.setCellWidget(row, 3, _badge_cell(entry.safety))

            self._table.setRowHeight(row, 40)

        enabled_count = sum(1 for e in entries if e.enabled)
        self._status.setText(
            f"{len(entries)} startup entries · {enabled_count} enabled · "
            "Click a row for details"
        )

    def _on_row_clicked(self, row: int, _col: int):
        if row < len(self._entries):
            dlg = EntryDetailDialog(self._entries[row], self.window())
            dlg.exec()

    def _toggle(self, entry: StartupEntry, state: int):
        enable = (state == Qt.CheckState.Checked.value)
        if enable:
            ok, msg = self._manager.enable_entry(entry)
        else:
            ok, msg = self._manager.disable_entry(entry)

        entry.enabled = enable
        self._status.setText(msg if ok else f"Error: {msg}")
