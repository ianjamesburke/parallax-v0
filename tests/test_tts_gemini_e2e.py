"""End-to-end paid integration tests for Gemini TTS (tts-gemini alias).

These tests hit the real OpenRouter API and cost real money.
They are skipped unless OPENROUTER_API_KEY is set in the environment.

Run with:
    OPENROUTER_API_KEY=<key> uv run pytest tests/test_tts_gemini_e2e.py -v
"""

from __future__ import annotations

import json
import os

import pytest

_NEEDS_KEY = pytest.mark.skipif(
    "OPENROUTER_API_KEY" not in os.environ,
    reason="real-mode integration test; set OPENROUTER_API_KEY to enable.",
)


@pytest.fixture(autouse=True)
def _real_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "0")
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    from parallax import runlog
    runlog.start_run("e2e-gemini-tts")
    yield
    runlog.end_run()


@_NEEDS_KEY
def test_gemini_tts_round_trip_plain(tmp_path):
    """Basic round-trip: send plain text, get back a WAV with word timings."""
    from parallax import openrouter

    path, words, duration = openrouter.generate_tts(
        "Hello. This is a test of the Gemini text to speech system.",
        alias="tts-gemini",
        voice="Kore",
        out_dir=tmp_path,
    )

    assert path.exists(), f"WAV not written: {path}"
    assert path.suffix == ".wav"
    head = path.read_bytes()[:4]
    assert head == b"RIFF", f"Expected WAV RIFF header, got: {head!r}"
    assert duration > 0.5, f"Duration implausibly short: {duration}"
    assert len(words) >= 5, f"Too few words returned: {words}"
    assert all("word" in w and "start" in w and "end" in w for w in words)


@_NEEDS_KEY
def test_gemini_tts_round_trip_with_emotional_tags(tmp_path):
    """Emotional tags pass through to Gemini and produce valid audio output."""
    from parallax import openrouter

    text = (
        "[dramatically] The Fast Talker. "
        "[speaking quickly] Built for speed. No pauses. No mercy. Buy now. "
        "[softly] Or don't."
    )

    path, words, duration = openrouter.generate_tts(
        text,
        alias="tts-gemini",
        voice="Fenrir",
        out_dir=tmp_path,
    )

    assert path.exists()
    assert path.read_bytes()[:4] == b"RIFF"
    assert duration > 0.5
    # Word count should match the spoken words (tags stripped from timing calc)
    spoken_words = [w for w in text.split() if not w.startswith("[")]
    assert len(words) >= len(spoken_words) * 0.5, (
        "Fewer words in timings than expected — alignment may have failed silently"
    )


@_NEEDS_KEY
def test_gemini_tts_fast_talker_sample(tmp_path):
    """Fast Talker sample — the canonical demo for tts-gemini expressive delivery.

    This is the reference output for the 'Fast Talker' style: Puck voice,
    rapid-fire delivery with emotional tags driving the pacing.
    """
    from parallax import openrouter

    text = (
        "[speaking quickly] "
        "This is the fastest pitch you've ever heard. "
        "No filler. No fluff. Just results. "
        "[with excitement] Results that matter. "
        "[speaking quickly] "
        "Sign up today. Cancel anytime. "
        "[dramatically] But you won't want to."
    )

    path, words, duration = openrouter.generate_tts(
        text,
        alias="tts-gemini",
        voice="Puck",
        out_dir=tmp_path,
    )

    assert path.exists()
    print(f"\n  Fast Talker sample: {path}")
    print(f"  Duration: {duration:.2f}s, words: {len(words)}")
    if words:
        wpm = len(words) / (duration / 60)
        print(f"  Effective WPM: {wpm:.0f}")

    assert path.read_bytes()[:4] == b"RIFF"
    assert duration > 0

    # Save a copy with a stable name for manual review
    import shutil
    sample_path = tmp_path / "fast_talker_sample.wav"
    shutil.copy2(path, sample_path)
    print(f"  Saved to: {sample_path}")


@_NEEDS_KEY
@pytest.mark.parametrize("voice", ["Kore", "Fenrir", "Aoede", "Puck", "Charon"])
def test_gemini_tts_voices_produce_audio(tmp_path, voice):
    """Smoke test: each key voice returns valid audio for a short sample."""
    from parallax import openrouter

    path, words, duration = openrouter.generate_tts(
        f"[dramatically] Testing voice {voice}. How does it sound?",
        alias="tts-gemini",
        voice=voice,
        out_dir=tmp_path,
    )

    assert path.exists()
    assert path.read_bytes()[:4] == b"RIFF"
    assert duration > 0
