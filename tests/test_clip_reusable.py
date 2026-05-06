"""Tests for _is_clip_reusable — the file-existence guard on clip reuse."""
from __future__ import annotations

from pathlib import Path

from parallax.stages import _is_clip_reusable
from parallax.settings import ProductionMode


def test_none_clip_not_reusable():
    assert _is_clip_reusable(None, ProductionMode.TEST) is False
    assert _is_clip_reusable(None, ProductionMode.REAL) is False


def test_clip_path_missing_file_not_reusable(tmp_path):
    """A clip_path pointing to a non-existent file must not be reusable."""
    missing = str(tmp_path / "scene_00_animated.mp4")
    assert _is_clip_reusable(missing, ProductionMode.TEST) is False
    assert _is_clip_reusable(missing, ProductionMode.REAL) is False


def test_clip_path_existing_file_reusable(tmp_path):
    """A clip_path pointing to an existing file is reusable in DRY mode."""
    clip = tmp_path / "scene_00_animated.mp4"
    clip.write_bytes(b"fake")
    assert _is_clip_reusable(str(clip), ProductionMode.TEST) is True


def test_mock_asset_not_reusable_in_real_mode(tmp_path):
    """A mock/dry-run placeholder is not reusable in REAL mode even if it exists."""
    # is_mock_asset checks for mock_ prefix — see shim.py
    mock_clip = tmp_path / "mock_video_scene_00.mp4"
    mock_clip.write_bytes(b"fake")
    assert _is_clip_reusable(str(mock_clip), ProductionMode.REAL) is False


def test_mock_asset_reusable_in_dry_mode(tmp_path):
    """A mock asset IS reusable in DRY mode (that's the whole point of dry runs)."""
    mock_clip = tmp_path / "mock_video_scene_00.mp4"
    mock_clip.write_bytes(b"fake")
    assert _is_clip_reusable(str(mock_clip), ProductionMode.TEST) is True
