from __future__ import annotations

import pytest

from parallax.pricing import (
    ALIASES,
    IMAGE_MODELS,
    TTS_MODELS,
    VIDEO_MODELS,
    alias_guidance,
    resolve,
    resolve_chain,
)


def test_three_kinds_populated():
    assert {"draft", "mid", "premium", "nano-banana", "seedream"} <= set(IMAGE_MODELS)
    assert {"kling", "veo", "seedance", "wan", "sora"} <= set(VIDEO_MODELS)
    # gemini-flash-tts is the only TTS path; ElevenLabs is reached via
    # voice='eleven:<id>'. OpenRouter does not currently host an alignment-
    # emitting TTS model.
    assert {"gemini-flash-tts"} <= set(TTS_MODELS)


def test_resolve_returns_spec_with_kind():
    assert resolve("mid").kind == "image"
    assert resolve("kling").kind == "video"
    assert resolve("gemini-flash-tts").kind == "tts"


def test_resolve_kind_mismatch_raises():
    with pytest.raises(ValueError, match="image"):
        resolve("kling", kind="image")


def test_resolve_unknown_raises():
    with pytest.raises(ValueError, match="Unknown model alias"):
        resolve("flux-pro")


def test_alias_guidance_mentions_every_alias():
    text = alias_guidance()
    for alias in ALIASES:
        assert alias in text


def test_resolve_chain_follows_fallbacks_without_cycle():
    chain = resolve_chain("nano-banana")
    assert chain[0].alias == "nano-banana"
    # Chain terminates (no infinite loop on mutual fallbacks)
    aliases_in_chain = [s.alias for s in chain]
    assert len(aliases_in_chain) == len(set(aliases_in_chain))
