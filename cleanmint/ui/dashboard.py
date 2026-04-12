"""
ui/dashboard.py — CleanMint Dashboard Page

Shows disk usage, health score, junk estimate, last clean date,
and top space-consuming folders.
"""

import os
import shutil
from pathlib import Path
from datetime import datetime

import psutil
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QGridLayout,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont

from ui.theme import Theme
from config.settings import settings
from core.scanner import Scanner, _human_size


# ────────────────────────────────────────────────────────────────
# Background worker — runs scanner off the main thread
# ────────────────────────────────────────────────────────────────
class ScanWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(list)   # list[ScanCategory]
    error    = pyqtSignal(str)

    def run(self):
        try:
            scanner = Scanner(progress_callback=lambda m, p: self.progress.emit(m, p))
            cats = scanner.run_full_scan()
            self.finished.emit(cats)
        except Exception as e:
            self.error.emit(str(e))


# ────────────────────────────────────────────────────────────────
# Stat card widget
# ────────────────────────────────────────────────────────────────
class StatCard(QFrame):
    def __init__(self, title: str, value: str, subtitle: str = "", accent: bool = False):
        super().__init__()
        self.setObjectName("Card")
        self.setMinimumWidth(160)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        t = QLabel(title)
        t.setObjectName("MutedLabel")
        layout.addWidget(t)

        self._value_label = QLabel(value)
        self._value_label.setFont(QFont("Inter", 20, QFont.Weight.Bold))
        if accent:
            self._value_label.setStyleSheet(f"color: {Theme.p().accent};")
        layout.addWidget(self._value_label)

        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("MutedLabel")
            layout.addWidget(s)

    def set_value(self, value: str):
        self._value_label.setText(value)


