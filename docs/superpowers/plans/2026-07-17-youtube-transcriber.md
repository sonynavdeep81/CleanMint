# YouTube Transcriber Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New CleanMint page: paste YouTube URL → download video + produce formatted transcript (`.txt` + `.pdf`) in `~/Downloads`; captions-first, faster-whisper `medium` fallback.

**Architecture:** All logic in UI-free `core/transcriber.py` (pure parse/format functions + yt-dlp subprocess wrappers + lazy-imported whisper). `ui/transcriber_page.py` hosts a single QThread worker streaming progress signals, mirroring `snapshot_page.py`. Spec: `docs/superpowers/specs/2026-07-17-youtube-transcriber-design.md`.

**Tech Stack:** yt-dlp (system, `/usr/local/bin/yt-dlp`), faster-whisper (venv, lazy), reportlab (already in venv), PyQt6.

---

### Task 1: Pure functions — `sanitize_filename`, `parse_json3`, `build_paragraphs`

**Files:** Create `cleanmint/core/transcriber.py`, `tests/test_transcriber.py`

- [ ] **Step 1: Failing tests** — `tests/test_transcriber.py`:

```python
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "cleanmint"))
from core.transcriber import sanitize_filename, parse_json3, build_paragraphs

def test_sanitize_strips_bad_chars():
    assert sanitize_filename('A/B: "quote" <x>?') == "A_B_ _quote_ _x__"

def test_sanitize_truncates():
    assert len(sanitize_filename("x" * 200)) == 80

def test_parse_json3_basic():
    data = {"events": [
        {"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "Hello "}, {"utf8": "world."}]},
        {"tStartMs": 1200, "segs": [{"utf8": "\n"}]},                      # newline-only: skipped
        {"tStartMs": 1500, "aAppend": 1, "segs": [{"utf8": "world."}]},    # rolling dup: skipped
        {"tStartMs": 2000, "dDurationMs": 900, "segs": [{"utf8": "Bye."}]},
    ]}
    assert parse_json3(json.dumps(data)) == [(0, "Hello world."), (2000, "Bye.")]

def test_build_paragraphs_splits_on_gap():
    segs = [(0, "One."), (1000, "Two."), (9000, "Three.")]   # 8 s gap
    assert build_paragraphs(segs, min_chars=1) == ["One. Two.", "Three."]

def test_build_paragraphs_splits_on_length():
    segs = [(i * 1000, "Word soup sentence.") for i in range(100)]
    paras = build_paragraphs(segs)
    assert all(len(p) <= 1000 for p in paras) and len(paras) > 1
```

- [ ] **Step 2:** `venv/bin/python -m pytest tests/test_transcriber.py -q` → FAIL (no module)
- [ ] **Step 3: Implement** in `core/transcriber.py`:

```python
"""YouTube Transcriber engine: download, captions, whisper fallback, txt/pdf output."""
import json
import re

PAUSE_GAP_MS = 2500      # silence gap that starts a new paragraph
MAX_PARA_CHARS = 900     # hard wrap for paragraph length
MIN_PARA_CHARS = 200     # don't split on gap below this length

def sanitize_filename(title: str) -> str:
    return re.sub(r'[/\\:*?"<>|\n\t]', "_", title).strip()[:80]

def parse_json3(text: str) -> list[tuple[int, str]]:
    """json3 caption events -> [(start_ms, text)]; skips rolling-window dups."""
    out = []
    for ev in json.loads(text).get("events", []):
        if ev.get("aAppend"):
            continue
        segtext = "".join(s.get("utf8", "") for s in ev.get("segs", []))
        segtext = segtext.replace("\n", " ").strip()
        if segtext:
            out.append((int(ev.get("tStartMs", 0)), segtext))
    return out

def build_paragraphs(segments: list[tuple[int, str]],
                     min_chars: int = MIN_PARA_CHARS) -> list[str]:
    """Merge timed text chunks into readable paragraphs (gap- and length-based)."""
    paras, cur, last_ms = [], "", None
    for start_ms, text in segments:
        gap_split = (last_ms is not None and start_ms - last_ms > PAUSE_GAP_MS
                     and len(cur) >= min_chars)
        if cur and (gap_split or len(cur) + len(text) > MAX_PARA_CHARS):
            paras.append(cur)
            cur = ""
        cur = f"{cur} {text}".strip()
        last_ms = start_ms
    if cur:
        paras.append(cur)
    return paras
```

