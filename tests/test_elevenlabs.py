"""Tests for the ElevenLabs synthesis module + openrouter integration."""

from __future__ import annotations

import pytest

from parallax import elevenlabs, openrouter, runlog


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    runlog.start_run("eleven-test")
    yield
    runlog.end_run()


def test_resolve_voice_alias_short_circuits_without_api_call():
    """Known shorthand aliases never touch the network."""
    assert elevenlabs.resolve_voice("george", api_key="unused") == "JBFqnCBsd6RMkjVDRZzb"
    # Case-insensitive
    assert elevenlabs.resolve_voice("BELLA", api_key="unused") == "EXAVITQu4vr4xnSDxMaL"


def test_resolve_voice_raw_id_passes_through():
    raw = "ABC123def456ghi789jk"  # 20 chars, alphanumeric
    assert elevenlabs.resolve_voice(raw, api_key="unused") == raw


def test_cost_for_uses_per_char_rate():
    # 1000 chars × $0.000166/char = $0.166
    assert elevenlabs.cost_for("a" * 1000) == 0.166


def test_words_from_alignment_groups_chars_to_words():
    words = elevenlabs._words_from_alignment(
        chars=["h", "i", " ", "y", "o", "u"],
        starts=[0.0, 0.1, 0.25, 0.4, 0.5, 0.6],
        total_duration=0.8,
    )
    assert [w["word"] for w in words] == ["hi", "you"]
    # First word's end is the space's timestamp (acoustic end), not next-word start
    assert words[0]["end"] == 0.25
    # Last word falls back to total_duration
    assert words[1]["end"] == 0.8


def test_eleven_test_mode_runs_without_key(monkeypatch, tmp_path):
    """voice='eleven:<id>' in test mode must not require AI_VIDEO_ELEVENLABS_KEY."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.delenv("AI_VIDEO_ELEVENLABS_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    path, words, dur = openrouter.generate_tts(
        "hello world", alias="gemini-flash-tts", voice="eleven:abc123", out_dir=tmp_path,
    )
    assert path.exists()
    assert dur > 0


def test_eleven_real_mode_missing_key_raises_runtime_error(monkeypatch, tmp_path):
    """Real-mode eleven without a key surfaces a clear error, not a stub."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "0")
    monkeypatch.delenv("AI_VIDEO_ELEVENLABS_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ElevenLabs requires"):
        openrouter.generate_tts(
            "hello", alias="gemini-flash-tts", voice="eleven:abc123", out_dir=tmp_path,
        )
