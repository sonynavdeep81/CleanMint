"""
tests/test_transcriber.py — Transcriber engine validation (pytest style)

Pure functions only — no network, no yt-dlp, no whisper model downloads.
Run: venv/bin/python -m pytest tests/test_transcriber.py -q
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "cleanmint"))

from core.transcriber import sanitize_filename, parse_json3, build_paragraphs


def test_sanitize_strips_bad_chars():
    assert sanitize_filename('A/B: "quote" <x>?') == 'A_B_ _quote_ _x__'


def test_sanitize_truncates():
    assert len(sanitize_filename("x" * 200)) == 80


def test_parse_json3_basic():
    data = {"events": [
        {"tStartMs": 0, "dDurationMs": 1000,
         "segs": [{"utf8": "Hello "}, {"utf8": "world."}]},
        {"tStartMs": 1200, "segs": [{"utf8": "\n"}]},                    # newline-only
        {"tStartMs": 1500, "aAppend": 1, "segs": [{"utf8": "world."}]},  # rolling dup
        {"tStartMs": 2000, "dDurationMs": 900, "segs": [{"utf8": "Bye."}]},
    ]}
    assert parse_json3(json.dumps(data)) == [(0, "Hello world."), (2000, "Bye.")]


def test_parse_json3_no_events():
    assert parse_json3("{}") == []


def test_build_paragraphs_splits_on_gap():
    segs = [(0, "One."), (1000, "Two."), (9000, "Three.")]  # 8 s gap
    assert build_paragraphs(segs, min_chars=1) == ["One. Two.", "Three."]


def test_build_paragraphs_splits_on_length():
    segs = [(i * 1000, "Word soup sentence.") for i in range(100)]
    paras = build_paragraphs(segs)
    assert all(len(p) <= 1000 for p in paras) and len(paras) > 1


def test_build_paragraphs_empty():
    assert build_paragraphs([]) == []


# ---- yt-dlp wrappers (command construction only, no network) ----

from core.transcriber import _ytdlp_cmd, pick_caption_track  # noqa: E402


def test_ytdlp_cmd_cookies():
    cmd = _ytdlp_cmd(["--dump-json", "URL"], cookies=True)
    assert cmd[0] == "yt-dlp" and "--cookies-from-browser" in cmd and "chrome" in cmd


def test_ytdlp_cmd_no_cookies():
    cmd = _ytdlp_cmd(["URL"])
    assert "--cookies-from-browser" not in cmd and "--no-playlist" in cmd


def test_pick_caption_prefers_manual():
    info = {"subtitles": {"en": [{"ext": "json3"}]},
            "automatic_captions": {"en": [{"ext": "json3"}]}}
    assert pick_caption_track(info) == ("en", False)


def test_pick_caption_auto_fallback():
    info = {"subtitles": {}, "automatic_captions": {"en-orig": [{}], "de": [{}]}}
    assert pick_caption_track(info) == ("en-orig", True)


def test_pick_caption_none():
    info = {"subtitles": {}, "automatic_captions": {"fr": [{}]}}
    assert pick_caption_track(info) is None


# ---- writers + whisper availability ----

from core.transcriber import (  # noqa: E402
    VideoMeta, write_txt, write_pdf, whisper_available)


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


# ---- UI smoke test (offscreen) ----

def test_transcriber_page_constructs():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.transcriber_page import TranscriberPage
    page = TranscriberPage()
    assert page._go_btn.isEnabled()
    del app
