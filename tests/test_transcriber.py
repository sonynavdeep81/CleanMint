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
