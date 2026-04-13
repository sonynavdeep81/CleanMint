"""
ui/vscode_page.py — VS Code Profile Viewer

Shows installed VS Code extensions and user settings.
Lets the user export a portable restore script.
"""

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.vscode import (
    VSCodeExtension,
    VSCodeProfile,
    generate_restore_script,
    is_data_available,
    load_profile,
)
from ui.theme import Theme


# ── Background worker ──────────────────────────────────────────────────────


class LoadWorker(QThread):
    finished = pyqtSignal(object)   # VSCodeProfile
    error    = pyqtSignal(str)

    def run(self):
        try:
            self.finished.emit(load_profile())
        except Exception as e:
            self.error.emit(str(e))


# ── Page ───────────────────────────────────────────────────────────────────


class VSCodePage(QWidget):
    def __init__(self):
        super().__init__()
        self._extensions: list[VSCodeExtension] = []
        self._worker: QThread | None = None
        self._build_ui()
        self._load()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        p = Theme.p()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        # ── Header ─────────────────────────────────────────────────────────
        hdr = QHBoxLayout()

        title = QLabel("VS Code Profile")
        title.setObjectName("TitleLabel")
        hdr.addWidget(title)
        hdr.addStretch()

        self._refresh_btn = QPushButton("↻  Refresh")
        self._refresh_btn.setObjectName("SecondaryBtn")
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.setFixedHeight(32)
        self._refresh_btn.clicked.connect(self._load)
        hdr.addWidget(self._refresh_btn)

        self._export_btn = QPushButton("⬇  Export Restore Script")
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.setFixedHeight(32)
        self._export_btn.setEnabled(False)
        self._export_btn.setStyleSheet(
            f"QPushButton {{ background: {p.accent}; color: #fff; border: none;"
            f"  border-radius: 6px; font-weight: 600; padding: 0 16px; }}"
            f"QPushButton:hover {{ background: {p.accent}cc; }}"
            f"QPushButton:disabled {{ background: {p.accent}55; }}"
        )
        self._export_btn.clicked.connect(self._export)
        hdr.addWidget(self._export_btn)

        layout.addLayout(hdr)

        sub = QLabel(
            "View all your VS Code extensions and settings. "
            "Export a restore script to set up the same environment on any machine."
        )
        sub.setObjectName("SubtitleLabel")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        # ── Status label ───────────────────────────────────────────────────
        self._status = QLabel("")
        self._status.setObjectName("MutedLabel")
        self._status.hide()
        layout.addWidget(self._status)

        # ── Disk-fallback notice (shown when VS Code app is uninstalled) ───
        self._disk_notice = QLabel(
            "VS Code is not installed — showing your saved profile from disk.  "
            "You can still export the restore script to reinstall everything."
        )
        self._disk_notice.setObjectName("MutedLabel")
        self._disk_notice.setWordWrap(True)
        self._disk_notice.setStyleSheet(
            f"color: {Theme.p().warning}; padding: 4px 0;"
        )
        self._disk_notice.hide()
        layout.addWidget(self._disk_notice)

        # ── No-data banner (VS Code never installed on this machine) ───────
        self._not_installed = QLabel(
            "No VS Code data found on this machine.\n"
            "Install VS Code and open it at least once, then come back here."
        )
        self._not_installed.setObjectName("MutedLabel")
        self._not_installed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._not_installed.setWordWrap(True)
        self._not_installed.hide()
        layout.addWidget(self._not_installed)

        # ── Splitter: extensions left | tabs right ─────────────────────────
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(6)

        # Left — extensions table
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)

        self._ext_count = QLabel("Extensions")
        self._ext_count.setObjectName("SubtitleLabel")
        left_lay.addWidget(self._ext_count)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Publisher", "Extension", "Version"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setDefaultSectionSize(32)
        left_lay.addWidget(self._table, 1)

        self._splitter.addWidget(left)

        # Right — settings / keybindings tabs
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(6)

        right_lbl = QLabel("Configuration")
        right_lbl.setObjectName("SubtitleLabel")
        right_lay.addWidget(right_lbl)

        self._tabs = QTabWidget()

        self._settings_view = QTextEdit()
        self._settings_view.setReadOnly(True)
        self._settings_view.setFont(QFont("Monospace", 10))
        self._settings_view.setPlaceholderText("settings.json not found")
        self._tabs.addTab(self._settings_view, "settings.json")

        self._keybindings_view = QTextEdit()
        self._keybindings_view.setReadOnly(True)
        self._keybindings_view.setFont(QFont("Monospace", 10))
        self._keybindings_view.setPlaceholderText("keybindings.json not found")
        self._tabs.addTab(self._keybindings_view, "keybindings.json")

        right_lay.addWidget(self._tabs, 1)
        self._splitter.addWidget(right)

        self._splitter.setSizes([480, 480])
        layout.addWidget(self._splitter, 1)

    # ── Data loading ───────────────────────────────────────────────────────

    def _load(self):
        if not is_data_available():
            # VS Code was never installed — nothing to show
            self._not_installed.show()
            self._disk_notice.hide()
            self._splitter.hide()
            self._export_btn.setEnabled(False)
            return

        self._not_installed.hide()
        self._splitter.show()
        self._set_status("Loading VS Code profile…")
        self._refresh_btn.setEnabled(False)
        self._export_btn.setEnabled(False)

        self._worker = LoadWorker()
        self._worker.finished.connect(self._on_loaded)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_loaded(self, profile: VSCodeProfile):
        self._extensions = profile.extensions
        self._populate_table(profile.extensions)
        self._settings_view.setPlainText(profile.settings_text)
        self._keybindings_view.setPlainText(profile.keybindings_text)
        self._ext_count.setText(
            f"Extensions  ({len(profile.extensions)} found)"
        )
        # Show disk-fallback notice if VS Code app is gone
        self._disk_notice.setVisible(profile.source == "disk")
        self._set_status("")
        self._refresh_btn.setEnabled(True)
        self._export_btn.setEnabled(bool(profile.extensions))

    def _on_error(self, err: str):
        self._set_status(f"Error: {err}")
        self._refresh_btn.setEnabled(True)

    def _populate_table(self, extensions: list):
        self._table.setRowCount(0)
        for ext in extensions:
            row = self._table.rowCount()
            self._table.insertRow(row)
            for col, val in enumerate([ext.publisher, ext.name, ext.version]):
                item = QTableWidgetItem(val)
                item.setData(Qt.ItemDataRole.UserRole, ext.ext_id)
                self._table.setItem(row, col, item)

    # ── Export ─────────────────────────────────────────────────────────────

    def _export(self):
        if not self._extensions:
            QMessageBox.information(
                self, "No Data",
                "No extensions loaded. Click Refresh first."
            )
            return

        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Export VS Code Restore Script",
            str(Path.home() / "vscode_restore.sh"),
            "Shell Scripts (*.sh);;All Files (*)",
        )
        if not dest:
            return

        try:
            generate_restore_script(self._extensions, Path(dest))
            QMessageBox.information(
                self,
                "Script Exported",
                f"VS Code restore script saved to:\n{dest}\n\n"
                f"It will install {len(self._extensions)} extensions, "
                "your settings.json, and keybindings.json.\n\n"
                "On any new machine run:\n"
                "  bash vscode_restore.sh",
            )
        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))

    # ── Helpers ────────────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self._status.setText(msg)
        self._status.setVisible(bool(msg))
