"""Mock-reuse guard: live runs must not reuse dry-run mock stubs.

Locks in:
  - stage_stills in REAL mode regenerates when still_path points to a mock file.
  - stage_stills in TEST mode still reuses mock still_path (hash-cache intact).
  - stage_stills in REAL mode reuses a real (non-mock) still_path.
  - stage_animate in REAL mode re-animates when clip_path points to a mock file.
  - stage_animate in TEST mode still skips mock clip_path (hash-cache intact).
  - stage_animate in REAL mode skips a real (non-mock) clip_path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from parallax.settings import ProductionMode, Settings
from parallax.stages import PipelineState, SceneRuntime, stage_animate, stage_stills


def _make_png(path: Path, w: int = 1080, h: int = 1920) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=blue:s={w}x{h}",
         "-frames:v", "1", str(path)],
        check=True, capture_output=True,
    )


def _make_mp4(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=blue:s=1080x1920:d=1",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         str(path)],
        check=True, capture_output=True,
    )


def _make_settings(tmp_path: Path, mode: ProductionMode) -> Settings:
    folder = tmp_path / "project"
    folder.mkdir(exist_ok=True)
    plan_path = folder / "plan.yaml"
    plan_path.write_text("scenes: []\n")
    return Settings(
        folder=folder,
        plan_path=plan_path,
        concept_prefix="",
        image_model="flux-schnell",
        video_model="mid",
        aspect="9:16",
        resolution="1080x1920",
        animate_resolution="480x854",
        video_width=1080,
        video_height=1920,
        res_scale=1.0,
        voice="alloy",
        voice_model="tts-mini",
        voice_speed=1.0,
        style=None,
        style_hint=None,
        caption_style="default",
        fontsize=48,
        words_per_chunk=3,
        caption_animation_override=None,
        caption_shift_s=0.0,
        skip_captions=False,
        headline=None,
        headline_fontsize=None,
        headline_bg=None,
        headline_color=None,
        character_image=None,
        product_image=None,
        avatar_cfg=None,
        stills_only=False,
        mode=mode,
        events=lambda *a, **kw: None,
    )


def _make_state(tmp_path: Path) -> PipelineState:
    out = tmp_path / "output"
    out.mkdir(exist_ok=True)
    (out / "stills").mkdir(exist_ok=True)
    (out / "video").mkdir(exist_ok=True)
    (out / "audio").mkdir(exist_ok=True)
    return PipelineState(
        out_dir=str(out),
        stills_dir=str(out / "stills"),
        video_dir=str(out / "video"),
        audio_dir=str(out / "audio"),
        version=1,
        short_id="abc123",
        convention_name="project-v1-abc123.mp4",
    )


def _make_plan(scenes: list[dict[str, Any]]) -> dict[str, Any]:
    return {"scenes": scenes, "video_model": "mid", "image_model": "flux-schnell"}


# ─── stage_stills guard ───────────────────────────────────────────────────────

def test_stage_stills_real_mode_rejects_mock_still(tmp_path):
    """In REAL mode, a locked mock still triggers regeneration, not reuse."""
    settings = _make_settings(tmp_path, ProductionMode.REAL)
    state = _make_state(tmp_path)
    mock_png = Path(state.stills_dir) / "mock_abc12345.png"
    _make_png(mock_png)
    real_png = Path(state.stills_dir) / "real_generated.png"
    _make_png(real_png)

    plan = _make_plan([
        {"index": 0, "vo_text": "hello", "prompt": "a scene", "still_path": str(mock_png)},
    ])

    gen_called = []

    def fake_generate_image(**kwargs):
        gen_called.append(kwargs)
        return str(real_png)

    with (
        patch("parallax.openrouter.generate_image", side_effect=fake_generate_image),
        patch("parallax.stills.check_aspect", return_value=MagicMock(within_tolerance=True)),
        patch("parallax.stills.normalize_aspect", side_effect=lambda p, r: p),
        patch("parallax.stages._lock_field_in_plan"),
    ):
        stage_stills(plan, settings, state)

    assert len(gen_called) == 1, "expected generate_image to be called (regenerated, not reused)"


def test_stage_stills_test_mode_reuses_mock_still(tmp_path):
    """In TEST mode, a locked mock still is reused (hash cache intact)."""
    settings = _make_settings(tmp_path, ProductionMode.TEST)
    state = _make_state(tmp_path)
    mock_png = Path(state.stills_dir) / "mock_abc12345.png"
    _make_png(mock_png)

    plan = _make_plan([
        {"index": 0, "vo_text": "hello", "prompt": "a scene", "still_path": str(mock_png)},
    ])

    with patch("parallax.openrouter.generate_image") as mock_gen:
        stage_stills(plan, settings, state)
        mock_gen.assert_not_called()


def test_stage_stills_real_mode_reuses_real_still(tmp_path):
    """In REAL mode, a real (non-mock) still_path is reused without regenerating."""
    settings = _make_settings(tmp_path, ProductionMode.REAL)
    state = _make_state(tmp_path)
    real_png = Path(state.stills_dir) / "scene_00_photo.png"
    _make_png(real_png)

    plan = _make_plan([
        {"index": 0, "vo_text": "hello", "prompt": "a scene", "still_path": str(real_png)},
    ])

    with patch("parallax.openrouter.generate_image") as mock_gen:
        stage_stills(plan, settings, state)
        mock_gen.assert_not_called()


# ─── stage_animate guard ──────────────────────────────────────────────────────

def _scene_runtime(still_path: Path, clip_path: Path) -> SceneRuntime:
    return SceneRuntime(
        index=0,
        shot_type="broll",
        vo_text="hello",
        prompt="a scene",
        still_path=str(still_path),
        aspect="9:16",
        animate=True,
        motion_prompt="drift",
        clip_path=str(clip_path),
    )


def test_stage_animate_real_mode_rejects_mock_clip(tmp_path):
    """In REAL mode, a locked mock clip triggers re-animation."""
    settings = _make_settings(tmp_path, ProductionMode.REAL)
    state = _make_state(tmp_path)
    still = Path(state.stills_dir) / "scene_00.png"
    _make_png(still)
    mock_clip = Path(state.video_dir) / "mock_video_wan-i2v_abc123.mp4"
    _make_mp4(mock_clip)
    real_clip = Path(state.video_dir) / "real_clip.mp4"
    _make_mp4(real_clip)

    state.scenes = [_scene_runtime(still, mock_clip)]
    plan = _make_plan([])

    with (
        patch("parallax.openrouter.generate_video", return_value=real_clip) as mock_gen,
        patch("parallax.stages._lock_field_in_plan"),
    ):
        stage_animate(plan, settings, state)
        mock_gen.assert_called_once()


def test_stage_animate_test_mode_reuses_mock_clip(tmp_path):
    """In TEST mode, a locked mock clip is skipped (hash cache intact)."""
    settings = _make_settings(tmp_path, ProductionMode.TEST)
    state = _make_state(tmp_path)
    still = Path(state.stills_dir) / "scene_00.png"
    _make_png(still)
    mock_clip = Path(state.video_dir) / "mock_video_wan-i2v_abc123.mp4"
    _make_mp4(mock_clip)

    state.scenes = [_scene_runtime(still, mock_clip)]
    plan = _make_plan([])

    with patch("parallax.openrouter.generate_video") as mock_gen:
        stage_animate(plan, settings, state)
        mock_gen.assert_not_called()


def test_stage_animate_real_mode_reuses_real_clip(tmp_path):
    """In REAL mode, a real (non-mock) clip_path is not re-animated."""
    settings = _make_settings(tmp_path, ProductionMode.REAL)
    state = _make_state(tmp_path)
    still = Path(state.stills_dir) / "scene_00.png"
    _make_png(still)
    real_clip = Path(state.video_dir) / "scene_00_animated.mp4"
    _make_mp4(real_clip)

    state.scenes = [_scene_runtime(still, real_clip)]
    plan = _make_plan([])

    with patch("parallax.openrouter.generate_video") as mock_gen:
        stage_animate(plan, settings, state)
        mock_gen.assert_not_called()