# ────────────────────────────────────────────────────────────────
# Disk usage bar widget
# ────────────────────────────────────────────────────────────────
class DiskUsageBar(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("Card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Disk Usage")
        title.setObjectName("SectionHeader")
        header.addWidget(title)
        header.addStretch()
        self._pct_label = QLabel("—")
        self._pct_label.setObjectName("AccentLabel")
        header.addWidget(self._pct_label)
        layout.addLayout(header)

        self._bar_container = QFrame()
        self._bar_container.setFixedHeight(12)
        self._bar_container.setStyleSheet(
            f"background: {Theme.p().bg_secondary}; border-radius: 6px;"
        )
        layout.addWidget(self._bar_container)

        self._fill = QFrame(self._bar_container)
        self._fill.setFixedHeight(12)
        self._fill.setStyleSheet(
            f"background: {Theme.p().accent}; border-radius: 6px;"
        )
        self._fill.setFixedWidth(0)

        details = QHBoxLayout()
        self._used_label = QLabel("Used: —")
        self._used_label.setObjectName("MutedLabel")
        self._free_label = QLabel("Free: —")
        self._free_label.setObjectName("MutedLabel")
        details.addWidget(self._used_label)
        details.addStretch()
        details.addWidget(self._free_label)
        layout.addLayout(details)

    def update_disk(self, total: int, used: int, free: int):
        pct = round(used / total * 100) if total > 0 else 0
        self._pct_label.setText(f"{pct}% used")
        self._used_label.setText(f"Used: {_human_size(used)}")
        self._free_label.setText(f"Free: {_human_size(free)}  /  {_human_size(total)} total")

        # Resize fill bar proportionally
        width = self._bar_container.width()
        fill_w = max(8, int(width * pct / 100))
        self._fill.setFixedWidth(fill_w)

        # Colour shifts to orange/red when disk is getting full
        if pct >= 90:
            colour = Theme.p().danger
        elif pct >= 75:
            colour = Theme.p().warning
        else:
            colour = Theme.p().accent
        self._fill.setStyleSheet(f"background: {colour}; border-radius: 6px;")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Re-apply width on resize
        w = self._bar_container.width()
        if w > 0:
            pct_text = self._pct_label.text().replace("% used", "")
            if pct_text.strip().isdigit():
                pct = int(pct_text.strip())
                self._fill.setFixedWidth(max(8, round(w * pct / 100)))


# ────────────────────────────────────────────────────────────────
# Dashboard Page
# ────────────────────────────────────────────────────────────────
class DashboardPage(QWidget):
    navigate_to = pyqtSignal(str)   # emits nav key e.g. "cleaner"

    def __init__(self):
        super().__init__()
        self._scan_worker: ScanWorker | None = None
        self._build_ui()
        # Auto-scan after a short delay so the window is fully rendered first
        QTimer.singleShot(600, self._start_scan)

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
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(20)

        # ── Page header ──────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setObjectName("TitleLabel")
        header.addWidget(title)
        header.addStretch()

        self._scan_btn = QPushButton("↺  Refresh")
        self._scan_btn.setObjectName("SecondaryBtn")
        self._scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scan_btn.setFixedHeight(34)
        self._scan_btn.clicked.connect(self._start_scan)
        header.addWidget(self._scan_btn)
        layout.addLayout(header)

        self._status_label = QLabel("Scanning your system...")
        self._status_label.setObjectName("MutedLabel")
        layout.addWidget(self._status_label)

        # ── Disk usage bar ────────────────────────────────────
        self._disk_bar = DiskUsageBar()
        layout.addWidget(self._disk_bar)

        # ── Stat cards row ────────────────────────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)

        self._card_junk = StatCard("Junk Found", "—", "calculating…", accent=True)
        self._card_health = StatCard("Health Score", "—", "out of 100")
        self._card_last_clean = StatCard("Last Cleaned", self._last_clean_str())
        self._card_categories = StatCard("Categories", "—", "with junk")

        for card in (self._card_junk, self._card_health, self._card_last_clean, self._card_categories):
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            cards_row.addWidget(card)

        layout.addLayout(cards_row)

        # ── Junk breakdown ────────────────────────────────────
        breakdown_title = QLabel("Junk Breakdown")
        breakdown_title.setObjectName("SectionHeader")
        layout.addWidget(breakdown_title)

        self._breakdown_frame = QFrame()
        self._breakdown_frame.setObjectName("Card")
        self._breakdown_layout = QVBoxLayout(self._breakdown_frame)
        self._breakdown_layout.setContentsMargins(12, 12, 12, 12)
        self._breakdown_layout.setSpacing(0)
        layout.addWidget(self._breakdown_frame)

        self._breakdown_placeholder = QLabel("Scanning…")
        self._breakdown_placeholder.setObjectName("MutedLabel")
        self._breakdown_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._breakdown_placeholder.setFixedHeight(60)
        self._breakdown_layout.addWidget(self._breakdown_placeholder)

        # ── Quick action button ───────────────────────────────
        self._clean_btn = QPushButton("✦  Free Space Safely")
        self._clean_btn.setObjectName("PrimaryBtn")
        self._clean_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clean_btn.setFixedHeight(46)
        self._clean_btn.setEnabled(False)
        self._clean_btn.clicked.connect(lambda: self.navigate_to.emit("cleaner"))
        layout.addWidget(self._clean_btn)

        layout.addStretch()

    # ── Disk info (always available) ─────────────────────────

    def _refresh_disk_info(self):
        try:
            usage = psutil.disk_usage("/")
            self._disk_bar.update_disk(usage.total, usage.used, usage.free)
        except Exception:
            pass

    # ── Scan lifecycle ────────────────────────────────────────

    def _start_scan(self):
        if self._scan_worker and self._scan_worker.isRunning():
            return

        self._scan_btn.setEnabled(False)
        self._clean_btn.setEnabled(False)
        self._status_label.setText("Scanning your system…")
        self._breakdown_placeholder.setText("Scanning…")
        self._refresh_disk_info()

        self._scan_worker = ScanWorker()
        self._scan_worker.progress.connect(self._on_progress)
        self._scan_worker.finished.connect(self._on_scan_done)
        self._scan_worker.error.connect(self._on_scan_error)
        self._scan_worker.start()

    def _on_progress(self, msg: str, pct: int):
        self._status_label.setText(msg)

    def _on_scan_done(self, categories):
        self._scan_btn.setEnabled(True)
        self._clean_btn.setEnabled(True)

        total_junk = sum(c.size_bytes for c in categories)
        cats_with_junk = [c for c in categories if c.size_bytes > 0]
        health_score = self._compute_health(categories)

        self._status_label.setText(
            f"Scan complete · {len(categories)} categories checked"
        )
        self._card_junk.set_value(_human_size(total_junk))
        self._card_health.set_value(str(health_score))
        self._card_categories.set_value(str(len(cats_with_junk)))

        self._populate_breakdown(categories)

    def _on_scan_error(self, msg: str):
        self._scan_btn.setEnabled(True)
        self._status_label.setText(f"Scan error: {msg}")

    # ── Breakdown list ─────────────────────────────────────────

    def _populate_breakdown(self, categories):
        # Clear old items (keep placeholder for reference but hide it)
        self._breakdown_placeholder.hide()

        # Remove old rows (skip placeholder at index 0)
        while self._breakdown_layout.count() > 1:
            item = self._breakdown_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        sorted_cats = sorted(categories, key=lambda c: c.size_bytes, reverse=True)

        for cat in sorted_cats:
            row = self._make_breakdown_row(cat)
            self._breakdown_layout.addWidget(row)

        if not sorted_cats:
            self._breakdown_placeholder.setText("No junk found!")
            self._breakdown_placeholder.show()

    def _make_breakdown_row(self, cat) -> QWidget:
        p = Theme.p()
        row = QFrame()
        row.setStyleSheet(f"border-bottom: 1px solid {p.border};")
        row.setFixedHeight(52)

        h = QHBoxLayout(row)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(12)

        # Risk badge
        risk_colours = {
            "low":    (p.risk_low_bg,    p.risk_low_fg),
            "medium": (p.risk_medium_bg, p.risk_medium_fg),
            "expert": (p.risk_expert_bg, p.risk_expert_fg),
        }
        bg, fg = risk_colours.get(cat.risk, (p.bg_secondary, p.text_muted))
        badge = QLabel(cat.risk.upper())
        badge.setFixedSize(58, 20)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background: {bg}; color: {fg}; border-radius: 4px;"
            f"font-size: 10px; font-weight: 700;"
        )
        h.addWidget(badge)

        # Name + description
        info = QVBoxLayout()
        info.setSpacing(1)
        name_lbl = QLabel(cat.name)
        name_lbl.setFont(QFont("Inter", 12, QFont.Weight.Medium))
        desc_lbl = QLabel(cat.description)
        desc_lbl.setObjectName("MutedLabel")
        desc_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        info.addWidget(name_lbl)
        info.addWidget(desc_lbl)
        h.addLayout(info, 1)

        # Size
        size_lbl = QLabel(cat.size_human if cat.size_bytes > 0 else "—")
        size_lbl.setFont(QFont("Inter", 13, QFont.Weight.Bold))
        size_lbl.setStyleSheet(f"color: {p.accent if cat.size_bytes > 0 else p.text_muted};")
        size_lbl.setFixedWidth(72)
        size_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(size_lbl)

        return row

    # ── Helpers ────────────────────────────────────────────────

    def _compute_health(self, categories) -> int:
        """Simple health score 0-100 based on junk size relative to disk."""
        try:
            total_disk = psutil.disk_usage("/").total
            total_junk = sum(c.size_bytes for c in categories)
            ratio = total_junk / total_disk if total_disk > 0 else 0
            # Scale: 0% junk → 100, 5%+ junk → 60, 15%+ → 30
            score = max(30, int(100 - ratio * 600))
            return min(score, 100)
        except Exception:
            return 80

    def _last_clean_str(self) -> str:
        d = settings.get("last_clean_date")
        if d:
            return str(d)[:10]
        return "Never"
