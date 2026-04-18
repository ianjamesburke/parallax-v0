from __future__ import annotations

import pytest

from parallax.pricing import ALIASES, MODELS, alias_guidance, resolve


def test_five_aliases_present():
    assert set(ALIASES) == {"draft", "mid", "premium", "nano-banana", "grok"}


def test_resolve_returns_spec():
    spec = resolve("mid")
    assert spec.alias == "mid"
    assert spec.fal_id == MODELS["mid"].fal_id
    assert spec.price_usd > 0


def test_resolve_unknown_raises():
    with pytest.raises(ValueError, match="Unknown model alias"):
        resolve("flux-pro")


def test_alias_guidance_mentions_every_alias():
    text = alias_guidance()
    for alias in ALIASES:
        assert alias in text
    assert "mid" in text
