"""Tests for `_resolve_still_refs` — additive ref merging in stage_stills."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from parallax.stages import _resolve_still_refs


def _settings(tmp_path: Path, *, character_image: str | None = None, stills_only: bool = False, product_image: str | None = None):
    return SimpleNamespace(folder=tmp_path, character_image=character_image, stills_only=stills_only, product_image=product_image)


def test_no_refs_no_media_returns_none(tmp_path):
    s = {"index": 0}
    assert _resolve_still_refs(s, _settings(tmp_path)) is None


def test_reference_images_only_resolved_relative_to_folder(tmp_path):
    s = {"index": 0, "reference_images": ["brand/char.png"]}
    result = _resolve_still_refs(s, _settings(tmp_path))
    assert result == [str(tmp_path / "brand/char.png")]


def test_character_image_only_when_reference_flag_set(tmp_path):
    char = str(tmp_path / "char.png")
    s = {"index": 0, "reference": True}
    with patch("parallax.stages._extract_character_image_frame", return_value=char):
        result = _resolve_still_refs(s, _settings(tmp_path, character_image=char))
    assert result == [char]


def test_reference_images_and_character_image_merged_additively(tmp_path):
    """Core of issue #82: reference_images + character_image must merge, not compete."""
    product_ref = "brand/product.png"
    char = str(tmp_path / "char.png")
    s = {"index": 0, "reference_images": [product_ref], "reference": True}
    with patch("parallax.stages._extract_character_image_frame", return_value=char):
        result = _resolve_still_refs(s, _settings(tmp_path, character_image=char))
    assert result == [str(tmp_path / product_ref), char]


def test_reference_images_and_stills_only_merged_additively(tmp_path):
    product_ref = "brand/product.png"
    char = str(tmp_path / "char.png")
    s = {"index": 0, "reference_images": [product_ref]}
    with patch("parallax.stages._extract_character_image_frame", return_value=char):
        result = _resolve_still_refs(s, _settings(tmp_path, character_image=char, stills_only=True))
    assert result == [str(tmp_path / product_ref), char]


def test_multiple_image_refs_all_included(tmp_path):
    char = str(tmp_path / "char.png")
    product = str(tmp_path / "product.png")
    s = {"index": 0, "reference_images": ["char.png", "product.png"], "reference": True}
    char_frame = str(tmp_path / "char_frame.png")
    with patch("parallax.stages._extract_character_image_frame", return_value=char_frame):
        result = _resolve_still_refs(
            s, _settings(tmp_path, character_image=char)
        )
    assert result == [str(tmp_path / "char.png"), str(tmp_path / "product.png"), char_frame]


def test_media_fallback_used_when_no_explicit_refs(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "image1.png").write_bytes(b"")
    (media / "image2.png").write_bytes(b"")
    s = {"index": 0}
    result = _resolve_still_refs(s, _settings(tmp_path))
    assert result is not None
    assert len(result) == 2
    assert all("media" in r for r in result)


def test_reference_images_resolve_to_folder_not_plan_dir(tmp_path):
    """Issue #64: relative reference_images must resolve from --folder, not plan file dir.

    Scenario: plan lives on a drive mount, --folder points to a local checkout.
    The reference file is in --folder; plan dir is irrelevant.
    """
    folder = tmp_path / "github" / "PX0001"
    folder.mkdir(parents=True)
    plan_dir = tmp_path / "drive" / "PX0001" / "parallax" / "scratch"
    plan_dir.mkdir(parents=True)

    s = {"index": 0, "reference_images": ["bottle.png"]}
    settings = SimpleNamespace(folder=folder, character_image=None, stills_only=False, product_image=None)
    result = _resolve_still_refs(s, settings)
    # Must resolve against folder, not plan_dir
    assert result is not None
    assert result == [str(folder / "bottle.png")]
    assert str(plan_dir) not in result[0]


def test_media_fallback_skips_derivatives(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "image1.png").write_bytes(b"")
    (media / "image1_a1080x1920.png").write_bytes(b"")
    (media / "image1_n480x854.png").write_bytes(b"")
    s = {"index": 0}
    result = _resolve_still_refs(s, _settings(tmp_path))
    assert result == [str(media / "image1.png")]
