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
