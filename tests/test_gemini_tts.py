"""Gemini TTS contract + gotcha tests.

Locks in:
  - Gemini does NOT emit per-word timestamps (only raw PCM audio); we
    distribute words evenly across total duration.
  - Returned wav is 24kHz mono 16-bit PCM.
  - Missing AI_VIDEO_GEMINI_KEY raises a clear RuntimeError, not a
    ModuleNotFoundError or stub success.
  - The default voice is `Kore`; passing `voice='default'` from
    `generate_tts` resolves to Kore (not the literal string 'default').
"""

from __future__ import annotations

import wave

import pytest

from parallax import gemini_tts


def test_evenly_distributed_words_splits_text_uniformly():
    words = gemini_tts._evenly_distributed_words("hello world this is a test", 6.0)
    assert [w["word"] for w in words] == ["hello", "world", "this", "is", "a", "test"]
    assert words[0]["start"] == 0.0
    assert words[0]["end"] == 1.0
    assert words[5]["end"] == 6.0


def test_evenly_distributed_words_returns_empty_for_empty_text():
    assert gemini_tts._evenly_distributed_words("", 5.0) == []
    assert gemini_tts._evenly_distributed_words("nonempty", 0.0) == []


def test_synthesize_raises_when_key_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("AI_VIDEO_GEMINI_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="AI_VIDEO_GEMINI_KEY|GEMINI_API_KEY"):
        gemini_tts.synthesize("hi", out_dir=tmp_path)


def test_synthesize_writes_wav_with_expected_format(monkeypatch, tmp_path):
    """Mock the genai client to return a known PCM payload and verify the
    wav header matches our 24kHz mono 16-bit contract."""
    pcm = b"\x00\x01" * (gemini_tts.PCM_SAMPLE_RATE * 2)  # 2 seconds of audio

    class _FakePart:
        def __init__(self, data): self.inline_data = type("d", (), {"data": data, "mime_type": "audio/L16;rate=24000"})()
    class _FakeContent:
        def __init__(self, parts): self.parts = parts
    class _FakeCandidate:
        def __init__(self, content): self.content = content
    class _FakeResponse:
        def __init__(self, parts): self.candidates = [_FakeCandidate(_FakeContent(parts))]
    class _FakeModels:
        def generate_content(self, *, model, contents, config):
            return _FakeResponse([_FakePart(pcm)])
    class _FakeClient:
        def __init__(self, *, api_key): self.models = _FakeModels()

    monkeypatch.setenv("AI_VIDEO_GEMINI_KEY", "fake-key")
    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _FakeClient)

    path, words, dur = gemini_tts.synthesize("two-second test", voice="Kore", out_dir=tmp_path)
    assert path.exists() and path.suffix == ".wav"

    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2  # 16-bit
        assert w.getframerate() == 24_000
    assert abs(dur - 2.0) < 0.01
    assert len(words) == 2  # split() on whitespace → ["two-second", "test"]


def test_rapid_fire_preset_is_default_on_generate_tts(monkeypatch, tmp_path):
    """Calling `generate_tts(alias='gemini-flash-tts')` with no style applies
    the rapid_fire preset by default — verified faster TTS for ads."""
    pcm = b"\x00\x01" * 24_000  # 1s
    captured = {}

    class _FakePart:
        def __init__(self, data):
            self.inline_data = type("d", (), {"data": data, "mime_type": "audio/L16;rate=24000"})()
    class _FakeContent:
        def __init__(self, parts): self.parts = parts
    class _FakeCandidate:
        def __init__(self, content): self.content = content
    class _FakeResponse:
        def __init__(self, parts): self.candidates = [_FakeCandidate(_FakeContent(parts))]
    class _FakeModels:
        def generate_content(self, *, model, contents, config):
            captured["spoken"] = contents
            return _FakeResponse([_FakePart(pcm)])
    class _FakeClient:
        def __init__(self, *, api_key): self.models = _FakeModels()

    monkeypatch.setenv("AI_VIDEO_GEMINI_KEY", "fake")
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    monkeypatch.delenv("PARALLAX_TEST_MODE", raising=False)
    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _FakeClient)

    from parallax import openrouter, runlog
    runlog.start_run("style-default")
    try:
        openrouter.generate_tts("Buy now. Limited time.", alias="gemini-flash-tts", out_dir=tmp_path)
    finally:
        runlog.end_run()

    assert captured["spoken"].startswith("Read this as a rapid-fire commercial"), (
        f"expected rapid_fire preset prefix, got: {captured['spoken'][:80]!r}"
    )
    # The original text must still appear after the directive
    assert "Buy now. Limited time." in captured["spoken"]


