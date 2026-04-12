"""
ui/settings_page.py — Settings UI
"""

from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QCheckBox, QSpinBox, QLineEdit, QScrollArea,
    QFileDialog, QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from ui.theme import Theme
from config.settings import settings


def _section(title: str) -> QLabel:
    lbl = QLabel(title)
    lbl.setObjectName("SectionHeader")
    lbl.setContentsMargins(0, 12, 0, 4)
    return lbl


def _divider() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    return f


class SettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 24, 28, 32)
        layout.setSpacing(6)

        title = QLabel("Settings")
        title.setObjectName("TitleLabel")
        layout.addWidget(title)

        # ── Appearance ────────────────────────────────────────
        layout.addWidget(_section("Appearance"))
        layout.addWidget(_divider())

        dark_row = QHBoxLayout()
        dark_row.addWidget(QLabel("Dark mode"))
        dark_row.addStretch()
        self._dark_chk = QCheckBox()
        self._dark_chk.setChecked(settings.get("dark_mode", True))
        self._dark_chk.stateChanged.connect(
            lambda s: settings.set("dark_mode", s == Qt.CheckState.Checked.value)
        )
        dark_row.addWidget(self._dark_chk)
        layout.addLayout(dark_row)

        # ── Scan Behaviour ────────────────────────────────────
        layout.addWidget(_section("Scan Behaviour"))
        layout.addWidget(_divider())

        scan_row = QHBoxLayout()
        scan_row.addWidget(QLabel("Scan on startup"))
        scan_row.addStretch()
        self._scan_chk = QCheckBox()
        self._scan_chk.setChecked(settings.get("scan_on_startup", False))
        self._scan_chk.stateChanged.connect(
            lambda s: settings.set("scan_on_startup", s == Qt.CheckState.Checked.value)
        )
        scan_row.addWidget(self._scan_chk)
        layout.addLayout(scan_row)

        remind_row = QHBoxLayout()
        remind_row.addWidget(QLabel("Monthly cleanup reminder"))
        remind_row.addStretch()
        self._remind_chk = QCheckBox()
        self._remind_chk.setChecked(settings.get("auto_monthly_reminder", True))
        self._remind_chk.stateChanged.connect(
            lambda s: settings.set("auto_monthly_reminder", s == Qt.CheckState.Checked.value)
        )
        remind_row.addWidget(self._remind_chk)
        layout.addLayout(remind_row)

        # Downloads age
        dl_row = QHBoxLayout()
        dl_row.addWidget(QLabel("Flag downloads older than (days)"))
        dl_row.addStretch()
        self._dl_spin = QSpinBox()
        self._dl_spin.setRange(1, 365)
        self._dl_spin.setValue(settings.get("downloads_age_days", 30))
        self._dl_spin.setFixedWidth(70)
        self._dl_spin.valueChanged.connect(lambda v: settings.set("downloads_age_days", v))
        dl_row.addWidget(self._dl_spin)
        layout.addLayout(dl_row)

        # Duplicate method
        dupe_row = QHBoxLayout()
        dupe_row.addWidget(QLabel("Duplicate detection method"))
        dupe_row.addStretch()
        from PyQt6.QtWidgets import QComboBox
        self._dupe_combo = QComboBox()
        self._dupe_combo.addItems(["Hash (accurate)", "Name + Size (fast)"])
        self._dupe_combo.setCurrentIndex(0 if settings.get("duplicate_method") == "hash" else 1)
        self._dupe_combo.currentIndexChanged.connect(
            lambda i: settings.set("duplicate_method", "hash" if i == 0 else "name_size")
        )
        dupe_row.addWidget(self._dupe_combo)
        layout.addLayout(dupe_row)

        # Log retention
        log_row = QHBoxLayout()
        log_row.addWidget(QLabel("Keep log files for (days)"))
        log_row.addStretch()
        self._log_spin = QSpinBox()
        self._log_spin.setRange(7, 365)
        self._log_spin.setValue(settings.get("log_retention_days", 90))
        self._log_spin.setFixedWidth(70)
        self._log_spin.valueChanged.connect(lambda v: settings.set("log_retention_days", v))
        log_row.addWidget(self._log_spin)
        layout.addLayout(log_row)

        # ── Exclusions ────────────────────────────────────────
        layout.addWidget(_section("Excluded Folders"))
        layout.addWidget(_divider())

        excl_note = QLabel(
            "Files inside these folders will never be touched, even if they match a cleanup rule."
        )
        excl_note.setObjectName("MutedLabel")
        excl_note.setWordWrap(True)
        layout.addWidget(excl_note)

        self._excl_list = QListWidget()
        self._excl_list.setFixedHeight(120)
        for p in settings.get("excluded_paths", []):
            self._excl_list.addItem(p)
        layout.addWidget(self._excl_list)

        excl_btns = QHBoxLayout()
        add_btn = QPushButton("+ Add Folder")
        add_btn.setObjectName("SecondaryBtn")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._add_exclusion)
        rem_btn = QPushButton("Remove Selected")
        rem_btn.setObjectName("SecondaryBtn")
        rem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rem_btn.clicked.connect(self._remove_exclusion)
        excl_btns.addWidget(add_btn)
        excl_btns.addWidget(rem_btn)
        excl_btns.addStretch()
        layout.addLayout(excl_btns)

        # ── Reset ─────────────────────────────────────────────
        layout.addWidget(_section("Reset"))
        layout.addWidget(_divider())
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.setObjectName("SecondaryBtn")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.clicked.connect(self._reset_defaults)
        layout.addWidget(reset_btn)

        layout.addStretch()

    def _add_exclusion(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder to exclude", str(Path.home()))
        if folder:
            # Avoid duplicates
            existing = [self._excl_list.item(i).text() for i in range(self._excl_list.count())]
            if folder not in existing:
                self._excl_list.addItem(folder)
                self._save_exclusions()

    def _remove_exclusion(self):
        for item in self._excl_list.selectedItems():
            self._excl_list.takeItem(self._excl_list.row(item))
        self._save_exclusions()

    def _save_exclusions(self):
        paths = [self._excl_list.item(i).text() for i in range(self._excl_list.count())]
        settings.set("excluded_paths", paths)

    def _reset_defaults(self):
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Reset Settings",
            "Reset all settings to defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            from config.settings import DEFAULTS
            for k, v in DEFAULTS.items():
                settings.set(k, v)
            # Reload page
            self._dark_chk.setChecked(settings.get("dark_mode", True))
            self._scan_chk.setChecked(settings.get("scan_on_startup", False))
            self._remind_chk.setChecked(settings.get("auto_monthly_reminder", True))
            self._dl_spin.setValue(settings.get("downloads_age_days", 30))
            self._log_spin.setValue(settings.get("log_retention_days", 90))
            self._excl_list.clear()
