"""
ui/logs_page.py — Logs viewer + export UI
"""

import csv
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QFrame, QFileDialog, QComboBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from ui.theme import Theme

LOG_DIR = Path.home() / ".local" / "share" / "cleanmint" / "logs"


class LogsPage(QWidget):
    def __init__(self):
        super().__init__()
        self._build_ui()
        self._load_logs()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        hdr = QHBoxLayout()
        title = QLabel("Logs")
        title.setObjectName("TitleLabel")
        hdr.addWidget(title)
        hdr.addStretch()

        self._log_combo = QComboBox()
        self._log_combo.setMinimumWidth(200)
        self._log_combo.currentIndexChanged.connect(self._on_log_selected)
        hdr.addWidget(self._log_combo)

        refresh_btn = QPushButton("↺  Refresh")
        refresh_btn.setObjectName("SecondaryBtn")
        refresh_btn.setFixedHeight(34)
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self._load_logs)
        hdr.addWidget(refresh_btn)
        layout.addLayout(hdr)

        # Export buttons
        export_row = QHBoxLayout()
        export_row.addWidget(QLabel("Export:"))
        for fmt in ("TXT", "CSV"):
            btn = QPushButton(fmt)
            btn.setObjectName("SecondaryBtn")
            btn.setFixedHeight(30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, f=fmt: self._export(f))
            export_row.addWidget(btn)
        export_row.addStretch()
        self._status = QLabel("")
        self._status.setObjectName("MutedLabel")
        export_row.addWidget(self._status)
        layout.addLayout(export_row)

        # Log text view
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Monospace", 11))
        self._text.setObjectName("Card")
        layout.addWidget(self._text, 1)

    def _load_logs(self):
        self._log_combo.blockSignals(True)
        self._log_combo.clear()

        if not LOG_DIR.exists():
            self._text.setPlainText("No log files found yet.\nLogs are created after your first cleanup.")
            self._log_combo.blockSignals(False)
            return

        logs = sorted(LOG_DIR.glob("*.log"), reverse=True)
        if not logs:
            self._text.setPlainText("No log files found yet.")
            self._log_combo.blockSignals(False)
            return

        for log in logs:
            self._log_combo.addItem(log.name, userData=log)

        self._log_combo.blockSignals(False)
        self._show_log(logs[0])

    def _on_log_selected(self, index: int):
        path = self._log_combo.itemData(index)
        if path:
            self._show_log(path)

    def _show_log(self, path: Path):
        try:
            self._text.setPlainText(path.read_text(encoding="utf-8", errors="replace"))
            self._status.setText(f"Showing: {path.name}")
        except OSError as e:
            self._text.setPlainText(f"Could not read log: {e}")

    def _export(self, fmt: str):
        path = self._log_combo.currentData()
        if not path:
            return

        ext = f".{fmt.lower()}"
        dest, _ = QFileDialog.getSaveFileName(
            self, f"Export Log as {fmt}",
            str(Path.home() / f"cleanmint_log{ext}"),
            f"{fmt} Files (*{ext})"
        )
        if not dest:
            return

        content = self._text.toPlainText()
        try:
            if fmt == "CSV":
                with open(dest, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Line", "Content"])
                    for i, line in enumerate(content.splitlines(), 1):
                        writer.writerow([i, line])
            else:
                Path(dest).write_text(content, encoding="utf-8")
            self._status.setText(f"Exported to {Path(dest).name}")
        except OSError as e:
            self._status.setText(f"Export failed: {e}")