def test_explicit_style_natural_skips_directive(monkeypatch, tmp_path):
    """Passing `style='natural'` produces a bare prompt (no rapid_fire prefix),
    so users can opt out of the ad-default for non-ad copy."""
    pcm = b"\x00\x01" * 24_000
    captured = {}

    class _FakePart:
        def __init__(self, data):
            self.inline_data = type("d", (), {"data": data, "mime_type": "audio/L16;rate=24000"})()
    class _FakeContent:
        def __init__(self, parts): self.parts = parts
    class _FakeCandidate:
        def __init__(self, content): self.content = content
    class _FakeResponse:
        def __init__(self, parts): self.candidates = [_FakeCandidate(_FakeContent(parts))]
    class _FakeModels:
        def generate_content(self, *, model, contents, config):
            captured["spoken"] = contents
            return _FakeResponse([_FakePart(pcm)])
    class _FakeClient:
        def __init__(self, *, api_key): self.models = _FakeModels()

    monkeypatch.setenv("AI_VIDEO_GEMINI_KEY", "fake")
    monkeypatch.delenv("PARALLAX_TEST_MODE", raising=False)
    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _FakeClient)

    gemini_tts.synthesize("Once upon a time.", out_dir=tmp_path, style="natural")
    assert captured["spoken"] == "Once upon a time."  # no prefix


def test_freeform_style_hint_overrides_preset(monkeypatch, tmp_path):
    pcm = b"\x00\x01" * 24_000
    captured = {}

    class _FakePart:
        def __init__(self, data):
            self.inline_data = type("d", (), {"data": data, "mime_type": "audio/L16;rate=24000"})()
    class _FakeContent:
        def __init__(self, parts): self.parts = parts
    class _FakeCandidate:
        def __init__(self, content): self.content = content
    class _FakeResponse:
        def __init__(self, parts): self.candidates = [_FakeCandidate(_FakeContent(parts))]
    class _FakeModels:
        def generate_content(self, *, model, contents, config):
            captured["spoken"] = contents
            return _FakeResponse([_FakePart(pcm)])
    class _FakeClient:
        def __init__(self, *, api_key): self.models = _FakeModels()

    monkeypatch.setenv("AI_VIDEO_GEMINI_KEY", "fake")
    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _FakeClient)

    gemini_tts.synthesize(
        "Hello.", out_dir=tmp_path,
        style="rapid_fire",
        style_hint="Whisper this conspiratorially:",  # should win over preset
    )
    assert captured["spoken"].startswith("Whisper this conspiratorially: Hello.")


def test_unknown_style_raises_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_VIDEO_GEMINI_KEY", "fake")
    with pytest.raises(ValueError, match="Unknown TTS style"):
        gemini_tts.synthesize("hi", out_dir=tmp_path, style="not-a-real-preset")


def test_synthesize_raises_when_response_has_no_audio_parts(monkeypatch, tmp_path):
    class _FakeContent:
        def __init__(self): self.parts = []
    class _FakeCandidate:
        def __init__(self): self.content = _FakeContent()
    class _FakeResponse:
        def __init__(self): self.candidates = [_FakeCandidate()]
    class _FakeModels:
        def generate_content(self, **kw): return _FakeResponse()
    class _FakeClient:
        def __init__(self, *, api_key): self.models = _FakeModels()

    monkeypatch.setenv("AI_VIDEO_GEMINI_KEY", "fake-key")
    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _FakeClient)

    with pytest.raises(RuntimeError, match="no inline audio"):
        gemini_tts.synthesize("x", out_dir=tmp_path)
