"""
ui/screenshot_page.py — Screenshot Doctor page.

Diagnoses and repairs GNOME screenshot save issues.
"""

import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from core.screenshot import DiagnosticCheck, ScreenshotDoctor, ScreenshotStatus, SCREENSHOTS_DIR
from ui.theme import Theme


# ------------------------------------------------------------------ #
#  Worker threads                                                      #
# ------------------------------------------------------------------ #

class DiagnoseWorker(QThread):
    finished = pyqtSignal(object)   # ScreenshotStatus

    def run(self):
        self.finished.emit(ScreenshotDoctor().diagnose())


class FixWorker(QThread):
    finished = pyqtSignal(list)     # list[tuple[name, ok, msg]]

    def __init__(self, checks: list[DiagnosticCheck]):
        super().__init__()
        self._checks = checks

    def run(self):
        self.finished.emit(ScreenshotDoctor().apply_all_fixes(self._checks))


class SingleFixWorker(QThread):
    finished = pyqtSignal(bool, str, str)   # ok, msg, fix_key

    def __init__(self, fix_key: str):
        super().__init__()
        self._key = fix_key

    def run(self):
        ok, msg = ScreenshotDoctor().apply_fix(self._key)
        self.finished.emit(ok, msg, self._key)


# ------------------------------------------------------------------ #
#  Check row widget                                                    #
# ------------------------------------------------------------------ #

_STATUS_COLORS = {
    "ok":   "#4ade80",
    "warn": "#fbbf24",
    "fail": "#f87171",
}
_STATUS_ICONS = {
    "ok":   "✔",
    "warn": "⚠",
    "fail": "✘",
}


class CheckRow(QFrame):
    fix_requested = pyqtSignal(str)   # emits fix_key

    def __init__(self, check: DiagnosticCheck, parent=None):
        super().__init__(parent)
        self.setObjectName("CheckRow")
        self._check = check

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        # Status dot
        color = _STATUS_COLORS.get(check.status, "#888")
        icon = _STATUS_ICONS.get(check.status, "?")
        dot = QLabel(icon)
        dot.setFixedWidth(20)
        dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dot.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: bold;")
        layout.addWidget(dot)

        # Name + detail
        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        name_lbl = QLabel(check.name)
        name_lbl.setFont(QFont("Inter", 10, QFont.Weight.Medium))
        text_col.addWidget(name_lbl)

        detail_lbl = QLabel(check.detail)
        detail_lbl.setObjectName("MutedLabel")
        detail_lbl.setWordWrap(True)
        detail_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        text_col.addWidget(detail_lbl)

        layout.addLayout(text_col, 1)

        # Fix button
        if check.fix_available:
            self._fix_btn = QPushButton("Fix")
            self._fix_btn.setObjectName("SecondaryBtn")
            self._fix_btn.setFixedWidth(64)
            self._fix_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._fix_btn.clicked.connect(lambda: self.fix_requested.emit(check.fix_key))
            layout.addWidget(self._fix_btn)

    def mark_fixed(self):
        color = _STATUS_COLORS["ok"]
        icon = _STATUS_ICONS["ok"]
        # update dot
        dot = self.findChild(QLabel)
        if dot:
            dot.setText(icon)
            dot.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: bold;")
        if hasattr(self, "_fix_btn"):
            self._fix_btn.setEnabled(False)
            self._fix_btn.setText("Fixed")


# ------------------------------------------------------------------ #
#  Main page                                                           #
# ------------------------------------------------------------------ #

class ScreenshotPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._status: ScreenshotStatus | None = None
        self._check_rows: list[CheckRow] = []
        self._worker: QThread | None = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(0)

        # ── Header ──────────────────────────────────────────────── #
        title = QLabel("Screenshot Doctor")
        title.setObjectName("TitleLabel")
        title.setFont(QFont("Inter", 18, QFont.Weight.Bold))
        root.addWidget(title)

        sub = QLabel(
            "Diagnoses why screenshots aren't being saved and fixes the issue automatically."
        )
        sub.setObjectName("SubtitleLabel")
        sub.setWordWrap(True)
        root.addWidget(sub)
        root.addSpacing(20)

        # ── Action buttons ───────────────────────────────────────── #
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._diagnose_btn = QPushButton("▶  Run Diagnosis")
        self._diagnose_btn.setObjectName("PrimaryBtn")
        self._diagnose_btn.setFixedHeight(36)
        self._diagnose_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._diagnose_btn.clicked.connect(self._run_diagnose)
        btn_row.addWidget(self._diagnose_btn)

        self._fix_all_btn = QPushButton("⚡  Fix All Issues")
        self._fix_all_btn.setObjectName("SecondaryBtn")
        self._fix_all_btn.setFixedHeight(36)
        self._fix_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fix_all_btn.setEnabled(False)
        self._fix_all_btn.clicked.connect(self._fix_all)
        btn_row.addWidget(self._fix_all_btn)

        self._open_btn = QPushButton("⊞  Open Screenshots Folder")
        self._open_btn.setObjectName("SecondaryBtn")
        self._open_btn.setFixedHeight(36)
        self._open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_btn.clicked.connect(self._open_folder)
        btn_row.addWidget(self._open_btn)

        btn_row.addStretch()
        root.addLayout(btn_row)
        root.addSpacing(16)

        # ── Status label ─────────────────────────────────────────── #
        self._status_lbl = QLabel("")
        self._status_lbl.setObjectName("MutedLabel")
        root.addWidget(self._status_lbl)
        root.addSpacing(12)

        # ── Checks list (scrollable) ──────────────────────────────── #
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self._list_layout.addStretch()

        scroll.setWidget(self._list_widget)
        root.addWidget(scroll, 1)

        # ── Lost files section ────────────────────────────────────── #
        self._lost_frame = QFrame()
        self._lost_frame.setVisible(False)
        lost_layout = QVBoxLayout(self._lost_frame)
        lost_layout.setContentsMargins(0, 12, 0, 0)
        lost_layout.setSpacing(4)

        lost_title = QLabel("Screenshots found outside folder:")
        lost_title.setFont(QFont("Inter", 10, QFont.Weight.Medium))
        lost_layout.addWidget(lost_title)

        self._lost_list_lbl = QLabel()
        self._lost_list_lbl.setObjectName("MutedLabel")
        self._lost_list_lbl.setWordWrap(True)
        lost_layout.addWidget(self._lost_list_lbl)

        root.addWidget(self._lost_frame)

    # ---------------------------------------------------------------- #
    #  Diagnosis                                                         #
    # ---------------------------------------------------------------- #

    def _run_diagnose(self):
        self._diagnose_btn.setEnabled(False)
        self._diagnose_btn.setText("Running…")
        self._fix_all_btn.setEnabled(False)
        self._status_lbl.setText("Scanning…")
        self._clear_rows()

        self._worker = DiagnoseWorker()
        self._worker.finished.connect(self._on_diagnose_done)
        self._worker.start()

    def _on_diagnose_done(self, status: ScreenshotStatus):
        self._status = status
        self._diagnose_btn.setEnabled(True)
        self._diagnose_btn.setText("▶  Run Diagnosis")

        self._clear_rows()
        self._check_rows = []

        has_fixable = False
        for check in status.checks:
            row = CheckRow(check)
            row.fix_requested.connect(self._fix_single)
            # insert before the stretch
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)
            self._check_rows.append(row)
            if check.fix_available:
                has_fixable = True

        self._fix_all_btn.setEnabled(has_fixable)

        fail_count = sum(1 for c in status.checks if c.status == "fail")
        warn_count = sum(1 for c in status.checks if c.status == "warn")
        if fail_count == 0 and warn_count == 0:
            self._status_lbl.setText("All checks passed — screenshots should save correctly.")
        else:
            parts = []
            if fail_count:
                parts.append(f"{fail_count} issue(s) found")
            if warn_count:
                parts.append(f"{warn_count} warning(s)")
            self._status_lbl.setText(", ".join(parts) + ". Click Fix or Fix All Issues.")

        if status.lost_files:
            self._lost_list_lbl.setText("\n".join(str(f) for f in status.lost_files[:10]))
            self._lost_frame.setVisible(True)
        else:
            self._lost_frame.setVisible(False)

    def _clear_rows(self):
        while self._list_layout.count() > 1:  # keep the trailing stretch
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._check_rows = []

    # ---------------------------------------------------------------- #
    #  Fix all                                                           #
    # ---------------------------------------------------------------- #

    def _fix_all(self):
        if not self._status:
            return
        self._fix_all_btn.setEnabled(False)
        self._fix_all_btn.setText("Fixing…")
        self._status_lbl.setText("Applying fixes…")

        self._worker = FixWorker(self._status.checks)
        self._worker.finished.connect(self._on_fix_all_done)
        self._worker.start()

    def _on_fix_all_done(self, results: list[tuple[str, bool, str]]):
        self._fix_all_btn.setText("⚡  Fix All Issues")
        msgs = []
        patched_ext = False
        for name, ok, msg in results:
            icon = "✔" if ok else "✘"
            msgs.append(f"{icon} {name}: {msg}")
            if ok and "Patched" in msg:
                patched_ext = True
        summary = "  |  ".join(msgs) if msgs else "Nothing to fix."
        if patched_ext:
            summary += "  —  Log out and back in for extension patches to fully take effect."
        self._status_lbl.setText(summary)
        # Re-run diagnosis to refresh state
        self._run_diagnose()

    # ---------------------------------------------------------------- #
    #  Fix single                                                        #
    # ---------------------------------------------------------------- #

    def _fix_single(self, fix_key: str):
        self._status_lbl.setText(f"Applying fix: {fix_key}…")
        self._worker = SingleFixWorker(fix_key)
        self._worker.finished.connect(self._on_fix_single_done)
        self._worker.start()

    def _on_fix_single_done(self, ok: bool, msg: str, fix_key: str):
        icon = "✔" if ok else "✘"
        text = f"{icon} {msg}"
        if ok and fix_key == "fix_extension_compat":
            text += "  —  Log out and back in for the patch to fully take effect."
        self._status_lbl.setText(text)
        self._run_diagnose()

    # ---------------------------------------------------------------- #
    #  Open folder                                                       #
    # ---------------------------------------------------------------- #

    def _open_folder(self):
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", str(SCREENSHOTS_DIR)])
        except Exception:
            pass
