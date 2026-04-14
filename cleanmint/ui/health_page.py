"""
ui/health_page.py — System Health UI
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QProgressBar, QSizePolicy,
    QDialog, QTextEdit, QDialogButtonBox, QMessageBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont

from ui.theme import Theme
from core.health import HealthChecker, HealthCheck
from core.icon_doctor import BrokenIconApp, scan_broken_icons, fix_icon


class HealthWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def run(self):
        try:
            checker = HealthChecker(progress_callback=lambda m, p: self.progress.emit(m, p))
            self.finished.emit(checker.run_all())
        except Exception as e:
            self.error.emit(str(e))


class AptUpgradeWorker(QThread):
    line_ready = pyqtSignal(str)
    finished   = pyqtSignal(bool, str)   # success, summary

    def run(self):
        import subprocess
        env = {"DEBIAN_FRONTEND": "noninteractive", "PATH": "/usr/bin:/bin"}
        steps = [
            (["pkexec", "/usr/bin/apt-get", "update", "-y"],          "Updating package lists…"),
            (["pkexec", "/usr/bin/apt-get", "upgrade", "-y",
              "-o", "Dpkg::Options::=--force-confdef",
              "-o", "Dpkg::Options::=--force-confold"], "Upgrading packages…"),
        ]
        import os
        full_env = {**os.environ, **env}
        for cmd, label in steps:
            self.line_ready.emit(f"\n── {label} ──\n")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=full_env,
                )
                for line in proc.stdout:
                    self.line_ready.emit(line.rstrip())
                proc.wait()
                if proc.returncode != 0:
                    self.finished.emit(False, f"Command failed (exit {proc.returncode})")
                    return
            except Exception as e:
                self.finished.emit(False, str(e))
                return
        self.finished.emit(True, "All packages updated successfully.")


class AptUpgradeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Updating & Upgrading Packages")
        self.setMinimumSize(680, 480)
        self.setStyleSheet(Theme.stylesheet())
        self._worker = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        self._status = QLabel("Starting…")
        self._status.setObjectName("MutedLabel")
        lay.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate
        self._progress.setFixedHeight(6)
        lay.addWidget(self._progress)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setObjectName("Card")
        self._output.setFont(QFont("Monospace", 10))
        lay.addWidget(self._output, 1)

        self._btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._btns.setEnabled(False)
        self._btns.rejected.connect(self.reject)
        lay.addWidget(self._btns)

    def start(self):
        self._worker = AptUpgradeWorker()
        self._worker.line_ready.connect(self._append)
        self._worker.finished.connect(self._on_done)
        self._worker.start()

    def _append(self, line: str):
        self._output.append(line)
        self._output.verticalScrollBar().setValue(
            self._output.verticalScrollBar().maximum()
        )

    def _on_done(self, success: bool, summary: str):
        self._progress.setRange(0, 100)
        self._progress.setValue(100 if success else 0)
        self._btns.setEnabled(True)
        if success:
            self._status.setText(f"Done — {summary}")
            self._status.setStyleSheet(f"color: {Theme.p().success};")
        else:
            self._status.setText(f"Failed — {summary}")
            self._status.setStyleSheet(f"color: {Theme.p().danger};")


class HealthRow(QFrame):
    def __init__(self, check: HealthCheck):
        super().__init__()
        self.setObjectName("Card")
        self.setMinimumHeight(64)

        p = Theme.p()
        status_colours = {
            "ok":       (p.success,  "✓"),
            "warning":  (p.warning,  "⚠"),
            "critical": (p.danger,   "✕"),
            "info":     (p.info,     "ℹ"),
        }
        colour, icon = status_colours.get(check.status, (p.text_muted, "?"))

        h = QHBoxLayout(self)
        h.setContentsMargins(16, 12, 16, 12)
        h.setSpacing(14)

        # Status icon
        icon_lbl = QLabel(icon)
        icon_lbl.setFixedSize(28, 28)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet(
            f"background: {colour}22; color: {colour};"
            f"border-radius: 14px; font-size: 14px; font-weight: 700;"
        )
        h.addWidget(icon_lbl)

        # Title + detail
        info = QVBoxLayout()
        info.setSpacing(2)
        title_lbl = QLabel(check.title)
        title_lbl.setFont(QFont("Inter", 12, QFont.Weight.Medium))
        detail_lbl = QLabel(check.detail)
        detail_lbl.setObjectName("MutedLabel")
        detail_lbl.setWordWrap(True)
        info.addWidget(title_lbl)
        info.addWidget(detail_lbl)
        h.addLayout(info, 1)

        # Action buttons
        if check.id == "failed_services" and check.services:
            self._add_service_buttons(h, check)
        elif check.id == "updates" and check.fix_label:
            fix_btn = QPushButton(check.fix_label)
            fix_btn.setObjectName("SecondaryBtn")
            fix_btn.setFixedHeight(30)
            fix_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            fix_btn.clicked.connect(lambda: self._show_apt_upgrade_dialog())
            h.addWidget(fix_btn)
        elif check.fix_cmd and check.fix_label:
            fix_btn = QPushButton(check.fix_label)
            fix_btn.setObjectName("SecondaryBtn")
            fix_btn.setFixedHeight(30)
            fix_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            fix_btn.setToolTip(" ".join(check.fix_cmd))
            fix_btn.clicked.connect(lambda: self._show_terminal_cmd(check.fix_cmd))
            h.addWidget(fix_btn)

    def _add_service_buttons(self, layout: QHBoxLayout, check: HealthCheck):
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)

        status_btn = QPushButton("Show Status")
        status_btn.setObjectName("SecondaryBtn")
        status_btn.setFixedHeight(26)
        status_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        status_btn.clicked.connect(lambda: self._show_service_status(check.services))
        btn_col.addWidget(status_btn)

        restart_btn = QPushButton("Restart Services")
        restart_btn.setObjectName("SecondaryBtn")
        restart_btn.setFixedHeight(26)
        restart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        restart_btn.clicked.connect(lambda: self._restart_services(check.services))
        btn_col.addWidget(restart_btn)

        layout.addLayout(btn_col)

    def _show_service_status(self, services: list):
        import subprocess
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox
        from ui.theme import Theme
        dlg = QDialog(self.window())
        dlg.setWindowTitle("Service Status")
        dlg.setMinimumSize(600, 400)
        dlg.setStyleSheet(Theme.stylesheet())
        lay = QVBoxLayout(dlg)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setObjectName("Card")
        output = []
        for svc in services:
            try:
                r = subprocess.run(
                    ["systemctl", "status", svc, "--no-pager", "-l"],
                    capture_output=True, text=True, timeout=10
                )
                output.append(f"━━━ {svc} ━━━\n{r.stdout or r.stderr}\n")
            except Exception as e:
                output.append(f"━━━ {svc} ━━━\nError: {e}\n")
        text.setPlainText("\n".join(output))
        lay.addWidget(text)

        note = QLabel(
            "These are systemd services that failed to start. Most are background "
            "daemons — if you don't notice any problems, they can be safely ignored or restarted."
        )
        note.setWordWrap(True)
        note.setObjectName("MutedLabel")
        lay.addWidget(note)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        dlg.exec()

    def _restart_services(self, services: list):
        import subprocess
        from PyQt6.QtWidgets import QMessageBox
        names = "\n  • ".join(services)
        confirm = QMessageBox(self.window())
        confirm.setWindowTitle("Restart Services")
        confirm.setIcon(QMessageBox.Icon.Question)
        confirm.setText(
            f"Restart the following failed service(s)?\n\n  • {names}\n\n"
            "This is safe — it simply asks systemd to try starting them again. "
            "Your password will be required once."
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return

        errors = []
        for svc in services:
            try:
                r = subprocess.run(
                    ["pkexec", "/usr/local/lib/cleanmint/cleanmint-helper",
                     "systemctl-restart", svc],
                    capture_output=True, text=True, timeout=30
                )
                if r.returncode != 0:
                    errors.append(f"{svc}: {r.stderr.strip()[:80]}")
            except Exception as e:
                errors.append(f"{svc}: {e}")

        result = QMessageBox(self.window())
        if errors:
            result.setIcon(QMessageBox.Icon.Warning)
            result.setText("Some services could not be restarted:\n\n" + "\n".join(errors))
        else:
            result.setIcon(QMessageBox.Icon.Information)
            result.setText(
                f"Restarted {len(services)} service(s) successfully.\n\n"
                "Click Refresh to update the health check."
            )
        result.exec()

    def _show_apt_upgrade_dialog(self):
        confirm = QMessageBox(self.window())
        confirm.setWindowTitle("Update & Upgrade Packages")
        confirm.setIcon(QMessageBox.Icon.Question)
        confirm.setText(
            "<b>Update and upgrade all packages?</b><br><br>"
            "This will run <code>apt update</code> then <code>apt upgrade</code>.<br><br>"
            "<b>What happens:</b><br>"
            "• Package lists are refreshed from Ubuntu's servers<br>"
            "• Installed packages are updated to latest stable versions<br>"
            "• Your configuration files and data are NOT changed<br>"
            "• Ubuntu version is NOT changed<br><br>"
            "Your password will be required once. This is safe on Ubuntu LTS."
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        confirm.button(QMessageBox.StandardButton.Ok).setText("Update Now")
        if confirm.exec() != QMessageBox.StandardButton.Ok:
            return

        dlg = AptUpgradeDialog(self.window())
        dlg.show()
        dlg.start()

    def _show_terminal_cmd(self, cmd: list):
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(self.window())
        msg.setWindowTitle("Run in Terminal")
        msg.setText(f"Run the following command in a terminal:\n\n  {' '.join(cmd)}")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.exec()


# ── Icon Doctor workers ────────────────────────────────────────────────────────

class IconScanWorker(QThread):
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def run(self):
        try:
            self.finished.emit(scan_broken_icons())
        except Exception as e:
            self.error.emit(str(e))


class IconFixWorker(QThread):
    progress = pyqtSignal(str, str)   # (icon_name, message)
    done     = pyqtSignal(str, bool, str)  # (icon_name, success, message)

    def __init__(self, apps: list[BrokenIconApp]):
        super().__init__()
        self._apps = apps

    def run(self):
        for app in self._apps:
            self.progress.emit(app.icon_name, f"Fixing {app.name}…")
            ok, msg = fix_icon(app, progress=lambda m: self.progress.emit(app.icon_name, m))
            self.done.emit(app.icon_name, ok, msg)


# ── Icon Doctor row ────────────────────────────────────────────────────────────

class IconDoctorRow(QFrame):
    fix_requested = pyqtSignal(object)  # BrokenIconApp

    def __init__(self, app: BrokenIconApp):
        super().__init__()
        self.app = app
        self.setObjectName("Card")
        self.setFixedHeight(52)

        p = Theme.p()
        type_colours = {
            "appimage": ("#a78bfa", "AppImage"),
            "snap":     (p.info,    "Snap"),
            "flatpak":  (p.accent,  "Flatpak"),
            "unknown":  (p.text_muted, "Unknown"),
        }
        colour, type_label = type_colours.get(app.install_type, (p.text_muted, "?"))

        h = QHBoxLayout(self)
        h.setContentsMargins(14, 0, 14, 0)
        h.setSpacing(12)

        # Warning dot
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {p.warning}; font-size: 10px;")
        dot.setFixedWidth(14)
        h.addWidget(dot)

        # App name
        name_lbl = QLabel(app.name)
        name_lbl.setFont(QFont("Inter", 11, QFont.Weight.Medium))
        name_lbl.setMinimumWidth(160)
        h.addWidget(name_lbl)

        # Icon name (muted)
        icon_lbl = QLabel(app.icon_name)
        icon_lbl.setObjectName("MutedLabel")
        icon_lbl.setFont(QFont("Inter", 10))
        h.addWidget(icon_lbl, 1)

        # Type badge
        badge = QLabel(type_label)
        badge.setFixedHeight(20)
        badge.setContentsMargins(8, 0, 8, 0)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background: {colour}22; color: {colour};"
            f"border-radius: 10px; font-size: 10px; font-weight: 600;"
        )
        h.addWidget(badge)

        # Status label (updated during fix)
        self._status = QLabel("")
        self._status.setObjectName("MutedLabel")
        self._status.setFont(QFont("Inter", 10))
        self._status.setFixedWidth(180)
        h.addWidget(self._status)

        # Fix button
        self._fix_btn = QPushButton("Fix")
        self._fix_btn.setObjectName("SecondaryBtn")
        self._fix_btn.setFixedSize(56, 26)
        self._fix_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if app.install_type == "unknown":
            self._fix_btn.setEnabled(False)
            self._fix_btn.setToolTip("Cannot auto-fix: unknown install type")
        self._fix_btn.clicked.connect(lambda: self.fix_requested.emit(self.app))
        h.addWidget(self._fix_btn)

    def set_fixing(self, msg: str = ""):
        self._fix_btn.setEnabled(False)
        self._fix_btn.setText("…")
        if msg:
            self._status.setText(msg[:30])

    def set_result(self, success: bool, msg: str):
        p = Theme.p()
        if success:
            self._fix_btn.setText("✓")
            self._fix_btn.setStyleSheet(f"color: {p.success};")
            self._status.setText("Fixed")
            self._status.setStyleSheet(f"color: {p.success};")
        else:
            self._fix_btn.setText("✕")
            self._fix_btn.setStyleSheet(f"color: {p.danger};")
            short = msg[:40] if len(msg) > 40 else msg
            self._status.setText(short)
            self._status.setStyleSheet(f"color: {p.danger};")
            self._status.setToolTip(msg)


# ── Icon Doctor section ────────────────────────────────────────────────────────

class IconDoctorSection(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("Card")
        self._rows: list[IconDoctorRow] = []
        self._scan_worker: IconScanWorker | None = None
        self._fix_worker: IconFixWorker | None = None
        self._build_ui()

    def _build_ui(self):
        p = Theme.p()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        # Header row
        hdr = QHBoxLayout()
        hdr.setSpacing(10)

        icon_lbl = QLabel("🔍")
        icon_lbl.setFixedWidth(24)
        hdr.addWidget(icon_lbl)

        title = QLabel("Icon Doctor")
        title.setFont(QFont("Inter", 13, QFont.Weight.Bold))
        hdr.addWidget(title)

        sub = QLabel("— finds apps missing their icons and fixes them automatically")
        sub.setObjectName("MutedLabel")
        sub.setFont(QFont("Inter", 11))
        hdr.addWidget(sub, 1)

        self._fix_all_btn = QPushButton("Fix All")
        self._fix_all_btn.setObjectName("SecondaryBtn")
        self._fix_all_btn.setFixedHeight(30)
        self._fix_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fix_all_btn.setVisible(False)
        self._fix_all_btn.clicked.connect(self._fix_all)
        hdr.addWidget(self._fix_all_btn)

        self._scan_btn = QPushButton("Scan Icons")
        self._scan_btn.setObjectName("SecondaryBtn")
        self._scan_btn.setFixedHeight(30)
        self._scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scan_btn.clicked.connect(self._start_scan)
        hdr.addWidget(self._scan_btn)

        outer.addLayout(hdr)

        # Status line
        self._status = QLabel("Click Scan Icons to check for missing app icons.")
        self._status.setObjectName("MutedLabel")
        outer.addWidget(self._status)

        # Results container (hidden until scan)
        self._results = QWidget()
        self._results_layout = QVBoxLayout(self._results)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        self._results_layout.setSpacing(6)
        self._results.setVisible(False)
        outer.addWidget(self._results)

    def _start_scan(self):
        if self._scan_worker and self._scan_worker.isRunning():
            return
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText("Scanning…")
        self._fix_all_btn.setVisible(False)
        self._status.setText("Scanning installed apps…")
        self._clear_rows()
        self._results.setVisible(False)

        self._scan_worker = IconScanWorker()
        self._scan_worker.finished.connect(self._on_scan_done)
        self._scan_worker.error.connect(lambda e: self._status.setText(f"Error: {e}"))
        self._scan_worker.finished.connect(lambda: (
            self._scan_btn.setEnabled(True),
            self._scan_btn.setText("Scan Icons"),
        ))
        self._scan_worker.start()

    def _on_scan_done(self, apps: list[BrokenIconApp]):
        p = Theme.p()
        if not apps:
            self._status.setText("All app icons are correctly installed.")
            self._status.setStyleSheet(f"color: {p.success};")
            return

        fixable = [a for a in apps if a.install_type != "unknown"]
        self._status.setText(
            f"{len(apps)} app(s) with missing icons"
            + (f" · {len(fixable)} can be auto-fixed" if fixable else "")
        )
        self._status.setStyleSheet("")

        for app in apps:
            row = IconDoctorRow(app)
            row.fix_requested.connect(self._fix_one)
            self._results_layout.addWidget(row)
            self._rows.append(row)

        self._results.setVisible(True)
        if fixable:
            self._fix_all_btn.setVisible(True)

    def _fix_one(self, app: BrokenIconApp):
        row = next((r for r in self._rows if r.app.icon_name == app.icon_name), None)
        if row:
            row.set_fixing()
        self._run_fix([app])

    def _fix_all(self):
        fixable = [r.app for r in self._rows if r.app.install_type != "unknown"]
        for r in self._rows:
            if r.app.install_type != "unknown":
                r.set_fixing()
        self._fix_all_btn.setEnabled(False)
        self._run_fix(fixable)

    def _run_fix(self, apps: list[BrokenIconApp]):
        if self._fix_worker and self._fix_worker.isRunning():
            return
        self._fix_worker = IconFixWorker(apps)
        self._fix_worker.progress.connect(self._on_fix_progress)
        self._fix_worker.done.connect(self._on_fix_done)
        self._fix_worker.finished.connect(self._on_all_fixes_done)
        self._fix_worker.start()

    def _on_all_fixes_done(self):
        self._fix_all_btn.setEnabled(True)
        # Re-enable Fix buttons on rows that haven't been fixed yet
        for row in self._rows:
            if row.app.install_type != "unknown" and row._fix_btn.text() not in ("✓", "✕"):
                row._fix_btn.setEnabled(True)
                row._fix_btn.setText("Fix")

    def _on_fix_progress(self, icon_name: str, msg: str):
        row = next((r for r in self._rows if r.app.icon_name == icon_name), None)
        if row:
            row.set_fixing(msg)

    def _on_fix_done(self, icon_name: str, success: bool, msg: str):
        row = next((r for r in self._rows if r.app.icon_name == icon_name), None)
        if row:
            row.set_result(success, msg)

    def _clear_rows(self):
        for row in self._rows:
            row.deleteLater()
        self._rows.clear()


class HealthPage(QWidget):
    def __init__(self):
        super().__init__()
        self._worker = None
        self._rows: list[HealthRow] = []
        self._build_ui()
        QTimer.singleShot(500, self._start_check)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)

        hdr = QHBoxLayout()
        title = QLabel("System Health")
        title.setObjectName("TitleLabel")
        hdr.addWidget(title)
        hdr.addStretch()
        self._refresh_btn = QPushButton("↺  Refresh")
        self._refresh_btn.setObjectName("SecondaryBtn")
        self._refresh_btn.setFixedHeight(34)
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self._start_check)
        hdr.addWidget(self._refresh_btn)
        outer.addLayout(hdr)

        self._status = QLabel("Running health checks…")
        self._status.setObjectName("MutedLabel")
        outer.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setFixedHeight(6)
        outer.addWidget(self._progress)

        # Score card
        self._score_card = QFrame()
        self._score_card.setObjectName("Card")
        score_lay = QHBoxLayout(self._score_card)
        score_lay.setContentsMargins(20, 16, 20, 16)
        self._score_lbl = QLabel("—")
        self._score_lbl.setFont(QFont("Inter", 32, QFont.Weight.Bold))
        self._score_lbl.setStyleSheet(f"color: {Theme.p().accent};")
        score_lay.addWidget(self._score_lbl)
        score_info = QVBoxLayout()
        score_info.addWidget(QLabel("Health Score"))
        self._score_sub = QLabel("out of 100")
        self._score_sub.setObjectName("MutedLabel")
        score_info.addWidget(self._score_sub)
        score_lay.addLayout(score_info)
        score_lay.addStretch()
        outer.addWidget(self._score_card)

        # Scrollable check list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(8)
        self._list_layout.addStretch()
        scroll.setWidget(self._list_container)
        outer.addWidget(scroll, 1)

        # Icon Doctor section
        self._icon_doctor = IconDoctorSection()
        outer.addWidget(self._icon_doctor)

    def _start_check(self):
        if self._worker and self._worker.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._progress.setValue(0)
        self._status.setText("Running checks…")
        self._clear_rows()

        self._worker = HealthWorker()
        self._worker.progress.connect(lambda m, p: (self._status.setText(m), self._progress.setValue(p)))
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(lambda e: self._status.setText(f"Error: {e}"))
        self._worker.finished.connect(lambda: self._refresh_btn.setEnabled(True))
        self._worker.start()

    def _on_done(self, checks: list):
        self._clear_rows()
        insert_at = self._list_layout.count() - 1

        ok_count = sum(1 for c in checks if c.status in ("ok", "info"))
        score = int(ok_count / max(len(checks), 1) * 100)
        self._score_lbl.setText(str(score))
        colour = Theme.p().success if score >= 80 else Theme.p().warning if score >= 50 else Theme.p().danger
        self._score_lbl.setStyleSheet(f"color: {colour};")
        self._score_sub.setText(f"{ok_count}/{len(checks)} checks passed")

        issues = [c for c in checks if c.status in ("warning", "critical")]
        ok_checks = [c for c in checks if c.status not in ("warning", "critical")]
        for check in issues + ok_checks:
            row = HealthRow(check)
            self._list_layout.insertWidget(insert_at, row)
            self._rows.append(row)
            insert_at += 1

        warnings = [c for c in checks if c.status in ("warning", "critical")]
        self._status.setText(
            f"Health check complete · {len(warnings)} issue(s) found"
            if warnings else "Health check complete · All checks passed"
        )

    def _clear_rows(self):
        for row in self._rows:
            row.deleteLater()
        self._rows.clear()
