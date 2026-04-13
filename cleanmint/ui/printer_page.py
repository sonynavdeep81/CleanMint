"""
ui/printer_page.py — Printer Profile Viewer

Shows configured CUPS printers and service status.
Lets the user export a restore script.
"""

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.printer import (
    PrinterInfo,
    ServiceStatus,
    generate_restore_script,
    get_printers,
    get_service_status,
)
from ui.theme import Theme


# ── Background worker ──────────────────────────────────────────────────────


class LoadWorker(QThread):
    finished = pyqtSignal(list, object)   # printers, ServiceStatus
    error    = pyqtSignal(str)

    def run(self):
        try:
            printers = get_printers()
            services = get_service_status()
            self.finished.emit(printers, services)
        except Exception as e:
            self.error.emit(str(e))


# ── Small card widget ──────────────────────────────────────────────────────


class InfoCard(QWidget):
    """A labelled info card used to display a single printer's details."""

    def __init__(self, printer: PrinterInfo, parent=None):
        super().__init__(parent)
        p = Theme.p()
        self.setStyleSheet(
            f"background: {p.bg_card}; border-radius: 8px; padding: 4px;"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        # Printer name header
        name_row = QHBoxLayout()
        name_lbl = QLabel(printer.name.replace("_", " "))
        name_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {p.text_primary};"
        )
        name_row.addWidget(name_lbl)
        name_row.addStretch()

        status_dot = "●"
        status_color = p.success if printer.enabled else p.danger
        status_lbl = QLabel(
            f'<span style="color:{status_color}">{status_dot}</span>'
            f' {"Ready" if printer.enabled else "Disabled"}'
        )
        status_lbl.setStyleSheet(f"color: {p.text_secondary}; font-size: 12px;")
        name_row.addWidget(status_lbl)
        lay.addLayout(name_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        lay.addWidget(sep)

        # Details grid
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setVerticalSpacing(6)
        grid.setHorizontalSpacing(16)

        def add_row(row: int, label: str, value: str):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {p.text_muted}; font-size: 12px;")
            val = QLabel(value)
            val.setStyleSheet(f"color: {p.text_primary}; font-size: 12px;")
            val.setWordWrap(True)
            grid.addWidget(lbl, row, 0)
            grid.addWidget(val, row, 1)

        add_row(0, "Model",      printer.model)
        add_row(1, "Connection", printer.connection)
        add_row(2, "Driver",     printer.driver_pkg)

        if printer.toner_pct is not None:
            color = (
                p.success if printer.toner_pct > 30
                else p.warning if printer.toner_pct > 10
                else p.danger
            )
            add_row(3, "Toner",
                    f'<span style="color:{color}">{printer.toner_pct}%</span>')

        lay.addLayout(grid)


# ── Service status row ─────────────────────────────────────────────────────


class ServiceRow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        p = Theme.p()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(20)

        lbl = QLabel("Services:")
        lbl.setStyleSheet(f"color: {p.text_muted}; font-size: 12px;")
        lay.addWidget(lbl)

        self._dots: dict[str, QLabel] = {}
        for svc in ("cups", "cups-browsed", "avahi-daemon"):
            dot = QLabel(f"● {svc}")
            dot.setStyleSheet(f"color: {p.text_muted}; font-size: 12px;")
            lay.addWidget(dot)
            self._dots[svc] = dot

        lay.addStretch()

    def update_status(self, status: ServiceStatus):
        p = Theme.p()
        mapping = {
            "cups":         status.cups,
            "cups-browsed": status.cups_browsed,
            "avahi-daemon": status.avahi,
        }
        for svc, active in mapping.items():
            color = p.success if active else p.danger
            self._dots[svc].setStyleSheet(
                f"color: {color}; font-size: 12px;"
            )


# ── Main page ──────────────────────────────────────────────────────────────


class PrinterPage(QWidget):
    def __init__(self):
        super().__init__()
        self._printers: list[PrinterInfo] = []
        self._worker: QThread | None = None
        self._build_ui()
        self._load()

    def _build_ui(self):
        p = Theme.p()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        # ── Header ─────────────────────────────────────────────────────────
        hdr = QHBoxLayout()

        title = QLabel("Printer Profile")
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
            "View your working printer configuration and export a restore script. "
            "Run the script whenever an update breaks your printer — "
            "no manual driver download needed."
        )
        sub.setObjectName("SubtitleLabel")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        # ── Status ─────────────────────────────────────────────────────────
        self._status = QLabel("")
        self._status.setObjectName("MutedLabel")
        self._status.hide()
        layout.addWidget(self._status)

        # ── Service status row ──────────────────────────────────────────────
        self._svc_row = ServiceRow()
        layout.addWidget(self._svc_row)

        # ── Printer cards ───────────────────────────────────────────────────
        self._cards_container = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(12)
        layout.addWidget(self._cards_container)

        # ── Empty state ─────────────────────────────────────────────────────
        self._empty = QLabel(
            "No configured printers found.\n"
            "Set up your printer in System Settings → Printers first."
        )
        self._empty.setObjectName("MutedLabel")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.hide()
        layout.addWidget(self._empty)

        layout.addStretch()

    # ── Data loading ───────────────────────────────────────────────────────

    def _load(self):
        self._set_status("Reading printer configuration…")
        self._refresh_btn.setEnabled(False)
        self._export_btn.setEnabled(False)

        self._worker = LoadWorker()
        self._worker.finished.connect(self._on_loaded)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_loaded(self, printers: list, services: ServiceStatus):
        self._printers = printers
        self._svc_row.update_status(services)
        self._rebuild_cards(printers)
        self._set_status("")
        self._refresh_btn.setEnabled(True)
        self._export_btn.setEnabled(bool(printers))

    def _on_error(self, err: str):
        self._set_status(f"Error: {err}")
        self._refresh_btn.setEnabled(True)

    def _rebuild_cards(self, printers: list):
        # Clear old cards
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not printers:
            self._empty.show()
            return

        self._empty.hide()
        for printer in printers:
            card = InfoCard(printer)
            self._cards_layout.addWidget(card)

    # ── Export ─────────────────────────────────────────────────────────────

    def _export(self):
        if not self._printers:
            QMessageBox.information(self, "No Data",
                                    "No printers loaded. Click Refresh first.")
            return

        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Export Printer Restore Script",
            str(Path.home() / "printer_restore.sh"),
            "Shell Scripts (*.sh);;All Files (*)",
        )
        if not dest:
            return

        try:
            generate_restore_script(self._printers, Path(dest))
            names = "\n".join(f"  • {p.name}" for p in self._printers)
            QMessageBox.information(
                self,
                "Script Exported",
                f"Printer restore script saved to:\n{dest}\n\n"
                f"Printers included:\n{names}\n\n"
                "Whenever your printer stops working after an update, run:\n"
                "  bash printer_restore.sh",
            )
        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))

    # ── Helpers ────────────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self._status.setText(msg)
        self._status.setVisible(bool(msg))
