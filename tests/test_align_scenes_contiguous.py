"""Regression test for align_scenes contiguous-coverage invariant.

Catches the "voiceover tail gets clipped" bug: when a forced-aligned
words JSON contains leading silence (first word starts at 0.31s) and
trailing silence (last word ends at 13.37s while the wav is 13.65s),
the assembled video must cover [0, total_duration_s] with no gaps. If
scene 0 starts at the first word instead of 0, the front gets misaligned;
if the last scene ends at the last word instead of total, the mux's
`-shortest` flag clips the audio tail.
"""

from __future__ import annotations

import json

from parallax.assembly import align_scenes


def _word(w: str, start: float, end: float) -> dict:
    return {"word": w, "start": start, "end": end}


def test_scene_0_absorbs_leading_silence():
    """First word at 0.31s must NOT cause scene 0 to start at 0.31."""
    scenes = [{"index": 0, "vo_text": "Hello world."}]
    words = [_word("Hello", 0.31, 0.55), _word("world", 0.60, 1.20)]
    payload = {"words": words, "total_duration_s": 1.50}
    out = json.loads(align_scenes(json.dumps(scenes), json.dumps(payload)))
    assert out[0]["start_s"] == 0.0


def test_last_scene_extends_to_total_duration():
    """Last word ending before audio end must NOT leave the tail uncovered."""
    scenes = [{"index": 0, "vo_text": "Hello world."}]
    words = [_word("Hello", 0.0, 0.40), _word("world", 0.45, 1.00)]
    payload = {"words": words, "total_duration_s": 2.00}
    out = json.loads(align_scenes(json.dumps(scenes), json.dumps(payload)))
    assert out[-1]["end_s"] == 2.0
    assert out[-1]["duration_s"] == 2.0


def test_scenes_are_contiguous_no_gaps():
    """Every scene starts where the previous one ended."""
    scenes = [
        {"index": 0, "vo_text": "Hello there."},
        {"index": 1, "vo_text": "How are you."},
    ]
    words = [
        _word("Hello", 0.30, 0.60), _word("there", 0.65, 1.10),
        _word("How", 1.50, 1.70), _word("are", 1.75, 1.95), _word("you", 2.00, 2.40),
    ]
    payload = {"words": words, "total_duration_s": 3.00}
    out = json.loads(align_scenes(json.dumps(scenes), json.dumps(payload)))
    assert out[0]["start_s"] == 0.0
    assert out[1]["start_s"] == out[0]["end_s"]   # contiguous
    assert out[-1]["end_s"] == 3.0                # extended to total


def test_durations_sum_to_total_audio():
    """Sum of scene durations must equal the audio's total_duration_s."""
    scenes = [
        {"index": 0, "vo_text": "First scene words."},
        {"index": 1, "vo_text": "Second scene here."},
        {"index": 2, "vo_text": "Final words now."},
    ]
    words = [
        _word("First", 0.31, 0.60), _word("scene", 0.65, 0.90), _word("words", 0.95, 1.30),
        _word("Second", 1.50, 1.80), _word("scene", 1.85, 2.10), _word("here", 2.15, 2.50),
        _word("Final", 2.80, 3.10), _word("words", 3.15, 3.40), _word("now", 3.45, 3.70),
    ]
    payload = {"words": words, "total_duration_s": 4.00}
    out = json.loads(align_scenes(json.dumps(scenes), json.dumps(payload)))
    total = sum(s["duration_s"] for s in out)
    assert abs(total - 4.0) < 0.005   # allow rounding noise


def test_falls_back_to_last_word_end_when_total_missing():
    """Backwards-compat: bare list (no total_duration_s) → last word's end."""
    words = [_word("Hi", 0.10, 0.40), _word("there", 0.45, 0.90)]
    scenes = [{"index": 0, "vo_text": "Hi there."}]
    out = json.loads(align_scenes(json.dumps(scenes), json.dumps(words)))
    assert out[-1]["end_s"] == 0.9
