"""Tests for regenerate: true scene flag in produce."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def test_regenerate_clears_locks_and_removes_flag(tmp_path):
    """_apply_regenerate_flags clears still_path/clip_path/end_frame_path and removes regenerate."""
    from parallax.produce import _apply_regenerate_flags

    plan_path = tmp_path / "plan.yaml"
    plan = {
        "scenes": [
            {
                "index": 0,
                "still_path": "parallax/assets/scene_00_still.png",
                "clip_path": "parallax/assets/scene_00_animated.mp4",
                "regenerate": True,
                "vo_text": "hello",
            },
            {
                "index": 1,
                "still_path": "parallax/assets/scene_01_still.png",
                "vo_text": "world",
            },
        ]
    }
    plan_path.write_text(yaml.safe_dump(plan))

    _apply_regenerate_flags(plan, plan_path)

    # In-memory: scene 0 locks cleared, regenerate removed
    s0 = plan["scenes"][0]
    assert "still_path" not in s0
    assert "clip_path" not in s0
    assert "regenerate" not in s0
    assert s0["vo_text"] == "hello"  # other fields preserved

    # Scene 1 untouched
    s1 = plan["scenes"][1]
    assert s1["still_path"] == "parallax/assets/scene_01_still.png"

    # On disk: same
    disk = yaml.safe_load(plan_path.read_text())
    assert "still_path" not in disk["scenes"][0]
    assert "clip_path" not in disk["scenes"][0]
    assert "regenerate" not in disk["scenes"][0]
    assert disk["scenes"][1]["still_path"] == "parallax/assets/scene_01_still.png"


def test_regenerate_no_op_when_absent(tmp_path):
    """_apply_regenerate_flags is a no-op when no scenes have regenerate: true."""
    from parallax.produce import _apply_regenerate_flags

    plan_path = tmp_path / "plan.yaml"
    original = {
        "scenes": [
            {"index": 0, "still_path": "some/path.png", "vo_text": "hello"},
        ]
    }
    plan_path.write_text(yaml.safe_dump(original))
    original_mtime = plan_path.stat().st_mtime

    plan = dict(original)
    plan["scenes"] = [dict(s) for s in original["scenes"]]
    _apply_regenerate_flags(plan, plan_path)

    # No disk write
    assert plan_path.stat().st_mtime == original_mtime
    assert plan["scenes"][0]["still_path"] == "some/path.png"


def test_archive_existing_asset_moves_to_versions_dir(tmp_path):
    """_archive_existing_asset moves real assets into parallax/assets/versions/<stem>_vN<suffix>."""
    from PIL import Image
    from parallax.stages import _archive_existing_asset

    assets_dir = tmp_path / "parallax" / "assets"
    assets_dir.mkdir(parents=True)
    canonical = assets_dir / "scene_03_still.png"

    # First real image
    Image.new("RGB", (64, 64), (10, 20, 30)).save(canonical, format="PNG")
    v1 = _archive_existing_asset(canonical, assets_dir)
    assert v1 == assets_dir / "versions" / "scene_03_still_v1.png"
    assert v1.is_file()
    assert not canonical.exists()

    # Second real image -> archives as v2
    Image.new("RGB", (64, 64), (40, 50, 60)).save(canonical, format="PNG")
    v2 = _archive_existing_asset(canonical, assets_dir)
    assert v2 == assets_dir / "versions" / "scene_03_still_v2.png"
    assert v2.is_file()
    assert v1.is_file()
    assert not canonical.exists()


def test_brief_scene_regenerate_propagates_and_clears(tmp_path):
    """BriefScene(regenerate=True) propagates into plan.yaml and _apply_regenerate_flags clears both plan and brief."""
    from parallax.brief import Brief
    from parallax.produce import _apply_regenerate_flags

    brief_path = tmp_path / "brief.yaml"
    still = tmp_path / "parallax" / "assets" / "scene_00_still.png"
    still.parent.mkdir(parents=True)
    still.write_bytes(b"png")

    brief_data = {
        "goal": "Test regenerate",
        "aspect": "9:16",
        "voice": "nova",
        "script": {
            "scenes": [
                {
                    "index": 0,
                    "shot_type": "broll",
                    "vo_text": "hello",
                    "prompt": "test prompt",
                    "still_path": "parallax/assets/scene_00_still.png",
                    "regenerate": True,
                }
            ]
        },
    }
    brief_path.write_text(yaml.safe_dump(brief_data, sort_keys=False))
    brief = Brief.from_yaml(brief_path)
    skeleton = brief.to_plan_skeleton()
    assert skeleton["scenes"][0].get("regenerate") is True

    plan_path = tmp_path / "parallax" / "scratch" / "plan.yaml"
    plan_path.parent.mkdir(parents=True)
    skeleton["source_brief"] = "brief.yaml"
    plan_path.write_text(yaml.safe_dump(skeleton, sort_keys=False))

    _apply_regenerate_flags(skeleton, plan_path)
    assert "still_path" not in skeleton["scenes"][0]
    assert "regenerate" not in skeleton["scenes"][0]

    reloaded_brief = yaml.safe_load(brief_path.read_text())
    assert "still_path" not in reloaded_brief["script"]["scenes"][0]
    assert "regenerate" not in reloaded_brief["script"]["scenes"][0]