- [ ] **Step 4:** rerun → PASS
- [ ] **Step 5:** `git add -A && git commit -m "feat: transcriber pure functions (parse/paragraphs/filename)"`

### Task 2: yt-dlp wrappers — metadata, download, captions

**Files:** Modify `cleanmint/core/transcriber.py`; extend `tests/test_transcriber.py`

- [ ] **Step 1: Failing tests** (command construction + caption-lang pick; no network):

```python
from core.transcriber import VideoMeta, pick_caption_track, _ytdlp_cmd

def test_ytdlp_cmd_cookies():
    cmd = _ytdlp_cmd(["--dump-json", "URL"], cookies=True)
    assert cmd[0] == "yt-dlp" and "--cookies-from-browser" in cmd and "chrome" in cmd

def test_pick_caption_prefers_manual():
    info = {"subtitles": {"en": [{"ext": "json3"}]},
            "automatic_captions": {"en": [{"ext": "json3"}]}}
    assert pick_caption_track(info) == ("en", False)

def test_pick_caption_auto_fallback():
    info = {"subtitles": {}, "automatic_captions": {"en-orig": [{}], "de": [{}]}}
    assert pick_caption_track(info) == ("en-orig", True)

def test_pick_caption_none():
    assert pick_caption_track({"subtitles": {}, "automatic_captions": {"fr": [{}]}}) is None
```

- [ ] **Step 2:** run → FAIL
- [ ] **Step 3: Implement:**

```python
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

DOWNLOADS = Path.home() / "Downloads"

@dataclass
class VideoMeta:
    title: str
    channel: str
    upload_date: str      # YYYY-MM-DD or ""
    duration: int         # seconds
    url: str
    info: dict            # full yt-dlp info dict

def _ytdlp_cmd(args: list[str], cookies: bool = False) -> list[str]:
    cmd = ["yt-dlp", "--no-playlist"]
    if cookies:
        cmd += ["--cookies-from-browser", "chrome"]
    return cmd + args

def fetch_metadata(url: str, cookies: bool = False) -> VideoMeta:
    res = subprocess.run(_ytdlp_cmd(["--dump-json", "--skip-download", url], cookies),
                         capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip().splitlines()[-1] if res.stderr else "yt-dlp failed")
    info = json.loads(res.stdout)
    d = info.get("upload_date", "")
    return VideoMeta(
        title=info.get("title", "video"),
        channel=info.get("channel") or info.get("uploader", ""),
        upload_date=f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else "",
        duration=int(info.get("duration") or 0),
        url=url, info=info)

def pick_caption_track(info: dict) -> tuple[str, bool] | None:
    """Return (lang, is_auto) preferring manual English subs, else auto; None if neither."""
    for key, is_auto in (("subtitles", False), ("automatic_captions", True)):
        for lang in (info.get(key) or {}):
            if lang == "en" or lang.startswith("en-"):
                return (lang, is_auto)
    return None

def download_video(url: str, cookies: bool = False, line_cb=None) -> Path:
    """Download to ~/Downloads, streaming progress lines to line_cb. Returns file path."""
    cmd = _ytdlp_cmd(["--newline", "-P", str(DOWNLOADS),
                      "-o", "%(title).80s [%(id)s].%(ext)s",
                      "--print", "after_move:filepath", "--no-simulate", url], cookies)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    path = None
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith("/"):
            path = line
        elif line_cb:
            line_cb(line)
    if proc.wait() != 0 or not path:
        raise RuntimeError("Video download failed — see log above")
    return Path(path)

def fetch_captions(url: str, lang: str, is_auto: bool,
                   cookies: bool = False) -> list[tuple[int, str]] | None:
    with tempfile.TemporaryDirectory() as td:
        flag = "--write-auto-subs" if is_auto else "--write-subs"
        cmd = _ytdlp_cmd(["--skip-download", flag, "--sub-langs", lang,
                          "--sub-format", "json3", "-P", td, "-o", "subs", url], cookies)
        subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        files = list(Path(td).glob("subs*.json3"))
        if not files:
            return None
        segs = parse_json3(files[0].read_text(encoding="utf-8"))
        return segs or None
```

