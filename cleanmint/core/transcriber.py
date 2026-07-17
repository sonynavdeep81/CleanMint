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
