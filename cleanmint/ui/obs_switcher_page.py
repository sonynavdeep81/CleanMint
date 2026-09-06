"""
ui/obs_switcher_page.py — OBS laptop⇄tablet switcher setup, protection & test.
"""

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import obs_switcher as ob
from ui.theme import Theme


class Worker(QThread):
    progress = pyqtSignal(str, int)
    done = pyqtSignal(object)
    fail = pyqtSignal(str)

    def __init__(self, fn, *args, wants_progress=False):
        super().__init__()
        self._fn = fn
        self._args = args
        self._wants_progress = wants_progress

    def run(self):
        try:
            if self._wants_progress:
                res = self._fn(
                    *self._args,
                    progress_cb=lambda m, p: self.progress.emit(m, p),
                )
            else:
                res = self._fn(*self._args)
            self.done.emit(res)
        except Exception as e:  # noqa: BLE001
            self.fail.emit(str(e))


class RestoreDialog(QDialog):
    def __init__(self, items: list[ob.VerifyItem], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Check & Restore")
        self.setMinimumWidth(460)
        self.setStyleSheet(Theme.stylesheet())
        self._boxes: list[tuple[QCheckBox, str]] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(10)

        title = QLabel("Comparison with the most recent backup:")
        title.setObjectName("TitleLabel")
        lay.addWidget(title)

        icon = {"ok": "✓", "changed": "!", "missing": "✗"}
        for it in items:
            box = QCheckBox(
                f"{icon.get(it.status, '?')}  {it.label} — {it.status}")
            restorable = it.status in ("changed", "missing") and it.can_restore
            box.setEnabled(restorable)
            box.setChecked(restorable)
            lay.addWidget(box)
            self._boxes.append((box, it.label))

        btns = QDialogButtonBox()
        btns.addButton("Restore Selected",
                       QDialogButtonBox.ButtonRole.AcceptRole)
        btns.addButton(QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def selected_labels(self) -> list[str]:
        return [label for box, label in self._boxes
                if box.isChecked() and box.isEnabled()]


class TestReportDialog(QDialog):
    def __init__(self, steps: list[ob.StepResult], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Test Switching")
        self.setMinimumWidth(460)
        self.setStyleSheet(Theme.stylesheet())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(8)

        all_ok = all(s.ok for s in steps)
        head = QLabel("✓  All checks passed" if all_ok
                      else "✗  Something needs attention")
        head.setObjectName("TitleLabel")
        lay.addWidget(head)

        for s in steps:
            lbl = QLabel(f"{'✓' if s.ok else '✗'}  {s.label}"
                         + (f" — {s.detail}" if s.detail else ""))
            lbl.setWordWrap(True)
            lay.addWidget(lbl)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        lay.addWidget(btns)


class ObsSwitcherPage(QWidget):
    def __init__(self):
        super().__init__()
        self._worker: QThread | None = None
        self._checks: list[ob.Check] = []
        self._build_ui()
        QTimer.singleShot(200, self._refresh)

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        p = Theme.p()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        hdr = QHBoxLayout()
        title = QLabel("OBS Switcher")
        title.setObjectName("TitleLabel")
        hdr.addWidget(title)
        hdr.addStretch()
        self._refresh_btn = QPushButton("⟳  Refresh")
        self._refresh_btn.setObjectName("SecondaryBtn")
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.setFixedHeight(30)
        self._refresh_btn.clicked.connect(self._refresh)
        hdr.addWidget(self._refresh_btn)
        layout.addLayout(hdr)

        sub = QLabel(
            "Build, protect, back up and test the Ctrl+Alt+1 / Ctrl+Alt+2 "
            "OBS scene-switching setup for your laptop and Samsung tablet. "
            "Your OBS scenes are never modified."
        )
        sub.setObjectName("SubtitleLabel")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

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

        # action bar
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._build_btn = QPushButton("⚙  Build / Repair Setup")
        self._build_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._build_btn.setFixedHeight(34)
        self._build_btn.setStyleSheet(
            f"QPushButton {{ background: {p.accent}; color: {p.accent_text};"
            f"  border: none; border-radius: 6px; font-weight: 600;"
            f"  padding: 0 16px; }}"
            f"QPushButton:hover {{ background: {p.accent_hover}; }}"
            f"QPushButton:disabled {{ background: {p.accent}55; }}"
        )
        self._build_btn.clicked.connect(self._build)
        bar.addWidget(self._build_btn)

        self._lock_btn = QPushButton("🔒  Protect Files")
        self._lock_btn.setObjectName("SecondaryBtn")
        self._lock_btn.setFixedHeight(34)
        self._lock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lock_btn.clicked.connect(self._toggle_lock)
        bar.addWidget(self._lock_btn)

        self._backup_btn = QPushButton("💾  Back Up Now")
        self._backup_btn.setObjectName("SecondaryBtn")
        self._backup_btn.setFixedHeight(34)
        self._backup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._backup_btn.clicked.connect(self._backup)
        bar.addWidget(self._backup_btn)

        self._restore_btn = QPushButton("🔍  Check & Restore")
        self._restore_btn.setObjectName("SecondaryBtn")
        self._restore_btn.setFixedHeight(34)
        self._restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restore_btn.clicked.connect(self._check_restore)
        bar.addWidget(self._restore_btn)

        self._test_btn = QPushButton("▶  Test Switching")
        self._test_btn.setObjectName("SecondaryBtn")
        self._test_btn.setFixedHeight(34)
        self._test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test_btn.clicked.connect(self._test)
        bar.addWidget(self._test_btn)

        bar.addStretch()
        layout.addLayout(bar)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Check", "Detail"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self._table, 1)

    # ── helpers ──────────────────────────────────────────────────────────
    def _set_busy(self, busy: bool):
        for b in (self._build_btn, self._lock_btn, self._backup_btn,
                  self._restore_btn, self._test_btn, self._refresh_btn):
            b.setEnabled(not busy)

    def _set_status(self, msg: str):
        self._status.setText(msg)
        self._status.setVisible(bool(msg))

    def _run_worker(self, fn, *args, wants_progress=False, on_done=None):
        self._set_busy(True)
        w = Worker(fn, *args, wants_progress=wants_progress)

        def _on_progress(m, pct):
            self._progress.show()
            self._progress.setValue(pct)
            self._set_status(m)

        def _finish(res):
            self._set_busy(False)
            self._progress.hide()
            self._set_status("")
            if on_done:
                on_done(res)

        w.progress.connect(_on_progress)
        w.done.connect(_finish)
        w.fail.connect(lambda e: (_finish(None),
                                  QMessageBox.warning(self, "Error", e)))
        w.start()
        self._worker = w

    # ── actions ──────────────────────────────────────────────────────────
    def _refresh(self):
        self._run_worker(ob.check_status, on_done=self._populate)

    def _populate(self, checks):
        if checks is None:
            return
        p = Theme.p()
        self._checks = checks
        self._table.setRowCount(0)
        for c in checks:
            row = self._table.rowCount()
            self._table.insertRow(row)
            if c.key in ("session", "protection", "backup", "tablet"):
                mark, colour = "•", p.text_secondary
            elif c.ok:
                mark, colour = "✓", p.success
            elif c.fixable:
                mark, colour = "✗", p.danger
            else:
                mark, colour = "!", p.warning
            it_left = QTableWidgetItem(f"{mark}  {c.label}")
            it_right = QTableWidgetItem(c.detail)
            for it in (it_left, it_right):
                it.setForeground(QColor(colour))
            self._table.setItem(row, 0, it_left)
            self._table.setItem(row, 1, it_right)
        locked = any(c.key == "protection" and c.detail == "Locked"
                     for c in checks)
        self._lock_btn.setText("🔓  Unprotect Files" if locked
                               else "🔒  Protect Files")

    def _on_cell_clicked(self, row, _col):
        if row >= len(self._checks):
            return
        c = self._checks[row]
        if (not c.ok) and (not c.fixable) and c.manual_steps:
            QMessageBox.information(self, c.label, c.manual_steps)

    def _build(self):
        self._run_worker(ob.build, wants_progress=True,
                         on_done=self._after_build)

    def _unlock_build_relock(self, progress_cb=None):
        """Temporarily lift protection, run build, then restore protection."""
        if progress_cb:
            progress_cb("Unprotecting files…", 5)
        ok, err = ob.unlock()
        if not ok:
            res = ob.BuildResult()
            res.steps.append(ob.StepResult(
                "Unprotect files", False, err or "could not unprotect"))
            return res
        result = ob.build(progress_cb=progress_cb)
        if progress_cb:
            progress_cb("Re-protecting files…", 98)
        ob.lock()
        return result

    def _after_build(self, result):
        if result is None:
            return
        if getattr(result, "needs_unlock", False):
            if QMessageBox.question(
                self, "Files are protected",
                "The switch script and password are protected against "
                "changes.\n\nTemporarily unprotect them, run the repair, "
                "then protect them again?",
            ) == QMessageBox.StandardButton.Yes:
                self._run_worker(self._unlock_build_relock, wants_progress=True,
                                 on_done=self._after_build)
            else:
                QMessageBox.information(
                    self, "Build / Repair",
                    "Nothing changed. Click “Unprotect Files”, then "
                    "“Build / Repair Setup”.")
            return
        if result.needs_password:
            pw, ok = QInputDialog.getText(
                self, "OBS WebSocket password",
                "CleanMint could not read the password from OBS.\n"
                "Open OBS → Tools → WebSocket Server Settings → Show Connect "
                "Info, and paste the Server Password here:",
                QLineEdit.EchoMode.Password,
            )
            if ok and pw.strip():
                ob.set_password(pw.strip())
                self._run_worker(ob.build, wants_progress=True,
                                 on_done=self._after_build)
                return
        msgs = "\n".join(f"{'✓' if s.ok else '✗'}  {s.label}: {s.detail}"
                         for s in result.steps)
        if result.warnings:
            msgs += "\n\n" + "\n".join("⚠  " + w for w in result.warnings)
        box = QMessageBox.information if result.ok else QMessageBox.warning
        box(self, "Build / Repair", msgs)
        self._refresh()

    def _toggle_lock(self):
        locked = self._lock_btn.text().startswith("🔓")
        if not locked:
            if QMessageBox.question(
                self, "Protect Files",
                "This locks the switch script and password file so they "
                "cannot be changed or deleted.\n\nYou must click "
                "“Unprotect Files” here before editing them again. "
                "Continue?",
            ) != QMessageBox.StandardButton.Yes:
                return
        fn = ob.unlock if locked else ob.lock

        def _done(res):
            ok, err = res if res else (False, "cancelled")
            if not ok and err:
                QMessageBox.warning(self, "Protection", err)
            self._refresh()

        self._run_worker(fn, on_done=_done)

    def _backup(self):
        def _done(path):
            if path:
                QMessageBox.information(self, "Backup",
                                       f"Backup saved to:\n{path}")

        self._run_worker(ob.backup, wants_progress=True, on_done=_done)

    def _check_restore(self):
        def _done(items):
            if not items:
                QMessageBox.information(
                    self, "Check & Restore",
                    "No backup found yet. Click “Back Up Now” first.")
                return
            dlg = RestoreDialog(items, self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                labels = dlg.selected_labels()
                if labels:
                    self._run_worker(ob.restore, labels,
                                     on_done=self._after_restore)

        self._run_worker(ob.verify, on_done=_done)

    def _after_restore(self, results):
        if results is None:
            return
        msg = "\n".join(f"{'✓' if r.ok else '✗'}  {r.label}: {r.detail}"
                        for r in results)
        QMessageBox.information(self, "Restore", msg)
        self._refresh()

    def _test(self):
        def _done(steps):
            if steps is None:
                return
            TestReportDialog(steps, self).exec()

        self._run_worker(ob.self_test, wants_progress=True, on_done=_done)
