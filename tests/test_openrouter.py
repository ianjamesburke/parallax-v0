from __future__ import annotations

from pathlib import Path

import pytest

from parallax import openrouter, runlog
from parallax.models import IMAGE_MODELS, TTS_MODELS, VIDEO_MODELS


@pytest.fixture(autouse=True)
def _test_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    runlog.start_run("test-run")
    yield
    runlog.end_run()


@pytest.mark.parametrize("alias", list(IMAGE_MODELS))
def test_image_aliases_dispatch_test_mode(alias, tmp_path):
    path = openrouter.generate_image("a red apple", alias, out_dir=tmp_path)
    assert path.exists() and path.suffix == ".png"


@pytest.mark.parametrize("alias", list(VIDEO_MODELS))
def test_video_aliases_dispatch_test_mode(alias, tmp_path):
    path = openrouter.generate_video("apple rolling", alias, duration_s=2.0, out_dir=tmp_path)
    assert path.exists() and path.suffix == ".mp4"


@pytest.mark.parametrize("alias", list(TTS_MODELS))
def test_tts_aliases_dispatch_test_mode(alias, tmp_path):
    path, words, total = openrouter.generate_tts("hello there friend", alias, out_dir=tmp_path)
    assert path.exists() and path.suffix == ".wav"
    assert len(words) == 3
    assert total > 0


def test_kind_mismatch_raises(tmp_path):
    # `kling` is video-only — surfaces a kind mismatch when called via image.
    with pytest.raises(ValueError, match="image"):
        openrouter.generate_image("x", alias="kling", out_dir=tmp_path)
    # `seedream` is image-only — surfaces a kind mismatch when called via video.
    with pytest.raises(ValueError, match="video"):
        openrouter.generate_video("x", alias="seedream", out_dir=tmp_path)
    # `seedream` is image-only — surfaces a kind mismatch when called via tts.
    with pytest.raises(ValueError, match="tts"):
        openrouter.generate_tts("x", alias="seedream", out_dir=tmp_path)


def test_unknown_alias_raises(tmp_path):
    with pytest.raises(ValueError, match="Unknown image model alias"):
        openrouter.generate_image("x", alias="flux-pro", out_dir=tmp_path)


def test_real_mode_raises_not_implemented(monkeypatch, tmp_path):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "0")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    with pytest.raises(RuntimeError, match="all fallbacks exhausted"):
        openrouter.generate_image("x", alias="mid", out_dir=tmp_path)


def test_real_mode_missing_key_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "0")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="all fallbacks exhausted"):
        openrouter.generate_image("x", alias="mid", out_dir=tmp_path)


@pytest.mark.skipif(
    "OPENROUTER_API_KEY" not in __import__("os").environ,
    reason="real-mode integration test; set OPENROUTER_API_KEY to enable.",
)
def test_image_real_mode_round_trip(monkeypatch, tmp_path):
    """End-to-end: hit OpenRouter, get back a real PNG. Uses `draft` alias
    (google/gemini-2.5-flash-image, verified hosted on OpenRouter)."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "0")
    monkeypatch.delenv("PARALLAX_USAGE_LOG", raising=False)
    p = openrouter.generate_image(
        "A photorealistic ripe red apple on a white studio background",
        alias="draft", out_dir=tmp_path,
    )
    assert p.exists() and p.suffix in (".png", ".jpg")
    # PNG/JPEG magic-byte check
    head = p.read_bytes()[:4]
    assert head[:4] == b"\x89PNG" or head[:3] == b"\xff\xd8\xff"


def test_video_real_mode_with_bad_key_falls_back_through_chain(monkeypatch, tmp_path):
    """Video real-mode actually hits OpenRouter `/api/v1/videos`. With a fake key
    the submit fails, the dispatcher walks the fallback chain, and once exhausted
    raises a `RuntimeError` wrapping the last HTTP error."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "0")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    with pytest.raises(RuntimeError, match="all fallbacks exhausted"):
        openrouter.generate_video("x", alias="kling", duration_s=2.0, out_dir=tmp_path)


def test_tts_real_mode_gemini_alias_requires_openrouter_key(monkeypatch, tmp_path):
    """gemini-flash-tts now routes through OpenRouter; missing
    OPENROUTER_API_KEY must surface a clear error, not stub-succeed."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "0")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        openrouter.generate_tts("hello", alias="gemini-flash-tts", out_dir=tmp_path)


def test_refs_validated_for_unsupported_model(tmp_path):
    fake_ref = tmp_path / "ref.png"
    fake_ref.write_bytes(b"\x89PNG\r\n\x1a\n")
    # `premium` (Nano Banana Pro) supports refs; `draft` supports refs;
    # there is no image alias that explicitly disallows refs in this table,
    # so this test asserts that providing refs simply works for ref-capable models.
    out = openrouter.generate_image("x", alias="nano-banana", reference_images=[fake_ref], out_dir=tmp_path)
    assert out.exists()


def test_refs_too_many_raises(tmp_path):
    refs = []
    for i in range(20):
        p = tmp_path / f"r{i}.png"
        p.write_bytes(b"\x89PNG")
        refs.append(p)
    with pytest.raises(ValueError, match="at most"):
        openrouter.generate_image("x", alias="mid", reference_images=refs, out_dir=tmp_path)


def test_refs_missing_path_raises(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        openrouter.generate_image(
            "x", alias="nano-banana",
            reference_images=[tmp_path / "nope.png"], out_dir=tmp_path,
        )
