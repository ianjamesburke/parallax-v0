"""Provided assets (character_ref, product_ref) are copied to internal cache.

Verifies that settings resolution:
  - Copies the original to parallax/assets/cache/ and returns the cache path.
  - Never modifies, renames, or deletes the original file.
  - Is idempotent: re-running with the same original leaves the cache stable.
  - Handles missing originals with a clear error.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from parallax.settings import _cache_provided_asset, resolve_settings


def _make_png(path: Path, w: int, h: int) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=blue:s={w}x{h}",
         "-frames:v", "1", str(path)],
        check=True,
    )


def _write_plan(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.safe_dump(payload, sort_keys=False))
    return p


def _minimal_plan(char_path: str) -> dict:
    return {
        "aspect": "9:16",
        "image_model": "mid",
        "video_model": "mid",
        "character_image": char_path,
        "scenes": [{"index": 0, "shot_type": "broll", "vo_text": "hi", "prompt": "test"}],
    }


# ---------------------------------------------------------------------------
# _cache_provided_asset unit tests
# ---------------------------------------------------------------------------

def test_cache_creates_copy_in_cache_dir(tmp_path: Path) -> None:
    src = tmp_path / "ref.png"
    _make_png(src, 400, 711)
    cache_dir = tmp_path / "parallax" / "assets" / "cache"

    result = _cache_provided_asset(src, cache_dir)

    assert result == cache_dir / "ref.png"
    assert result.is_file()


def test_original_untouched_after_cache(tmp_path: Path) -> None:
    src = tmp_path / "ref.png"
    _make_png(src, 400, 711)
    cache_dir = tmp_path / "parallax" / "assets" / "cache"

    _cache_provided_asset(src, cache_dir)

    assert src.is_file(), "original must still exist after caching"


def test_cache_is_idempotent(tmp_path: Path) -> None:
    src = tmp_path / "ref.png"
    _make_png(src, 400, 711)
    cache_dir = tmp_path / "parallax" / "assets" / "cache"

    result1 = _cache_provided_asset(src, cache_dir)
    result2 = _cache_provided_asset(src, cache_dir)

    assert result1 == result2
    assert src.is_file()


def test_no_copy_when_already_in_cache_dir(tmp_path: Path) -> None:
    cache_dir = tmp_path / "parallax" / "assets" / "cache"
    cache_dir.mkdir(parents=True)
    src = cache_dir / "ref.png"
    _make_png(src, 400, 711)

    result = _cache_provided_asset(src, cache_dir)
    assert result == src


# ---------------------------------------------------------------------------
# Integration: settings resolution copies to cache
# ---------------------------------------------------------------------------

def test_settings_character_image_points_to_cache(tmp_path: Path) -> None:
    src = tmp_path / "strawberry_ref.png"
    _make_png(src, 720, 1280)
    plan_path = _write_plan(tmp_path, _minimal_plan(str(src)))

    settings = resolve_settings(yaml.safe_load(plan_path.read_text()), tmp_path, plan_path)

    cache_dir = tmp_path / "parallax" / "assets" / "cache"
    assert settings.character_image == str(cache_dir / "strawberry_ref.png")
    assert src.is_file(), "original must not be deleted"


def test_settings_character_image_original_survives_multiple_runs(tmp_path: Path) -> None:
    src = tmp_path / "strawberry_ref.png"
    _make_png(src, 720, 1280)
    plan_path = _write_plan(tmp_path, _minimal_plan(str(src)))
    plan_dict = yaml.safe_load(plan_path.read_text())

    settings1 = resolve_settings(plan_dict, tmp_path, plan_path)
    settings2 = resolve_settings(plan_dict, tmp_path, plan_path)

    assert settings1.character_image == settings2.character_image
    assert src.is_file()


def test_settings_missing_character_image_raises_clear_error(tmp_path: Path) -> None:
    plan_path = _write_plan(tmp_path, _minimal_plan(str(tmp_path / "nonexistent.png")))

    with pytest.raises(FileNotFoundError, match="character_image not found"):
        resolve_settings(yaml.safe_load(plan_path.read_text()), tmp_path, plan_path)


def test_crop_to_aspect_on_cache_does_not_touch_original(tmp_path: Path) -> None:
    """Full pipeline: settings caches the original, then crop_to_aspect is
    called on the cache copy. Original must survive."""
    from parallax.stills import crop_to_aspect

    src = tmp_path / "strawberry_ref.png"
    _make_png(src, 400, 400)  # square, needs cropping for 9:16

    plan_path = _write_plan(tmp_path, _minimal_plan(str(src)))
    settings = resolve_settings(yaml.safe_load(plan_path.read_text()), tmp_path, plan_path)

    cached = Path(settings.character_image)
    assert cached.is_file()

    cropped = crop_to_aspect(cached, "720x1280")
    cache_dir = tmp_path / "parallax" / "assets" / "cache"
    assert cropped.parent == cache_dir, "cropped variant must be inside cache dir"
    assert src.is_file(), "original must survive crop_to_aspect on cache copy"