- [ ] **Step 4:** run → PASS. **Step 5:** commit `feat: transcriber yt-dlp wrappers`

### Task 3: Whisper fallback + txt/pdf writers

**Files:** Modify `cleanmint/core/transcriber.py`; extend tests

- [ ] **Step 1: Failing tests** (writers only; whisper is network/model-bound, not unit tested):

```python
from core.transcriber import write_txt, write_pdf, whisper_available

def _meta():
    return VideoMeta("My Video", "Chan", "2026-01-01", 65, "http://u", {})

def test_write_txt(tmp_path):
    p = write_txt(_meta(), ["Para one.", "Para two."], "YouTube captions", tmp_path)
    text = p.read_text()
    assert p.name == "My Video-transcript.txt"
    assert "My Video" in text and "Para one.\n\nPara two." in text and "1:05" in text

def test_write_pdf(tmp_path):
    p = write_pdf(_meta(), ["Para <one> & two."], "Whisper (medium)", tmp_path)
    assert p.suffix == ".pdf" and p.stat().st_size > 500

def test_whisper_available_bool():
    assert isinstance(whisper_available(), bool)
```

- [ ] **Step 2:** run → FAIL
- [ ] **Step 3: Implement:**

```python
def _fmt_duration(sec: int) -> str:
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def _header_lines(meta: VideoMeta, source: str) -> list[str]:
    return [meta.title,
            f"Channel: {meta.channel}",
            f"Date: {meta.upload_date}    Duration: {_fmt_duration(meta.duration)}",
            f"URL: {meta.url}",
            f"Transcript source: {source}"]

def write_txt(meta: VideoMeta, paragraphs: list[str], source: str,
              dest: Path = DOWNLOADS) -> Path:
    path = dest / f"{sanitize_filename(meta.title)}-transcript.txt"
    hdr = _header_lines(meta, source)
    body = "\n".join(hdr) + "\n" + "=" * 60 + "\n\n" + "\n\n".join(paragraphs) + "\n"
    path.write_text(body, encoding="utf-8")
    return path

def write_pdf(meta: VideoMeta, paragraphs: list[str], source: str,
              dest: Path = DOWNLOADS) -> Path:
    from xml.sax.saxutils import escape
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    path = dest / f"{sanitize_filename(meta.title)}-transcript.pdf"
    styles = getSampleStyleSheet()
    body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=11,
                                leading=16, spaceAfter=10)
    meta_style = ParagraphStyle("Meta", parent=styles["Normal"], fontSize=9,
                                textColor="#666666", spaceAfter=2)
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2 * cm,
                            rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
                            title=meta.title)
    story = [Paragraph(escape(meta.title), styles["Title"])]
    for line in _header_lines(meta, source)[1:]:
        story.append(Paragraph(escape(line), meta_style))
    story.append(Spacer(1, 18))
    story += [Paragraph(escape(p), body_style) for p in paragraphs]
    doc.build(story)
    return path

def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False

def whisper_transcribe(media_path: Path, duration: int,
                       progress_cb=None) -> list[tuple[int, str]]:
    """Transcribe with faster-whisper medium int8. progress_cb(pct: int)."""
    from faster_whisper import WhisperModel
    model = WhisperModel("medium", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(media_path), beam_size=5,
                                       temperature=0, vad_filter=True)
    out = []
    for seg in segments:            # generator — transcription happens here
        text = seg.text.strip()
        if text:
            out.append((int(seg.start * 1000), text))
        if progress_cb and duration:
            progress_cb(min(99, int(seg.end / duration * 100)))
    return out
```

- [ ] **Step 4:** run → PASS. **Step 5:** commit `feat: transcriber whisper fallback + txt/pdf writers`

### Task 4: UI page + navigation

**Files:** Create `cleanmint/ui/transcriber_page.py`; modify `cleanmint/ui/main_window.py` (NAV_ITEMS + `_create_page`); extend `tests/test_ui_imports.py` pattern via import smoke test in `tests/test_transcriber.py`

- [ ] **Step 1: `TranscribeWorker`** (QThread, in `transcriber_page.py`) — orchestrates the whole pipeline:

