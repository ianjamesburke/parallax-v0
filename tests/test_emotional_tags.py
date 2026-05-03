"""Unit tests for strip_emotional_tags.

Tests both that stripping works correctly for chat_audio backends and that
tags are NOT stripped (pass through) for the Gemini speech backend.
"""

from __future__ import annotations

from parallax.openrouter import strip_emotional_tags


# ---------------------------------------------------------------------------
# strip_emotional_tags — correctness
# ---------------------------------------------------------------------------

def test_strips_single_tag():
    assert strip_emotional_tags("[dramatically] Hello world") == "Hello world"


def test_strips_multiple_tags():
    result = strip_emotional_tags("[dramatically] The end. [softly] Goodbye.")
    assert result == "The end. Goodbye."


def test_strips_mid_sentence_tag():
    result = strip_emotional_tags("Hello [whispering] world.")
    assert result == "Hello world."


def test_strips_multiword_tag():
    result = strip_emotional_tags("[speaking quickly] Three two one.")
    assert result == "Three two one."


def test_normalizes_double_spaces_after_strip():
    result = strip_emotional_tags("[tag] Hello  world")
    assert "  " not in result


def test_strips_tag_only_string():
    assert strip_emotional_tags("[dramatically]") == ""


def test_passthrough_when_no_tags():
    text = "Hello world. No tags here."
    assert strip_emotional_tags(text) == text


def test_strips_adjacent_tags():
    result = strip_emotional_tags("[a][b][c] text")
    assert result == "text"


# ---------------------------------------------------------------------------
# Tag pass-through for speech (Gemini) backend — generate_tts must NOT strip
# ---------------------------------------------------------------------------

def test_gemini_backend_passes_tags_through(monkeypatch, tmp_path):
    """generate_tts with tts-gemini must send emotional tags to _tts_real_speech
    without stripping — the Gemini model interprets them for expressive delivery.
    """
    import wave, struct
    from pathlib import Path
    from parallax import openrouter, runlog

    runlog.start_run("tag-passthrough-test")

    captured: dict = {}

    def fake_speech(text, *, voice, out_dir, model):
        captured["text"] = text
        # Write a minimal valid WAV so the caller can parse duration
        wav = tmp_path / "fake.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * 24000)
        words = [{"word": "hello", "start": 0.0, "end": 1.0}]
        return wav, words, 1.0

    monkeypatch.delenv("PARALLAX_TEST_MODE", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    monkeypatch.setattr(openrouter, "_tts_real_speech", fake_speech)

    tagged_text = "[dramatically] Everything changed. [softly] No one knew."
    openrouter.generate_tts(tagged_text, alias="tts-gemini", voice="Kore", out_dir=tmp_path)

    assert captured["text"] == tagged_text, (
        f"tts-gemini must pass tags through unchanged; got: {captured['text']!r}"
    )

    runlog.end_run()


def test_openai_backend_strips_tags_before_sending(monkeypatch, tmp_path):
    """generate_tts with tts-mini must strip emotional tags before passing text
    to _tts_real — gpt-audio-mini has no tag interpretation and would read brackets.
    """
    import wave
    from parallax import openrouter, runlog

    runlog.start_run("tag-strip-test")

    captured: dict = {}

    def fake_tts_real(text, *, voice, out_dir, model, style, style_hint):
        captured["text"] = text
        wav = tmp_path / "fake.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * 24000)
        return wav, [{"word": "hello", "start": 0.0, "end": 1.0}], 1.0

    monkeypatch.delenv("PARALLAX_TEST_MODE", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    monkeypatch.setattr(openrouter, "_tts_real", fake_tts_real)

    tagged_text = "[dramatically] Everything changed. [softly] No one knew."
    openrouter.generate_tts(tagged_text, alias="tts-mini", voice="nova", out_dir=tmp_path)

    assert "[" not in captured["text"], (
        f"tts-mini must strip tags before sending; got: {captured['text']!r}"
    )
    assert "Everything changed." in captured["text"]
    assert "No one knew." in captured["text"]

    runlog.end_run()
