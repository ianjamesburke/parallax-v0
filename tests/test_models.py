from __future__ import annotations

import pytest

from parallax.models import (
    ALIASES,
    IMAGE_MODELS,
    TTS_MODELS,
    VIDEO_MODELS,
    alias_guidance,
    resolve,
    resolve_chain,
)


def test_three_kinds_populated():
    # Tier aliases per modality.
    assert {"draft", "mid", "premium"} <= set(IMAGE_MODELS)
    assert {"draft", "mid", "premium"} <= set(VIDEO_MODELS)
    # Named aliases for power users.
    assert {"nano-banana", "seedream"} <= set(IMAGE_MODELS)
    assert {"kling", "veo", "seedance", "wan", "sora"} <= set(VIDEO_MODELS)
    # Single TTS provider via OpenRouter (Gemini Flash Preview).
    assert {"gemini-flash-tts"} <= set(TTS_MODELS)


def test_resolve_returns_spec_with_kind():
    # Tier aliases shared across image and video — the kind= disambiguator picks.
    assert resolve("mid", kind="image").kind == "image"
    assert resolve("mid", kind="video").kind == "video"
    assert resolve("kling").kind == "video"
    assert resolve("gemini-flash-tts").kind == "tts"


def test_resolve_kind_mismatch_raises():
    with pytest.raises(ValueError, match="video"):
        resolve("kling", kind="image")


def test_resolve_unknown_raises():
    with pytest.raises(ValueError, match="Unknown"):
        resolve("flux-pro")


def test_alias_guidance_mentions_every_tier():
    text = alias_guidance()
    for tier in ("draft", "mid", "premium"):
        assert tier in text


def test_resolve_chain_follows_fallbacks_without_cycle():
    chain = resolve_chain("nano-banana")
    assert chain[0].alias == "nano-banana"
    aliases_in_chain = [s.alias for s in chain]
    assert len(aliases_in_chain) == len(set(aliases_in_chain))


def test_image_capabilities_populated():
    spec = resolve("mid", kind="image")
    assert "9:16" in spec.aspect_ratios
    assert spec.max_refs >= 1
    assert "style_ref" in spec.inputs


def test_video_capabilities_populated():
    spec = resolve("mid", kind="video")
    assert "9:16" in spec.aspect_ratios
    assert spec.start_frame is True


def test_tts_voices_populated():
    spec = resolve("gemini-flash-tts", kind="tts")
    assert len(spec.voices) >= 10
    assert "Kore" in spec.voices
