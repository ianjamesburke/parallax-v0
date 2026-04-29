"""Gemini TTS contract + gotcha tests.

Locks in:
  - synthesize() routes to OpenRouter's /api/v1/audio/speech endpoint
    with `model=google/gemini-2.5-flash-preview-tts`, the requested
    voice, and `response_format=mp3`.
  - The response body is written verbatim to disk and ffprobe yields a
    positive duration.
  - Missing OPENROUTER_API_KEY raises a clear RuntimeError.
  - Style presets prepend the directive prefix before the spoken text.
  - Unknown style names raise loudly.
"""

from __future__ import annotations

import subprocess

import pytest

from parallax import gemini_tts


def _make_real_mp3_bytes(duration_s: float = 1.0) -> bytes:
    """Build a tiny but valid mp3 via ffmpeg piped through stdout — gives the
    `synthesize` ffprobe duration probe a real file to chew on."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"anullsrc=cl=mono:r=44100",
         "-t", str(duration_s), "-f", "mp3", "-c:a", "libmp3lame", "-b:a", "64k",
         "pipe:1"],
        capture_output=True, check=True,
    )
    return result.stdout


class _FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200, text: str = ""):
        self.content = content
        self.status_code = status_code
        self.text = text


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
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        gemini_tts.synthesize("hi", out_dir=tmp_path)


def test_synthesize_writes_response_body_and_calls_openrouter(monkeypatch, tmp_path):
    audio = _make_real_mp3_bytes(1.0)
    captured: dict = {}

    def fake_post(url, *, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse(audio, status_code=200)

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")

    # Bypass forced_align (heavy whisper download) — the fallback path is
    # what we want exercised here.
    from parallax import forced_align
    monkeypatch.setattr(forced_align, "align_words", lambda p: (_ for _ in ()).throw(RuntimeError("stubbed")))

    path, words, dur = gemini_tts.synthesize(
        "two-second test", voice="Kore", out_dir=tmp_path,
    )
    assert path.exists() and path.suffix == ".mp3"
    assert path.read_bytes() == audio
    assert captured["url"] == "https://openrouter.ai/api/v1/audio/speech"
    assert captured["json"]["model"] == "google/gemini-2.5-flash-preview-tts"
    assert captured["json"]["voice"] == "Kore"
    assert captured["json"]["response_format"] == "mp3"
    assert captured["headers"]["Authorization"] == "Bearer fake-key"
    assert dur > 0.0
    assert len(words) == 2  # split() → ["two-second", "test"]


def test_rapid_fire_preset_is_default_on_generate_tts(monkeypatch, tmp_path):
    """Calling `generate_tts(alias='gemini-flash-tts')` with no style applies
    the rapid_fire preset by default — verified faster TTS for ads."""
    audio = _make_real_mp3_bytes(1.0)
    captured: dict = {}

    def fake_post(url, *, headers, json, timeout):
        captured["spoken"] = json["input"]
        return _FakeResponse(audio, status_code=200)

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    monkeypatch.delenv("PARALLAX_TEST_MODE", raising=False)

    from parallax import forced_align
    monkeypatch.setattr(forced_align, "align_words", lambda p: (_ for _ in ()).throw(RuntimeError("stubbed")))

    from parallax import openrouter, runlog
    runlog.start_run("style-default")
    try:
        openrouter.generate_tts("Buy now. Limited time.", alias="gemini-flash-tts", out_dir=tmp_path)
    finally:
        runlog.end_run()

    assert captured["spoken"].startswith("Read this as a rapid-fire commercial"), (
        f"expected rapid_fire preset prefix, got: {captured['spoken'][:80]!r}"
    )
    assert "Buy now. Limited time." in captured["spoken"]


def test_explicit_style_natural_skips_directive(monkeypatch, tmp_path):
    """Passing `style='natural'` produces a bare prompt (no rapid_fire prefix)."""
    audio = _make_real_mp3_bytes(1.0)
    captured: dict = {}

    def fake_post(url, *, headers, json, timeout):
        captured["spoken"] = json["input"]
        return _FakeResponse(audio, status_code=200)

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    monkeypatch.delenv("PARALLAX_TEST_MODE", raising=False)

    from parallax import forced_align
    monkeypatch.setattr(forced_align, "align_words", lambda p: (_ for _ in ()).throw(RuntimeError("stubbed")))

    gemini_tts.synthesize("Once upon a time.", out_dir=tmp_path, style="natural")
    assert captured["spoken"] == "Once upon a time."  # no prefix


def test_freeform_style_hint_overrides_preset(monkeypatch, tmp_path):
    audio = _make_real_mp3_bytes(1.0)
    captured: dict = {}

    def fake_post(url, *, headers, json, timeout):
        captured["spoken"] = json["input"]
        return _FakeResponse(audio, status_code=200)

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")

    from parallax import forced_align
    monkeypatch.setattr(forced_align, "align_words", lambda p: (_ for _ in ()).throw(RuntimeError("stubbed")))

    gemini_tts.synthesize(
        "Hello.", out_dir=tmp_path,
        style="rapid_fire",
        style_hint="Whisper this conspiratorially:",  # should win over preset
    )
    assert captured["spoken"].startswith("Whisper this conspiratorially: Hello.")


def test_unknown_style_raises_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    with pytest.raises(ValueError, match="Unknown TTS style"):
        gemini_tts.synthesize("hi", out_dir=tmp_path, style="not-a-real-preset")


def test_synthesize_raises_when_response_is_error(monkeypatch, tmp_path):
    def fake_post(url, *, headers, json, timeout):
        return _FakeResponse(b"", status_code=500, text="upstream busted")

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")

    with pytest.raises(RuntimeError, match="OpenRouter TTS request failed"):
        gemini_tts.synthesize("x", out_dir=tmp_path)


def test_synthesize_raises_on_empty_body(monkeypatch, tmp_path):
    def fake_post(url, *, headers, json, timeout):
        return _FakeResponse(b"", status_code=200)

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")

    with pytest.raises(RuntimeError, match="empty body"):
        gemini_tts.synthesize("x", out_dir=tmp_path)
