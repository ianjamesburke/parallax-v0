"""Voiceover pipeline characterization: generate_voiceover, _trim_long_pauses,
_mock_voiceover.

Locks in:
  - generate_voiceover always routes through openrouter.generate_tts with
    alias=tts-mini and the supplied voice name.
  - _trim_long_pauses collapses gaps > max_gap_s to keep_gap_s and shifts
    word timestamps by the cumulative removed duration.
  - PARALLAX_TEST_MODE produces a synthetic silence mp3 + word table.
  - voiceover.generate_voiceover does NOT accept a `speed` kwarg — speed
    adjustment lives in `audio.speedup` and runs as `stage_speed_adjust`.

Network calls (openrouter.generate_tts) are stubbed via monkeypatch.
"""

from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path

import pytest

from parallax import voiceover
from parallax.voiceover import generate_voiceover_dict


def _make_silent_mp3(path: Path, duration_s: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"anullsrc=cl=mono:r=44100",
         "-t", str(duration_s), "-c:a", "libmp3lame", "-b:a", "64k", str(path)],
        check=True, capture_output=True,
    )


def _probe_duration(path: Path) -> float:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    return float(p.stdout.strip())


# ─── _mock_voiceover ─────────────────────────────────────────────────────


def test_mock_voiceover_writes_silence_mp3_and_words(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path))

    out = json.loads(voiceover.generate_voiceover("hello world today", out_dir=str(tmp_path)))
    audio = Path(out["audio_path"])
    words_path = Path(out["words_path"])
    assert audio.exists() and audio.suffix == ".mp3"
    assert words_path.exists()
    assert len(out["words"]) == 3
    assert out["words"][0]["word"] == "hello"
    assert out["total_duration_s"] > 0


# ─── voiceover signature: speed kwarg is gone ────────────────────────────


def test_generate_voiceover_does_not_accept_speed_kwarg():
    sig = inspect.signature(voiceover.generate_voiceover)
    assert "speed" not in sig.parameters, (
        "voiceover.generate_voiceover must not accept `speed` — speed "
        "adjustment lives in audio.speedup / stage_speed_adjust now."
    )


def test_apply_atempo_helper_removed():
    assert not hasattr(voiceover, "_apply_atempo"), (
        "voiceover._apply_atempo must be removed; use audio.speedup."
    )


# ─── _trim_long_pauses ───────────────────────────────────────────────────


def test_trim_long_pauses_no_gaps_passthrough(tmp_path):
    audio = tmp_path / "in.mp3"
    _make_silent_mp3(audio, 1.0)
    words = [
        {"word": "a", "start": 0.0, "end": 0.3},
        {"word": "b", "start": 0.4, "end": 0.7},  # 0.1s gap, below default 0.4
    ]
    out_path = tmp_path / "out.mp3"
    adjusted, dur = voiceover._trim_long_pauses(audio, words, out_path)
    assert adjusted == words
    assert out_path.exists()


def test_trim_long_pauses_collapses_2s_gap(tmp_path):
    """Synthetic 4s silence + 2s gap between words → output should be ~2s shorter."""
    audio = tmp_path / "in.mp3"
    _make_silent_mp3(audio, 4.0)
    words = [
        {"word": "a", "start": 0.0, "end": 0.5},
        {"word": "b", "start": 2.5, "end": 3.0},  # 2.0s gap
        {"word": "c", "start": 3.0, "end": 4.0},
    ]
    out_path = tmp_path / "out.mp3"
    adjusted, dur = voiceover._trim_long_pauses(
        audio, words, out_path, max_gap_s=0.4, keep_gap_s=0.1,
    )

    assert adjusted[0]["start"] == 0.0
    assert adjusted[0]["end"] == 0.5
    assert abs(adjusted[1]["start"] - 0.6) < 0.01
    assert abs(adjusted[1]["end"] - 1.1) < 0.01
    assert abs(adjusted[2]["start"] - 1.1) < 0.01

    actual_dur = _probe_duration(out_path)
    assert abs(actual_dur - 2.1) < 0.2


def test_trim_long_pauses_empty_words(tmp_path):
    audio = tmp_path / "in.mp3"
    _make_silent_mp3(audio, 0.5)
    out_path = tmp_path / "out.mp3"
    adjusted, dur = voiceover._trim_long_pauses(audio, [], out_path)
    assert adjusted == []
    assert dur == 0.0
    assert out_path.exists()


# ─── generate_voiceover routing ──────────────────────────────────────────


def test_generate_voiceover_routes_to_gemini_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("PARALLAX_TEST_MODE", raising=False)
    captured = {}

    def fake_tts(*, text, alias, voice, out_dir, style, style_hint):
        captured["alias"] = alias
        captured["voice"] = voice
        raw = Path(out_dir) / "raw_tts.mp3"
        _make_silent_mp3(raw, 1.0)
        return str(raw), [{"word": "hi", "start": 0.0, "end": 1.0}], 1.0

    from parallax import openrouter
    monkeypatch.setattr(openrouter, "generate_tts", fake_tts)

    out = json.loads(voiceover.generate_voiceover(
        "hi", voice="nova", out_dir=str(tmp_path),
    ))
    assert captured["alias"] == "tts-mini"
    assert captured["voice"] == "nova"
    assert Path(out["audio_path"]).exists()


def test_generate_voiceover_passes_style_through(tmp_path, monkeypatch):
    monkeypatch.delenv("PARALLAX_TEST_MODE", raising=False)
    captured = {}

    def fake_tts(*, text, alias, voice, out_dir, style, style_hint):
        captured["style"] = style
        captured["style_hint"] = style_hint
        raw = Path(out_dir) / "raw_tts.mp3"
        _make_silent_mp3(raw, 0.5)
        return str(raw), [{"word": "x", "start": 0.0, "end": 0.5}], 0.5

    from parallax import openrouter
    monkeypatch.setattr(openrouter, "generate_tts", fake_tts)

    voiceover.generate_voiceover(
        "x", voice="nova", out_dir=str(tmp_path),
        style="rapid_fire", style_hint="urgent",
    )
    assert captured["style"] == "rapid_fire"
    assert captured["style_hint"] == "urgent"


def test_generate_voiceover_rejects_speed_kwarg(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path))
    with pytest.raises(TypeError):
        voiceover.generate_voiceover("hi", out_dir=str(tmp_path), speed=1.5)


# ─── generate_voiceover_dict: object-level API ───────────────────────────


def test_generate_voiceover_dict_returns_dict_in_test_mode(tmp_path, monkeypatch):
    """generate_voiceover_dict returns a Python dict, not a JSON string."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path))
    result = generate_voiceover_dict("hello world", out_dir=str(tmp_path))
    assert isinstance(result, dict)
    assert "audio_path" in result
    assert "words" in result
    assert "total_duration_s" in result
    assert isinstance(result["words"], list)


def test_generate_voiceover_dict_same_data_as_json_wrapper(tmp_path, monkeypatch):
    """Object API and JSON-string API produce identical data."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path))
    # Run dict version
    dict_result = generate_voiceover_dict("hello world", out_dir=str(tmp_path))
    # Run JSON version on a fresh dir to avoid file collision
    tmp2 = tmp_path / "json_run"
    tmp2.mkdir()
    json_result = json.loads(voiceover.generate_voiceover("hello world", out_dir=str(tmp2)))
    # Keys match; paths differ (different out_dir) but structure is identical
    assert set(dict_result.keys()) == set(json_result.keys())
    assert dict_result["words"] == json_result["words"]
    assert dict_result["total_duration_s"] == json_result["total_duration_s"]
