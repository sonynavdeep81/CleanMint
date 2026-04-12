"""
ui/cleaner_page.py — CleanMint Cleaner Page

Category list with checkboxes, risk badges, size estimates.
Dry-run preview dialog before any real deletion.
All scan/clean ops run in QThread — UI never blocks.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QCheckBox, QScrollArea, QProgressBar,
    QDialog, QTextEdit, QDialogButtonBox, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont

from ui.theme import Theme
from core.scanner import Scanner, ScanCategory, _human_size
from core.cleaner import Cleaner


# ── Workers ──────────────────────────────────────────────────────

class ScanWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def run(self):
        try:
            scanner = Scanner(progress_callback=lambda m, p: self.progress.emit(m, p))
            self.finished.emit(scanner.run_full_scan())
        except Exception as e:
            self.error.emit(str(e))


class CleanWorker(QThread):
    progress  = pyqtSignal(str, int)
    finished  = pyqtSignal(list)   # list[CleanResult]
    error     = pyqtSignal(str)

    def __init__(self, categories: list[ScanCategory], dry_run: bool):
        super().__init__()
        self._categories = categories
        self._dry_run    = dry_run

    def run(self):
        try:
            cleaner = Cleaner(
                dry_run=self._dry_run,
                progress_callback=lambda m, p: self.progress.emit(m, p),
                log_to_disk=True,
            )
            self.finished.emit(cleaner.clean_categories(self._categories))
        except Exception as e:
            self.error.emit(str(e))


# ── Dry-run preview dialog ─────────────────────────────────────

class DryRunDialog(QDialog):
    def __init__(self, results, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preview — What will be cleaned")
        self.setMinimumSize(560, 420)
        self.setStyleSheet(Theme.stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("Review before cleaning")
        title.setObjectName("TitleLabel")
        layout.addWidget(title)

        total = sum(r.freed_bytes for r in results)
        count = sum(r.deleted_count for r in results)
        summary = QLabel(
            f"{count} items will be removed · {_human_size(total)} will be freed"
        )
        summary.setObjectName("AccentLabel")
        layout.addWidget(summary)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setObjectName("Card")
        lines = []
        for r in results:
            if r.deleted_count == 0:
                continue
            lines.append(f"\n── {r.category_name} ({r.freed_human}) ──")
            for action in r.actions[:20]:   # cap at 20 per category
                lines.append(f"  {action}")
            if len(r.actions) > 20:
                lines.append(f"  … and {len(r.actions) - 20} more")
        text.setPlainText("\n".join(lines) if lines else "Nothing to clean.")
        layout.addWidget(text)

        warn = QLabel(
            "⚠  Deletion is permanent. Confirm only if you have reviewed the list above."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet(f"color: {Theme.p().warning}; font-size: 12px;")
        layout.addWidget(warn)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Clean Now")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("PrimaryBtn")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setObjectName("SecondaryBtn")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


# ── Category row widget ────────────────────────────────────────

class CategoryRow(QFrame):
    def __init__(self, cat: ScanCategory):
        super().__init__()
        self.cat = cat
        self.setObjectName("Card")
        self.setFixedHeight(70)

        h = QHBoxLayout(self)
        h.setContentsMargins(14, 0, 14, 0)
        h.setSpacing(14)

        # Checkbox
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(cat.recommended and cat.size_bytes > 0)
        self.checkbox.setEnabled(cat.size_bytes > 0)
        h.addWidget(self.checkbox)

        # Risk badge
        p = Theme.p()
        risk_map = {
            "low":    (p.risk_low_bg,    p.risk_low_fg),
            "medium": (p.risk_medium_bg, p.risk_medium_fg),
            "expert": (p.risk_expert_bg, p.risk_expert_fg),
        }
        bg, fg = risk_map.get(cat.risk, (p.bg_secondary, p.text_muted))
        badge = QLabel(cat.risk.upper())
        badge.setFixedSize(60, 22)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:4px;"
            f"font-size:10px; font-weight:700;"
        )
        h.addWidget(badge)

        # Name + description (stretches to fill available space)
        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel(cat.name)
        name.setFont(QFont("Inter", 12, QFont.Weight.Medium))
        desc = QLabel(cat.description)
        desc.setObjectName("MutedLabel")
        desc.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        desc.setWordWrap(False)
        info.addWidget(name)
        info.addWidget(desc)
        h.addLayout(info, 1)

        # Size / status — fixed width so it never gets squeezed off-screen
        right = QWidget()
        right.setFixedWidth(90)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(2)
        right_lay.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        size_lbl = QLabel(cat.size_human if cat.size_bytes > 0 else "—")
        size_lbl.setFont(QFont("Inter", 13, QFont.Weight.Bold))
        size_lbl.setStyleSheet(
            f"color: {p.accent if cat.size_bytes > 0 else p.text_muted};"
        )
        size_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        files_lbl = QLabel(f"{cat.file_count} files" if cat.file_count else "")
        files_lbl.setObjectName("MutedLabel")
        files_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        right_lay.addWidget(size_lbl)
        right_lay.addWidget(files_lbl)
        h.addWidget(right)

    def is_selected(self) -> bool:
        return self.checkbox.isChecked()


# ── Cleaner Page ───────────────────────────────────────────────

class CleanerPage(QWidget):
    def __init__(self):
        super().__init__()
        self._categories: list[ScanCategory] = []
        self._rows: list[CategoryRow] = []
        self._scan_worker: ScanWorker | None = None
        self._clean_worker: CleanWorker | None = None
        self._build_ui()
        QTimer.singleShot(400, self._start_scan)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)

        # ── Header ──────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("Cleaner")
        title.setObjectName("TitleLabel")
        header.addWidget(title)
        header.addStretch()

        self._rescan_btn = QPushButton("↺  Re-scan")
        self._rescan_btn.setObjectName("SecondaryBtn")
        self._rescan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rescan_btn.setFixedHeight(34)
        self._rescan_btn.clicked.connect(self._start_scan)
        header.addWidget(self._rescan_btn)
        outer.addLayout(header)

        self._status_label = QLabel("Scanning…")
        self._status_label.setObjectName("MutedLabel")
        outer.addWidget(self._status_label)

        # ── Progress bar ─────────────────────────────────────
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.hide()
        outer.addWidget(self._progress_bar)

        # ── Select-all row ───────────────────────────────────
        sel_row = QHBoxLayout()
        self._select_all = QCheckBox("Select all")
        self._select_all.setObjectName("ToggleSwitch")
        self._select_all.stateChanged.connect(self._on_select_all)
        sel_row.addWidget(self._select_all)
        sel_row.addStretch()
        self._total_label = QLabel("")
        self._total_label.setObjectName("AccentLabel")
        sel_row.addWidget(self._total_label)
        outer.addLayout(sel_row)

        # ── Scrollable category list ─────────────────────────
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

        # ── Action buttons ───────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self._preview_btn = QPushButton("Preview Changes")
        self._preview_btn.setObjectName("SecondaryBtn")
        self._preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preview_btn.setFixedHeight(42)
        self._preview_btn.setEnabled(False)
        self._preview_btn.clicked.connect(self._run_preview)
        btn_row.addWidget(self._preview_btn)

        self._clean_btn = QPushButton("✦  Free Space Safely")
        self._clean_btn.setObjectName("PrimaryBtn")
        self._clean_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clean_btn.setFixedHeight(42)
        self._clean_btn.setEnabled(False)
        self._clean_btn.clicked.connect(self._run_preview)   # always preview first
        btn_row.addWidget(self._clean_btn, 2)

        outer.addLayout(btn_row)

    # ── Scan ──────────────────────────────────────────────────

    def _start_scan(self):
        if self._scan_worker and self._scan_worker.isRunning():
            return

        self._clear_rows()
        self._rescan_btn.setEnabled(False)
        self._clean_btn.setEnabled(False)
        self._preview_btn.setEnabled(False)
        self._progress_bar.setValue(0)
        self._progress_bar.show()
        self._status_label.setText("Scanning…")

        self._scan_worker = ScanWorker()
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.finished.connect(self._on_scan_done)
        self._scan_worker.error.connect(self._on_error)
        self._scan_worker.start()

    def _on_scan_progress(self, msg: str, pct: int):
        self._status_label.setText(msg)
        self._progress_bar.setValue(pct)

    def _on_scan_done(self, categories: list):
        self._categories = categories
        self._progress_bar.hide()
        self._rescan_btn.setEnabled(True)
        self._clean_btn.setEnabled(True)
        self._preview_btn.setEnabled(True)

        cats_with_junk = [c for c in categories if c.size_bytes > 0]
        total = sum(c.size_bytes for c in categories)
        self._status_label.setText(
            f"Scan complete · {len(cats_with_junk)} categories with junk · "
            f"{_human_size(total)} recoverable"
        )
        self._populate_rows(categories)
        self._update_total()

    # ── Category rows ─────────────────────────────────────────

    def _clear_rows(self):
        for row in self._rows:
            row.deleteLater()
        self._rows.clear()

    def _populate_rows(self, categories: list[ScanCategory]):
        self._clear_rows()
        # Insert before the trailing stretch
        insert_at = self._list_layout.count() - 1

        sorted_cats = sorted(categories, key=lambda c: c.size_bytes, reverse=True)
        for cat in sorted_cats:
            row = CategoryRow(cat)
            row.checkbox.stateChanged.connect(self._update_total)
            self._list_layout.insertWidget(insert_at, row)
            self._rows.append(row)
            insert_at += 1

    def _on_select_all(self, state):
        for row in self._rows:
            if row.cat.size_bytes > 0:
                row.checkbox.setChecked(state == Qt.CheckState.Checked.value)
        self._update_total()

    def _update_total(self):
        selected = [r.cat for r in self._rows if r.is_selected()]
        total = sum(c.size_bytes for c in selected)
        if total > 0:
            self._total_label.setText(f"{_human_size(total)} selected")
        else:
            self._total_label.setText("")

    # ── Preview / Clean ───────────────────────────────────────

    def _run_preview(self):
        selected = [r.cat for r in self._rows if r.is_selected()]
        if not selected:
            self._status_label.setText("Select at least one category to clean.")
            return

        self._status_label.setText("Generating preview…")
        self._clean_btn.setEnabled(False)
        self._preview_btn.setEnabled(False)
        self._progress_bar.setValue(0)
        self._progress_bar.show()

        self._clean_worker = CleanWorker(selected, dry_run=True)
        self._clean_worker.progress.connect(self._on_clean_progress)
        self._clean_worker.finished.connect(self._on_preview_done)
        self._clean_worker.error.connect(self._on_error)
        self._clean_worker.start()

    def _on_clean_progress(self, msg: str, pct: int):
        # Strip internal log prefixes before showing to user
        display = msg.replace("[DRY RUN] ", "").replace("[DRY RUN]", "")
        self._status_label.setText(display)
        self._progress_bar.setValue(pct)

    def _on_preview_done(self, results):
        self._progress_bar.hide()
        self._clean_btn.setEnabled(True)
        self._preview_btn.setEnabled(True)

        dialog = DryRunDialog(results, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._run_real_clean([r for r in results if r.category_id])

    def _run_real_clean(self, preview_results):
        selected = [r.cat for r in self._rows if r.is_selected()]
        self._clean_btn.setEnabled(False)
        self._preview_btn.setEnabled(False)
        self._progress_bar.setValue(0)
        self._progress_bar.show()
        self._status_label.setText("Cleaning…")

        self._clean_worker = CleanWorker(selected, dry_run=False)
        self._clean_worker.progress.connect(self._on_clean_progress)
        self._clean_worker.finished.connect(self._on_clean_done)
        self._clean_worker.error.connect(self._on_error)
        self._clean_worker.start()

    def _on_clean_done(self, results):
        from config.settings import settings
        from datetime import datetime

        self._progress_bar.hide()
        self._clean_btn.setEnabled(True)
        self._preview_btn.setEnabled(True)

        total_freed = sum(r.freed_bytes for r in results)
        total_deleted = sum(r.deleted_count for r in results)
        total_skipped = sum(r.skipped_count for r in results)
        all_errors = [e for r in results for e in r.errors]
        settings.set("last_clean_date", datetime.now().isoformat())

        if all_errors and total_deleted == 0:
            from ui.theme import Theme
            self._status_label.setStyleSheet(f"color: {Theme.p().warning};")
            self._status_label.setText(
                f"Could not clean — permission required. Check Logs for the exact command to run."
            )
            # Show actionable dialog if journal hint is present
            journal_hint = next((e for e in all_errors if "sudo journalctl" in e), None)
            if journal_hint:
                from PyQt6.QtWidgets import QMessageBox
                msg = QMessageBox()
                msg.setWindowTitle("System Journal — Manual Step Required")
                msg.setText(journal_hint)
                msg.setIcon(QMessageBox.Icon.Information)
                msg.exec()
        else:
            self._status_label.setStyleSheet("")
            skipped_note = f" · {total_skipped} skipped (permission)" if total_skipped else ""
            self._status_label.setText(
                f"Done! Freed {_human_size(total_freed)} · {total_deleted} items removed{skipped_note}."
            )

        # Re-scan to update sizes
        QTimer.singleShot(1500, self._start_scan)

    def _on_error(self, msg: str):
        self._progress_bar.hide()
        self._clean_btn.setEnabled(True)
        self._preview_btn.setEnabled(True)
        self._rescan_btn.setEnabled(True)
        self._status_label.setText(f"Error: {msg}")
