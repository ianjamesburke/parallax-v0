"""Voiceover pipeline characterization: generate_voiceover, _apply_atempo,
_trim_long_pauses, _mock_voiceover.

Locks in:
  - generate_voiceover always routes through openrouter.generate_tts with
    alias=tts-mini and the supplied voice name.
  - atempo scales word timestamps by 1/speed and writes the canonical mp3.
  - _trim_long_pauses collapses gaps > max_gap_s to keep_gap_s and shifts
    word timestamps by the cumulative removed duration.
  - PARALLAX_TEST_MODE produces a synthetic silence mp3 + word table.

Network calls (openrouter.generate_tts) are stubbed via monkeypatch.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from parallax import tools_video


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

    out = json.loads(tools_video.generate_voiceover("hello world today", out_dir=str(tmp_path)))
    audio = Path(out["audio_path"])
    words_path = Path(out["words_path"])
    assert audio.exists() and audio.suffix == ".mp3"
    assert words_path.exists()
    assert len(out["words"]) == 3
    assert out["words"][0]["word"] == "hello"
    assert out["total_duration_s"] > 0


# ─── _apply_atempo ───────────────────────────────────────────────────────


def test_apply_atempo_scales_word_timestamps(tmp_path):
    raw = tmp_path / "raw.mp3"
    _make_silent_mp3(raw, 2.0)
    words = [
        {"word": "a", "start": 0.0, "end": 0.5},
        {"word": "b", "start": 0.5, "end": 1.0},
        {"word": "c", "start": 1.0, "end": 2.0},
    ]
    out_path = tmp_path / "sped.mp3"
    sped, dur = tools_video._apply_atempo(raw, words, out_path, 2.0)

    # speed=2 → new timestamps are half the originals
    assert sped[0]["start"] == 0.0
    assert sped[0]["end"] == 0.25
    assert sped[2]["end"] == 1.0
    # output duration is roughly half (atempo scales by 1/speed)
    assert dur == 1.0
    assert out_path.exists()
    actual_dur = _probe_duration(out_path)
    assert abs(actual_dur - 1.0) < 0.15


def test_apply_atempo_failure_falls_back_to_raw(tmp_path):
    """Bad input still completes (renames raw to out)."""
    raw = tmp_path / "raw.mp3"
    raw.write_bytes(b"not-an-mp3")
    words = [{"word": "x", "start": 0.0, "end": 1.0}]
    out_path = tmp_path / "sped.mp3"
    # ffmpeg will fail; function falls back to renaming raw -> out
    sped, dur = tools_video._apply_atempo(raw, words, out_path, 2.0)
    # words unchanged on failure
    assert sped == words
    assert dur == 1.0


# ─── _trim_long_pauses ───────────────────────────────────────────────────


def test_trim_long_pauses_no_gaps_passthrough(tmp_path):
    audio = tmp_path / "in.mp3"
    _make_silent_mp3(audio, 1.0)
    words = [
        {"word": "a", "start": 0.0, "end": 0.3},
        {"word": "b", "start": 0.4, "end": 0.7},  # 0.1s gap, below default 0.4
    ]
    out_path = tmp_path / "out.mp3"
    adjusted, dur = tools_video._trim_long_pauses(audio, words, out_path)
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
    adjusted, dur = tools_video._trim_long_pauses(
        audio, words, out_path, max_gap_s=0.4, keep_gap_s=0.1,
    )

    # gap was 2.0s, keep_gap_s=0.1 → removed = 2.0 - 0.1 = 1.9s
    # word "b" originally at 2.5 → shifted back by 1.9 → 0.6
    assert adjusted[0]["start"] == 0.0
    assert adjusted[0]["end"] == 0.5
    assert abs(adjusted[1]["start"] - 0.6) < 0.01
    assert abs(adjusted[1]["end"] - 1.1) < 0.01
    # word "c" at 3.0 → shifted back by 1.9 → 1.1
    assert abs(adjusted[2]["start"] - 1.1) < 0.01

    # Output duration roughly = original - 1.9
    actual_dur = _probe_duration(out_path)
    assert abs(actual_dur - 2.1) < 0.2


def test_trim_long_pauses_empty_words(tmp_path):
    audio = tmp_path / "in.mp3"
    _make_silent_mp3(audio, 0.5)
    out_path = tmp_path / "out.mp3"
    adjusted, dur = tools_video._trim_long_pauses(audio, [], out_path)
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

    out = json.loads(tools_video.generate_voiceover(
        "hi", voice="Kore", speed=1.0, out_dir=str(tmp_path),
    ))
    assert captured["alias"] == "tts-mini"
    assert captured["voice"] == "Kore"
    assert Path(out["audio_path"]).exists()


def test_generate_voiceover_applies_atempo_when_speed_changes(tmp_path, monkeypatch):
    """With speed != 1.0, atempo runs and word timestamps scale."""
    monkeypatch.delenv("PARALLAX_TEST_MODE", raising=False)

    def fake_tts(*, text, alias, voice, out_dir, style, style_hint):
        raw = Path(out_dir) / "raw_tts.mp3"
        _make_silent_mp3(raw, 2.0)
        words = [
            {"word": "one", "start": 0.0, "end": 1.0},
            {"word": "two", "start": 1.0, "end": 2.0},
        ]
        return str(raw), words, 2.0

    from parallax import openrouter
    monkeypatch.setattr(openrouter, "generate_tts", fake_tts)

    out = json.loads(tools_video.generate_voiceover(
        "one two", voice="Kore", speed=2.0, out_dir=str(tmp_path),
    ))
    # atempo=2 halves the timestamps
    assert out["words"][0]["end"] == 0.5
    assert out["words"][1]["end"] == 1.0


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

    tools_video.generate_voiceover(
        "x", voice="Kore", speed=1.0, out_dir=str(tmp_path),
        style="rapid_fire", style_hint="urgent",
    )
    assert captured["style"] == "rapid_fire"
    assert captured["style_hint"] == "urgent"
