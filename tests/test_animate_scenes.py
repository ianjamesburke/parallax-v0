"""animate_scenes characterization.

Locks in:
  - In PARALLAX_TEST_MODE the test-mode shim path produces a stub mp4
    per animate=true scene and writes clip_path back into the scene dict.
  - Scenes with animate=false are passed through unchanged.
  - Scenes with an existing clip_path that exists on disk are skipped
    (idempotent re-runs of the pipeline don't burn clips).
  - Scenes with a missing/non-existent clip_path are re-rendered.
"""

from __future__ import annotations

import json
from pathlib import Path

from parallax import project


def test_animate_skips_non_animate_scenes(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    scenes = [
        {"index": 0, "animate": False, "still_path": str(tmp_path / "fake.png")},
        {"index": 1, "animate": True, "still_path": str(tmp_path / "fake.png"),
         "duration_s": 1.0, "motion_prompt": "drift"},
    ]
    out = json.loads(project.animate_scenes(json.dumps(scenes), str(tmp_path)))
    assert "clip_path" not in out[0]
    assert "clip_path" in out[1]
    assert Path(out[1]["clip_path"]).exists()


def test_animate_skips_scenes_with_existing_clip(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    # Pre-create a fake clip
    existing = tmp_path / "scene_00_animated.mp4"
    existing.write_bytes(b"\x00")
    scenes = [
        {"index": 0, "animate": True, "still_path": str(tmp_path / "fake.png"),
         "duration_s": 1.0, "clip_path": str(existing)},
    ]
    out = json.loads(project.animate_scenes(json.dumps(scenes), str(tmp_path)))
    assert out[0]["clip_path"] == str(existing)
    # File unchanged (1 byte)
    assert existing.stat().st_size == 1


def test_animate_renders_when_clip_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    scenes = [
        {"index": 0, "animate": True, "still_path": str(tmp_path / "fake.png"),
         "duration_s": 1.0, "clip_path": str(tmp_path / "does_not_exist.mp4"),
         "motion_prompt": "drift"},
    ]
    out = json.loads(project.animate_scenes(json.dumps(scenes), str(tmp_path)))
    # New clip path was assigned (different from the missing path)
    assert Path(out[0]["clip_path"]).exists()
