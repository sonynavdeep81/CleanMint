"""
ui/logs_page.py — Logs viewer + export UI
"""

import csv
import html
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QFrame, QFileDialog, QComboBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from ui.theme import Theme

LOG_DIR = Path.home() / ".local" / "share" / "cleanmint" / "logs"

# Lines containing these keywords get colored in the viewer.
_HIGHLIGHT_RULES: list[tuple[str, str]] = [
    # keyword (case-insensitive)  →  background color
    ("[DELETED]",       "#c0392b"),   # red   — actual file/dir deleted
    ("[SNAP REMOVED]",  "#c0392b"),   # red   — snap revision removed
    ("[SNAP DRY-RUN]",  "#7f8c8d"),   # grey  — dry-run preview
    ("[DRY-RUN]",       "#7f8c8d"),   # grey  — dry-run preview
    ("[SNAP PROTECTED]","#e67e22"),   # orange — protected snap skipped
    ("blocked",         "#e67e22"),   # orange — safety block
    ("[ERROR]",         "#8e44ad"),   # purple — error
    ("error",           "#8e44ad"),   # purple — error
]

# Keywords that qualify a line for the "Deletions Only" filter
_DELETION_KEYWORDS = {"[DELETED]", "[SNAP REMOVED]"}


def _colorize(raw_text: str) -> str:
    """Convert plain log text to HTML with colored deletion/error lines."""
    lines_html = []
    for line in raw_text.splitlines():
        escaped = html.escape(line)
        color = None
        upper = line.upper()
        for keyword, col in _HIGHLIGHT_RULES:
            if keyword.upper() in upper:
                color = col
                break
        if color:
            lines_html.append(
                f'<span style="color:{color};font-weight:bold;">{escaped}</span>'
            )
        else:
            lines_html.append(f'<span>{escaped}</span>')
    return "<br>".join(lines_html)


def _deletions_only(raw_text: str) -> str:
    """Return only lines that represent actual deletions."""
    kept = [
        line for line in raw_text.splitlines()
        if any(kw.upper() in line.upper() for kw in _DELETION_KEYWORDS)
    ]
    if not kept:
        return "(No deletions recorded in this log session.)"
    return "\n".join(kept)


class LogsPage(QWidget):
    def __init__(self):
        super().__init__()
        self._raw_text = ""
        self._deletions_mode = False
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

        # Filter + export row
        action_row = QHBoxLayout()

        self._del_btn = QPushButton("🗑  Deletions Only")
        self._del_btn.setObjectName("SecondaryBtn")
        self._del_btn.setFixedHeight(30)
        self._del_btn.setCheckable(True)
        self._del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del_btn.setToolTip(
            "Show only lines where CleanMint actually deleted a file or snap revision"
        )
        self._del_btn.toggled.connect(self._on_deletions_toggled)
        action_row.addWidget(self._del_btn)

        action_row.addSpacing(16)
        action_row.addWidget(QLabel("Export:"))
        for fmt in ("TXT", "CSV"):
            btn = QPushButton(fmt)
            btn.setObjectName("SecondaryBtn")
            btn.setFixedHeight(30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, f=fmt: self._export(f))
            action_row.addWidget(btn)

        action_row.addStretch()
        self._status = QLabel("")
        self._status.setObjectName("MutedLabel")
        action_row.addWidget(self._status)
        layout.addLayout(action_row)

        # Legend row
        legend = QHBoxLayout()
        legend.addWidget(QLabel("Key:"))
        for label, color in [
            ("Deleted", "#c0392b"),
            ("Protected/Blocked", "#e67e22"),
            ("Dry-run", "#7f8c8d"),
            ("Error", "#8e44ad"),
        ]:
            dot = QLabel(f'<span style="color:{color};">&#9632;</span> {label}')
            dot.setTextFormat(Qt.TextFormat.RichText)
            legend.addWidget(dot)
        legend.addStretch()
        layout.addLayout(legend)

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
            self._raw_text = path.read_text(encoding="utf-8", errors="replace")
            self._status.setText(f"Showing: {path.name}")
            self._render()
        except OSError as e:
            self._text.setPlainText(f"Could not read log: {e}")

    def _on_deletions_toggled(self, checked: bool):
        self._deletions_mode = checked
        self._del_btn.setText("📋  Show All" if checked else "🗑  Deletions Only")
        self._render()

    def _render(self):
        if self._deletions_mode:
            plain = _deletions_only(self._raw_text)
            self._text.setPlainText(plain)
        else:
            self._text.setHtml(
                f'<pre style="font-family:monospace;font-size:11pt;">'
                f'{_colorize(self._raw_text)}</pre>'
            )

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

        # Always export the full raw log, not the filtered view
        content = self._raw_text
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
