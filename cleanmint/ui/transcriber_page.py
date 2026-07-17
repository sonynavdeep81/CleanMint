"""
ui/transcriber_page.py — YouTube Transcriber page

Paste a YouTube URL → downloads the video to ~/Downloads, then produces a
formatted transcript as .txt + .pdf (YouTube captions when available,
local faster-whisper fallback otherwise).
"""

import re

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.theme import Theme

_PCT_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
_URL_RE = re.compile(r"^https?://(www\.)?(youtube\.com|youtu\.be)/\S+$")


class TranscribeWorker(QThread):
    log = pyqtSignal(str)            # status line for the log pane
    pct = pyqtSignal(int)            # progress bar 0-100
    done = pyqtSignal(str, str)      # txt path, pdf path
    error = pyqtSignal(str)

    def __init__(self, url: str, cookies: bool):
        super().__init__()
        self._url = url
        self._cookies = cookies
        self._last_dl_pct = -10

    def _dl_line(self, line: str):
        m = _PCT_RE.search(line)
        if not m:
            return
        pct = float(m.group(1))
        if pct - self._last_dl_pct >= 10:      # throttle log/bar updates
            self._last_dl_pct = pct
            self.log.emit(f"  downloading… {pct:.0f}%")
            self.pct.emit(5 + int(pct * 0.5))  # map 0-100% into bar range 5-55

    def run(self):
        try:
            from core import transcriber as T

            self.log.emit("Fetching video info…")
            self.pct.emit(2)
            meta = T.fetch_metadata(self._url, self._cookies)
            mins = max(1, meta.duration // 60)
            self.log.emit(f"✔ {meta.title}  ({meta.channel}, ~{mins} min)")

            self.log.emit("Downloading video to ~/Downloads…")
            self.pct.emit(5)
            video_path = T.download_video(self._url, self._cookies,
                                          line_cb=self._dl_line)
            self.log.emit(f"✔ Video saved: {video_path.name}")

            track = T.pick_caption_track(meta.info)
            segs = None
            source = ""
            if track:
                lang, is_auto = track
                kind = "auto-captions" if is_auto else "captions"
                self.log.emit(f"Found YouTube {kind} ({lang}) — using them…")
                self.pct.emit(60)
                segs = T.fetch_captions(self._url, lang, is_auto, self._cookies)
                source = f"YouTube {kind} ({lang})"
            if not segs:
                if not T.whisper_available():
                    raise RuntimeError(
                        "This video has no captions and faster-whisper is not "
                        "installed.\nInstall it with:\n"
                        "  ~/Cleanmint/venv/bin/pip install faster-whisper"
                    )
                self.log.emit(
                    "No captions — transcribing locally with Whisper (medium). "
                    "This takes roughly the video's length, please wait…"
                )
                self.pct.emit(10)
                segs = T.whisper_transcribe(video_path, meta.duration,
                                            progress_cb=self.pct.emit)
                source = "Whisper (medium, local)"

            self.log.emit("Formatting transcript…")
            self.pct.emit(95)
            paras = T.build_paragraphs(segs)
            txt = T.write_txt(meta, paras, source)
            pdf = T.write_pdf(meta, paras, source)
            self.pct.emit(100)
            self.done.emit(str(txt), str(pdf))
        except Exception as e:                  # noqa: BLE001 — surfaced to UI
            self.error.emit(str(e))


class TranscriberPage(QWidget):
    def __init__(self):
        super().__init__()
        self._worker: TranscribeWorker | None = None
        self._build_ui()

    def _build_ui(self):
        p = Theme.p()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        # ── Header ─────────────────────────────────────────────────────────
        title = QLabel("YouTube Transcriber")
        title.setObjectName("TitleLabel")
        layout.addWidget(title)

        sub = QLabel(
            "Paste a YouTube URL. The video is downloaded to ~/Downloads and a "
            "formatted transcript is saved next to it as .txt and .pdf. "
            "YouTube captions are used when available; otherwise the audio is "
            "transcribed locally with Whisper."
        )
        sub.setObjectName("SubtitleLabel")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        # ── URL row ────────────────────────────────────────────────────────
        row = QHBoxLayout()
        row.setSpacing(8)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("https://www.youtube.com/watch?v=…")
        self._url_edit.setFixedHeight(34)
        self._url_edit.returnPressed.connect(self._start)
        row.addWidget(self._url_edit, 1)

        self._go_btn = QPushButton("▶  Transcribe")
        self._go_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._go_btn.setFixedHeight(34)
        self._go_btn.setStyleSheet(
            f"QPushButton {{ background: {p.accent}; color: #fff; border: none;"
            f"  border-radius: 6px; font-weight: 600; padding: 0 16px; }}"
            f"QPushButton:hover {{ background: {p.accent}cc; }}"
            f"QPushButton:disabled {{ background: {p.accent}55; }}"
        )
        self._go_btn.clicked.connect(self._start)
        row.addWidget(self._go_btn)

        layout.addLayout(row)

        self._cookies_cb = QCheckBox(
            "Use Chrome cookies (needed for age-restricted videos)"
        )
        layout.addWidget(self._cookies_cb)

        # ── Progress ───────────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        self._progress.hide()
        layout.addWidget(self._progress)

        # ── Log pane ───────────────────────────────────────────────────────
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("Progress will appear here…")
        layout.addWidget(self._log, 1)

        # ── Result line ────────────────────────────────────────────────────
        self._result = QLabel("")
        self._result.setObjectName("MutedLabel")
        self._result.setWordWrap(True)
        self._result.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._result.hide()
        layout.addWidget(self._result)

    # ── Actions ────────────────────────────────────────────────────────────

    def _start(self):
        if self._worker is not None:
            return
        url = self._url_edit.text().strip()
        if not _URL_RE.match(url):
            self._log.setPlainText("✗ That doesn't look like a YouTube URL.")
            return

        self._log.clear()
        self._result.hide()
        self._go_btn.setEnabled(False)
        self._go_btn.setText("⏳  Working…")
        self._progress.setValue(0)
        self._progress.show()

        self._worker = TranscribeWorker(url, self._cookies_cb.isChecked())
        self._worker.log.connect(self._log.appendPlainText)
        self._worker.pct.connect(self._progress.setValue)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_done(self, txt: str, pdf: str):
        self._log.appendPlainText("✔ All done!")
        self._result.setText(f"Transcript saved:\n{txt}\n{pdf}")
        self._result.show()

    def _on_error(self, err: str):
        self._log.appendPlainText(f"✗ {err}")

    def _on_finished(self):
        self._worker = None
        self._go_btn.setEnabled(True)
        self._go_btn.setText("▶  Transcribe")
        self._progress.hide()