```python
class TranscribeWorker(QThread):
    log = pyqtSignal(str)            # status line for the log pane
    pct = pyqtSignal(int)            # progress bar 0-100
    done = pyqtSignal(str, str)      # txt path, pdf path
    error = pyqtSignal(str)

    def __init__(self, url: str, cookies: bool):
        super().__init__()
        self._url, self._cookies = url, cookies

    def run(self):
        try:
            from core import transcriber as T
            self.log.emit("Fetching video info…"); self.pct.emit(2)
            meta = T.fetch_metadata(self._url, self._cookies)
            self.log.emit(f"✔ {meta.title}  ({meta.channel}, {meta.duration // 60} min)")

            self.log.emit("Downloading video to ~/Downloads…"); self.pct.emit(5)
            T.download_video(self._url, self._cookies, line_cb=self._dl_line)
            self.log.emit("✔ Video saved to ~/Downloads")

            track = T.pick_caption_track(meta.info)
            segs = source = None
            if track:
                lang, is_auto = track
                kind = "auto-captions" if is_auto else "captions"
                self.log.emit(f"Found YouTube {kind} ({lang}) — using them…"); self.pct.emit(60)
                segs = T.fetch_captions(self._url, lang, is_auto, self._cookies)
                source = f"YouTube {kind} ({lang})"
            if not segs:
                if not T.whisper_available():
                    raise RuntimeError(
                        "No captions on this video and faster-whisper is not installed.\n"
                        "Install it with:  venv/bin/pip install faster-whisper")
                self.log.emit("No captions — transcribing locally with Whisper (medium). "
                              "This takes roughly the video's length…")
                self.pct.emit(10)
                segs = T.whisper_transcribe(  # noqa — path from download step
                    self._video_path, meta.duration, progress_cb=self.pct.emit)
                source = "Whisper (medium, local)"

            self.log.emit("Formatting transcript…"); self.pct.emit(95)
            paras = T.build_paragraphs(segs)
            txt = T.write_txt(meta, paras, source)
            pdf = T.write_pdf(meta, paras, source)
            self.pct.emit(100)
            self.done.emit(str(txt), str(pdf))
        except Exception as e:                      # noqa: BLE001 — surfaced to UI
            self.error.emit(str(e))
```

(`download_video` return value stored as `self._video_path`; `_dl_line` regex-parses `%` into `self.pct` range 5–55 and forwards `[download]` lines to `self.log` throttled.)

- [ ] **Step 2: `TranscriberPage`** — header label, URL `QLineEdit`, cookies `QCheckBox("Use Chrome cookies (age-restricted videos)")`, `Transcribe` accent button, `QProgressBar` (hidden until run), read-only `QPlainTextEdit` log, result label with final file paths. Button disabled while worker runs. Styling/objectNames copied from `snapshot_page.py`.
- [ ] **Step 3: Register page** — `main_window.py`: add `("transcriber", "▶  Transcriber")` to `NAV_ITEMS` after `("screenshot", …)`; add branch in `_create_page`:

```python
        elif key == "transcriber":
            from ui.transcriber_page import TranscriberPage
            return TranscriberPage()
```

- [ ] **Step 4: Smoke test** in `tests/test_transcriber.py` (guarded like `test_ui_imports.py`) — import `ui.transcriber_page` under offscreen Qt; assert `TranscriberPage` constructs.
- [ ] **Step 5:** full suite `venv/bin/python -m pytest tests/ -q` → all pass. Commit `feat: Transcriber page UI + navigation`.

### Task 5: Dependency + end-to-end verify

- [ ] **Step 1:** `venv/bin/pip install faster-whisper` (app must still work without it — verify lazy import by running tests before install).
- [ ] **Step 2:** End-to-end: run headless pipeline on a short public captioned video (e.g. `https://www.youtube.com/watch?v=jNQXAC9IVRw`, 19 s) via a scratch script calling core functions; confirm video + `.txt` + `.pdf` appear in `~/Downloads` and content looks right.
- [ ] **Step 3:** Launch app (`bash cleanmint/run.sh`), visually confirm page renders and Transcribe works.
- [ ] **Step 4:** Update `CLAUDE.md` working-state section + commit `docs: transcriber notes`.

## Self-review
- Spec coverage: metadata/download/captions/whisper/txt/pdf/errors/progress/tests all mapped (Tasks 1–5). ✔
- No placeholders; types consistent (`VideoMeta`, `list[tuple[int, str]]` shared by captions & whisper paths). ✔
- Single subsystem, one plan. ✔
