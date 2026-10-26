"""Regression: animation must request the same clip length the pre-flight estimates."""
from __future__ import annotations

from pathlib import Path

from parallax import openrouter
from parallax.models import DEFAULT_CLIP_DURATION_S
from parallax.stages import SceneRuntime, _animate_one_scene

from tests.test_test_mode_voiceover import _settings, _state


def _animate(tmp_path: Path, monkeypatch, **scene_kw) -> dict:
    still = tmp_path / "still.png"
    still.write_bytes(b"png")
    captured: dict = {}

    def fake_generate_video(**kw):
        captured.update(kw)
        clip = Path(kw["out_dir"]) / "raw.mp4"
        clip.write_bytes(b"mp4")
        return clip

    monkeypatch.setattr(openrouter, "generate_video", fake_generate_video)
    scene = SceneRuntime(
        index=0, shot_type="broll", vo_text="", prompt="p", still_path=str(still),
        aspect="9:16", animate=True, **scene_kw,
    )
    _animate_one_scene(scene, _settings(tmp_path), _state(tmp_path), "seedance")
    return captured


def test_pinned_scene_duration_is_requested(tmp_path, monkeypatch):
    assert _animate(tmp_path, monkeypatch, duration_s=7.0)["duration_s"] == 7.0


def test_unpinned_scene_uses_default_clip_duration(tmp_path, monkeypatch):
    assert _animate(tmp_path, monkeypatch)["duration_s"] == DEFAULT_CLIP_DURATION_S
