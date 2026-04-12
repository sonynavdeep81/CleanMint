"""
ui/apps_page.py — Installed Apps Manager UI

Lists user-installed APT packages, Snap apps, and Flatpak apps.
Runs a safety check before every uninstall:
  - Blocked if the package is essential or would break the system.
  - Warning shown if removing it also removes other packages.
  - Snaps and Flatpaks are always safe to remove (isolated containers).
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QAbstractItemView, QLineEdit, QComboBox,
    QMessageBox, QDialog, QDialogButtonBox, QTextEdit,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSortFilterProxyModel
from PyQt6.QtGui import QFont, QColor

from ui.theme import Theme
from core.apps import AppManager, InstalledApp, RemovalSafety


# ── Background worker ──────────────────────────────────────────────────────

class AppsWorker(QThread):
    apt_done     = pyqtSignal(list)
    snap_done    = pyqtSignal(list)
    flatpak_done = pyqtSignal(list)
    progress     = pyqtSignal(str, int)
    error        = pyqtSignal(str)

    def run(self):
        try:
            mgr = AppManager(progress_callback=lambda m, p: self.progress.emit(m, p))
            self.progress.emit("Reading APT packages…", 10)
            self.apt_done.emit(mgr.list_apt_apps())
            self.progress.emit("Reading Snap packages…", 60)
            self.snap_done.emit(mgr.list_snap_apps())
            self.progress.emit("Reading Flatpak apps…", 85)
            self.flatpak_done.emit(mgr.list_flatpak_apps())
            self.progress.emit("Done.", 100)
        except Exception as e:
            self.error.emit(str(e))


class UninstallWorker(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, app: InstalledApp):
        super().__init__()
        self._app = app

    def run(self):
        mgr = AppManager()
        ok, msg = mgr.uninstall(self._app)
        self.finished.emit(ok, msg)


# ── Numeric sort for size column ───────────────────────────────────────────

class NumericItem(QTableWidgetItem):
    def __lt__(self, other):
        try:
            return (self.data(Qt.ItemDataRole.UserRole) or 0) < \
                   (other.data(Qt.ItemDataRole.UserRole) or 0)
        except TypeError:
            return super().__lt__(other)


# ── Main page ──────────────────────────────────────────────────────────────

class AppsPage(QWidget):
    def __init__(self):
        super().__init__()
        self._manager = AppManager()
        self._worker  = None
        self._uninstall_worker = None
        # All apps by source (filled after scan)
        self._apt_apps:     list[InstalledApp] = []
        self._snap_apps:    list[InstalledApp] = []
        self._flatpak_apps: list[InstalledApp] = []
        self._build_ui()
        QTimer.singleShot(400, self._start_scan)

    # ── UI construction ───────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("Installed Apps")
        title.setObjectName("TitleLabel")
        hdr.addWidget(title)
        hdr.addStretch()
        self._refresh_btn = QPushButton("↺  Refresh")
        self._refresh_btn.setObjectName("SecondaryBtn")
        self._refresh_btn.setFixedHeight(34)
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self._start_scan)
        hdr.addWidget(self._refresh_btn)
        layout.addLayout(hdr)

        sub = QLabel(
            "Shows apps you manually installed. "
            "CleanMint checks for dependency impact before any uninstall — "
            "it will warn you if removing an app would affect other software."
        )
        sub.setObjectName("SubtitleLabel")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        # Status + progress
        self._status = QLabel("Loading…")
        self._status.setObjectName("MutedLabel")
        layout.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setFixedHeight(6)
        layout.addWidget(self._progress)

        # Filter bar
        filter_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search apps…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._search, 1)

        self._source_filter = QComboBox()
        self._source_filter.addItems(["All sources", "APT", "Snap", "Flatpak"])
        self._source_filter.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self._source_filter)
        layout.addLayout(filter_row)

        # Table
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Name", "Version", "Size", "Source", "Action"]
        )
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.setColumnWidth(1, 130)
        self._table.setColumnWidth(2, 90)
        self._table.setColumnWidth(3, 80)
        self._table.setColumnWidth(4, 110)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._table.setSortingEnabled(True)
        layout.addWidget(self._table, 1)

        note = QLabel(
            "ℹ  APT packages: only manually installed apps are shown (not system dependencies). "
            "Snap and Flatpak apps are isolated — safe to remove individually."
        )
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

    # ── Scanning ──────────────────────────────────────────────────

    def _start_scan(self):
        if self._worker and self._worker.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._status.setText("Scanning installed apps…")
        self._progress.setValue(0)
        self._table.setRowCount(0)
        self._apt_apps = []
        self._snap_apps = []
        self._flatpak_apps = []

        self._worker = AppsWorker()
        self._worker.progress.connect(lambda m, p: (
            self._status.setText(m), self._progress.setValue(p)
        ))
        self._worker.apt_done.connect(self._on_apt_done)
        self._worker.snap_done.connect(self._on_snap_done)
        self._worker.flatpak_done.connect(self._on_flatpak_done)
        self._worker.error.connect(lambda e: self._status.setText(f"Error: {e}"))
        self._worker.finished.connect(self._on_scan_done)
        self._worker.start()

    def _on_apt_done(self, apps):
        self._apt_apps = apps
        self._apply_filter()

    def _on_snap_done(self, apps):
        self._snap_apps = apps
        self._apply_filter()

    def _on_flatpak_done(self, apps):
        self._flatpak_apps = apps
        self._apply_filter()

    def _on_scan_done(self):
        self._refresh_btn.setEnabled(True)
        total = len(self._apt_apps) + len(self._snap_apps) + len(self._flatpak_apps)
        self._status.setText(
            f"{total} app(s) found — "
            f"{len(self._apt_apps)} APT · "
            f"{len(self._snap_apps)} Snap · "
            f"{len(self._flatpak_apps)} Flatpak"
        )

    # ── Filtering + table population ──────────────────────────────

    def _apply_filter(self):
        source_idx = self._source_filter.currentIndex()
        query = self._search.text().lower().strip()

        source_map = {0: None, 1: "apt", 2: "snap", 3: "flatpak"}
        source = source_map.get(source_idx)

        all_apps = self._apt_apps + self._snap_apps + self._flatpak_apps
        filtered = [
            a for a in all_apps
            if (source is None or a.source == source)
            and (not query or query in a.name.lower() or query in a.description.lower())
        ]
        self._populate(filtered)

    def _populate(self, apps: list[InstalledApp]):
        p = Theme.p()
        source_colours = {"apt": p.accent, "snap": "#f97316", "flatpak": "#3b82f6"}

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(apps))

        for row, app in enumerate(apps):
            # Name + description tooltip
            name_item = QTableWidgetItem(app.name)
            name_item.setFont(QFont("Inter", 12))
            name_item.setToolTip(app.description)
            name_item.setData(Qt.ItemDataRole.UserRole, app)   # store app ref
            self._table.setItem(row, 0, name_item)

            # Version
            self._table.setItem(row, 1, QTableWidgetItem(app.version))

            # Size
            size_item = NumericItem(app.size_human)
            size_item.setData(Qt.ItemDataRole.UserRole, app.size_bytes)
            self._table.setItem(row, 2, size_item)

            # Source badge
            src_item = QTableWidgetItem(app.source.upper())
            src_item.setForeground(QColor(source_colours.get(app.source, p.text_muted)))
            self._table.setItem(row, 3, src_item)

            # Uninstall button
            btn = QPushButton("Uninstall")
            btn.setObjectName("DangerBtn")
            btn.setFixedHeight(26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, a=app: self._confirm_uninstall(a))
            cell = QWidget()
            cell_lay = QHBoxLayout(cell)
            cell_lay.setContentsMargins(4, 3, 4, 3)
            cell_lay.addWidget(btn)
            self._table.setCellWidget(row, 4, cell)

            self._table.setRowHeight(row, 38)

        self._table.setSortingEnabled(True)

    # ── Uninstall flow ─────────────────────────────────────────────

    def _confirm_uninstall(self, app: InstalledApp):
        # Run safety check first
        safety = self._manager.check_removal_safety(app)
        p = Theme.p()

        if safety.blocked:
            msg = QMessageBox(self.window())
            msg.setWindowTitle("Cannot Uninstall")
            msg.setIcon(QMessageBox.Icon.Critical)
            msg.setText(
                f"<b>{app.name}</b> cannot be removed.<br><br>"
                f"{safety.warning}"
            )
            msg.exec()
            return

        confirm = QMessageBox(self.window())
        confirm.setWindowTitle("Uninstall App")

        if safety.extra_removals:
            confirm.setIcon(QMessageBox.Icon.Warning)
            extras = "\n  • ".join(safety.extra_removals[:15])
            suffix = f"\n  … and {len(safety.extra_removals)-15} more" \
                     if len(safety.extra_removals) > 15 else ""
            confirm.setText(
                f"<b>Uninstall {app.name} ({app.version})?</b><br><br>"
                f"<span style='color:{p.warning}'><b>Warning:</b> {safety.warning}</span><br><br>"
                f"The following additional packages will also be removed:<br>"
                f"<code>  • {extras}{suffix}</code><br><br>"
                "If you are unsure, click Cancel and look up these packages first."
            )
            confirm.button(confirm.addButton(
                "Uninstall Anyway", QMessageBox.ButtonRole.AcceptRole
            ))
            confirm.addButton(QMessageBox.StandardButton.Cancel)
            confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        else:
            confirm.setIcon(QMessageBox.Icon.Question)
            source_note = {
                "apt":     "This APT package and its configuration files will be removed.",
                "snap":    "This Snap app and its data will be removed. Other apps are not affected.",
                "flatpak": "This Flatpak app will be removed. Other apps are not affected.",
            }.get(app.source, "")
            confirm.setText(
                f"<b>Uninstall {app.name} ({app.version})?</b><br><br>"
                f"{source_note}<br><br>"
                "Your password will be required once."
            )
            confirm.setStandardButtons(
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
            )
            confirm.button(QMessageBox.StandardButton.Ok).setText("Uninstall")

        result = confirm.exec()

        # For the warning dialog, AcceptRole = role 0
        accepted = (
            result == QMessageBox.StandardButton.Ok or
            result == 0  # AcceptRole when extra packages dialog used
        )
        if not accepted:
            return

        self._run_uninstall(app)

    def _run_uninstall(self, app: InstalledApp):
        self._refresh_btn.setEnabled(False)
        self._status.setText(f"Uninstalling {app.name}… (password may be required)")
        self._progress.setRange(0, 0)   # indeterminate

        self._uninstall_worker = UninstallWorker(app)
        self._uninstall_worker.finished.connect(
            lambda ok, msg: self._on_uninstall_done(ok, msg, app)
        )
        self._uninstall_worker.start()

    def _on_uninstall_done(self, ok: bool, msg: str, app: InstalledApp):
        self._progress.setRange(0, 100)
        self._progress.setValue(100 if ok else 0)
        self._refresh_btn.setEnabled(True)

        if ok:
            self._status.setText(f"Uninstalled {app.name} successfully.")
            # Remove from internal lists and refresh table
            for lst in (self._apt_apps, self._snap_apps, self._flatpak_apps):
                try:
                    lst.remove(app)
                except ValueError:
                    pass
            self._apply_filter()
        else:
            self._status.setText(f"Failed to uninstall {app.name}.")
            err = QMessageBox(self.window())
            err.setWindowTitle("Uninstall Failed")
            err.setIcon(QMessageBox.Icon.Warning)
            err.setText(f"Could not uninstall <b>{app.name}</b>:<br><br>{msg}")
            err.exec()
