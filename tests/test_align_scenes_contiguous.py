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

from parallax.assembly import align_scenes, align_scenes_obj


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


# ─── align_scenes_obj: object-level API ──────────────────────────────────


def test_align_scenes_obj_returns_list_of_dicts():
    """align_scenes_obj returns a Python list, not a JSON string."""
    scenes = [{"index": 0, "vo_text": "Hello world."}]
    words = [_word("Hello", 0.0, 0.5), _word("world", 0.6, 1.0)]
    payload = {"words": words, "total_duration_s": 1.5}
    result = align_scenes_obj(scenes, payload)
    assert isinstance(result, list)
    assert isinstance(result[0], dict)
    assert result[0]["start_s"] == 0.0
    assert result[-1]["end_s"] == 1.5


def test_align_scenes_obj_same_result_as_json_wrapper():
    """Object API and JSON-string API produce identical data."""
    scenes_data = [
        {"index": 0, "vo_text": "First scene."},
        {"index": 1, "vo_text": "Second scene."},
    ]
    words = [
        _word("First", 0.1, 0.4), _word("scene", 0.5, 0.8),
        _word("Second", 1.0, 1.3), _word("scene", 1.4, 1.7),
    ]
    payload = {"words": words, "total_duration_s": 2.0}

    obj_result = align_scenes_obj(
        [dict(s) for s in scenes_data],
        dict(payload, words=list(words)),
    )
    json_result = json.loads(
        align_scenes(json.dumps(scenes_data), json.dumps(payload))
    )
    assert obj_result == json_result


# ─── TTS word mismatch scenarios ─────────────────────────────────────────


def test_em_dash_in_plan_text_does_not_shift_cursor():
    """Punctuation-only tokens like '—' in vo_text shouldn't consume TTS words."""
    scenes = [
        {"index": 0, "vo_text": "He was exhausted — energy gone."},
        {"index": 1, "vo_text": "He felt better now."},
        {"index": 2, "vo_text": "Done here."},
    ]
    words = [
        _word("He", 0.0, 0.1), _word("was", 0.1, 0.2), _word("exhausted", 0.2, 0.5),
        _word("energy", 0.8, 1.0), _word("gone.", 1.0, 1.3),
        _word("He", 1.6, 1.7), _word("felt", 1.7, 1.9),
        _word("better", 1.9, 2.2), _word("now.", 2.2, 2.5),
        _word("Done", 2.7, 2.9), _word("here.", 2.9, 3.1),
    ]
    payload = {"words": words, "total_duration_s": 3.5}
    out = align_scenes_obj(scenes, payload)
    assert _scene_boundary_word(words, out[0]) == "gone."
    assert _scene_boundary_word(words, out[1]) == "now."


def test_tts_number_reformatting():
    """TTS turning 'ninety-day' → '90 -day' shouldn't break alignment."""
    scenes = [
        {"index": 0, "vo_text": "At the ninety-day mark things changed."},
        {"index": 1, "vo_text": "He felt great."},
        {"index": 2, "vo_text": "The end."},
    ]
    words = [
        _word("At", 0.0, 0.1), _word("the", 0.1, 0.2),
        _word("90", 0.2, 0.4), _word("-day", 0.4, 0.6),
        _word("mark", 0.6, 0.8), _word("things", 0.8, 1.0),
        _word("changed.", 1.0, 1.3),
        _word("He", 1.5, 1.6), _word("felt", 1.6, 1.8), _word("great.", 1.8, 2.1),
        _word("The", 2.3, 2.4), _word("end.", 2.4, 2.6),
    ]
    payload = {"words": words, "total_duration_s": 3.0}
    out = align_scenes_obj(scenes, payload)
    assert _scene_boundary_word(words, out[0]) == "changed."
    assert _scene_boundary_word(words, out[1]) == "great."


def test_tts_large_number_expansion():
    """'a hundred and twenty thousand' → '120 ,000' shouldn't drift cursor."""
    scenes = [
        {"index": 0, "vo_text": "Over a hundred and twenty thousand men tried it."},
        {"index": 1, "vo_text": "Results were clear."},
        {"index": 2, "vo_text": "Goodbye now."},
    ]
    words = [
        _word("Over", 0.0, 0.2), _word("120", 0.2, 0.5),
        _word(",000", 0.5, 0.7), _word("men", 0.7, 0.9),
        _word("tried", 0.9, 1.1), _word("it.", 1.1, 1.3),
        _word("Results", 1.5, 1.8), _word("were", 1.8, 2.0), _word("clear.", 2.0, 2.3),
        _word("Goodbye", 2.5, 2.7), _word("now.", 2.7, 2.9),
    ]
    payload = {"words": words, "total_duration_s": 3.0}
    out = align_scenes_obj(scenes, payload)
    assert _scene_boundary_word(words, out[0]) == "it."
    assert _scene_boundary_word(words, out[1]) == "clear."


def test_second_to_last_fallback_when_last_word_mangled():
    """If TTS mangles the last word, fall back to second-to-last."""
    scenes = [
        {"index": 0, "vo_text": "Take some Shilajit daily."},
        {"index": 1, "vo_text": "You will improve."},
        {"index": 2, "vo_text": "Trust me."},
    ]
    words = [
        _word("Take", 0.0, 0.2), _word("some", 0.2, 0.4),
        _word("Shiligid", 0.4, 0.8), _word("daily.", 0.8, 1.1),
        _word("You", 1.3, 1.4), _word("will", 1.4, 1.6), _word("improve.", 1.6, 2.0),
        _word("Trust", 2.2, 2.4), _word("me.", 2.4, 2.6),
    ]
    payload = {"words": words, "total_duration_s": 3.0}
    out = align_scenes_obj(scenes, payload)
    assert _scene_boundary_word(words, out[0]) == "daily."
    assert _scene_boundary_word(words, out[1]) == "improve."


def _scene_boundary_word(words: list[dict], scene: dict) -> str | None:
    """Return the TTS word text at a scene's end_s (pre-contiguous boundary)."""
    for w in words:
        if abs(w["end"] - scene["end_s"]) < 0.005:
            return w["word"]
    return None
