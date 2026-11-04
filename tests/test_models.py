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
    assert {"nano-banana", "gemini-3-flash", "gemini-3-pro"} <= set(IMAGE_MODELS)
    assert {"kling", "veo", "seedance", "wan", "veo-lite"} <= set(VIDEO_MODELS)
    # Single TTS provider via OpenRouter (Gemini Flash Preview).
    assert {"tts-mini"} <= set(TTS_MODELS)


def test_resolve_returns_spec_with_kind():
    # Tier aliases shared across image and video — the kind= disambiguator picks.
    assert resolve("mid", kind="image").kind == "image"
    assert resolve("mid", kind="video").kind == "video"
    assert resolve("kling").kind == "video"
    assert resolve("tts-mini").kind == "tts"


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
    spec = resolve("tts-mini", kind="tts")
    assert len(spec.voices) >= 10
    assert "nova" in spec.voices


# ---------------------------------------------------------------------------
# Catalog policy
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("alias", sorted(IMAGE_MODELS))
def test_image_default_resolution_short_side_at_least_720(alias):
    """Default policy: smallest output with a short side of at least 720 px,
    recorded explicitly as `native_size` (measured 9:16 output)."""
    spec = IMAGE_MODELS[alias]
    assert spec.native_size, f"{alias}: image entries must record native_size"
    w, h = (int(x) for x in spec.native_size.split("x"))
    assert min(w, h) >= 720, f"{alias}: native_size {spec.native_size} is below 720 px"


@pytest.mark.parametrize("alias", sorted(VIDEO_MODELS))
def test_video_entries_declare_sorted_durations(alias):
    spec = VIDEO_MODELS[alias]
    assert spec.durations and list(spec.durations) == sorted(spec.durations)


def test_request_duration_rounds_up_to_supported_length():
    kling = VIDEO_MODELS["kling"]  # live durations: 5, 10
    assert kling.request_duration(3) == 5
    assert kling.request_duration(5) == 5
    assert kling.request_duration(5.2) == 10
    assert kling.request_duration(30) == 10  # longer than any supported: clamp to the longest


def test_veo_lite_does_not_pay_for_audio():
    assert VIDEO_MODELS["veo-lite"].request_params == {
        "generate_audio": False,
        "resolution": "720p",
    }
