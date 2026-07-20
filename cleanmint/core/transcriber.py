"""
core/transcriber.py — YouTube Transcriber engine

Pipeline: fetch metadata → download video to ~/Downloads → use YouTube
captions if available (manual preferred over auto) → else transcribe
locally with faster-whisper (medium, int8) → write formatted .txt + .pdf
transcripts to ~/Downloads.

All functions are UI-free; the Qt worker in ui/transcriber_page.py drives them.
"""

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

DOWNLOADS = Path.home() / "Downloads"

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
        err = res.stderr.strip().splitlines()[-1] if res.stderr.strip() else "yt-dlp failed"
        raise RuntimeError(err)
    info = json.loads(res.stdout)
    d = info.get("upload_date") or ""
    return VideoMeta(
        title=info.get("title", "video"),
        channel=info.get("channel") or info.get("uploader") or "",
        upload_date=f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else "",
        duration=int(info.get("duration") or 0),
        url=url, info=info)


def pick_caption_track(info: dict) -> tuple[str, bool] | None:
    """Return (lang, is_auto) preferring manual English subs, else auto captions."""
    for key, is_auto in (("subtitles", False), ("automatic_captions", True)):
        for lang in (info.get(key) or {}):
            if lang == "en" or lang.startswith("en-"):
                return (lang, is_auto)
    return None


def download_video(url: str, cookies: bool = False, line_cb=None) -> Path:
    """Download to ~/Downloads, streaming yt-dlp output lines to line_cb."""
    cmd = _ytdlp_cmd(["--newline", "-P", str(DOWNLOADS),
                      "-o", "%(title).80s [%(id)s].%(ext)s",
                      "--print", "after_move:filepath", "--no-simulate", url], cookies)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    path = None
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith("/"):
            path = line
        elif line and line_cb:
            line_cb(line)
    if proc.wait() != 0 or not path:
        raise RuntimeError("Video download failed — see log above")
    return Path(path)


def fetch_captions(url: str, lang: str, is_auto: bool,
                   cookies: bool = False) -> list[tuple[int, str]] | None:
    """Download the chosen caption track as json3 and parse it. None on failure."""
    with tempfile.TemporaryDirectory() as td:
        flag = "--write-auto-subs" if is_auto else "--write-subs"
        cmd = _ytdlp_cmd(["--skip-download", flag, "--sub-langs", lang,
                          "--sub-format", "json3", "-P", td, "-o", "subs", url], cookies)
        subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        files = list(Path(td).glob("subs*.json3"))
        if not files:
            return None
        try:
            segs = parse_json3(files[0].read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return segs or None


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


# Non-Latin scripts ReportLab's built-in fonts can't render, mapped to a
# system TTF that covers them. Devanagari (Hindi/Marathi/etc. transcripts)
# is the practical case; extend as other scripts come up.
_SCRIPT_FONTS = {
    "NotoDevanagari": (
        re.compile(r"[ऀ-ॿ]+"),
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    ),
}
_registered_fonts: set[str] = set()


def _register_script_font(name: str, path: str) -> bool:
    if name in _registered_fonts:
        return True
    if not Path(path).is_file():
        return False
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    pdfmetrics.registerFont(TTFont(name, path))
    _registered_fonts.add(name)
    return True


def _markup_unicode(text: str) -> str:
    """Escape text for ReportLab and wrap runs in non-Latin scripts with a
    font that can render them, so they don't show as missing-glyph boxes."""
    from xml.sax.saxutils import escape
    for font_name, (pattern, font_path) in _SCRIPT_FONTS.items():
        if pattern.search(text) and not _register_script_font(font_name, font_path):
            continue
        if font_name not in _registered_fonts:
            continue
        parts, last = [], 0
        for m in pattern.finditer(text):
            parts.append(escape(text[last:m.start()]))
            parts.append(f'<font face="{font_name}">{escape(m.group())}</font>')
            last = m.end()
        parts.append(escape(text[last:]))
        text = "".join(parts)
        return text
    return escape(text)


def write_pdf(meta: VideoMeta, paragraphs: list[str], source: str,
              dest: Path = DOWNLOADS) -> Path:
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
                            rightMargin=2 * cm, topMargin=2 * cm,
                            bottomMargin=2 * cm, title=meta.title)
    story = [Paragraph(_markup_unicode(meta.title), styles["Title"])]
    for line in _header_lines(meta, source)[1:]:
        story.append(Paragraph(_markup_unicode(line), meta_style))
    story.append(Spacer(1, 18))
    story += [Paragraph(_markup_unicode(p), body_style) for p in paragraphs]
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
    """Transcribe with faster-whisper medium int8 on CPU. progress_cb(pct)."""
    from faster_whisper import WhisperModel
    model = WhisperModel("medium", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        str(media_path), beam_size=5, vad_filter=True,
        temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
        condition_on_previous_text=False,
        compression_ratio_threshold=2.4)
    out = []
    for seg in segments:            # generator — transcription happens here
        text = seg.text.strip()
        if text:
            out.append((int(seg.start * 1000), text))
        if progress_cb and duration:
            progress_cb(min(99, int(seg.end / duration * 100)))
    return out
