"""character_image frame extraction for video files.

When character_image points to a video file, _extract_character_image_frame
must extract a still frame and return the PNG path. Image files pass through
unchanged. Extracted frames are cached so ffmpeg is not called twice.

Also covers stage_stills reference routing: reference: true on a character
scene must use character_image even when media/ has images.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from parallax.settings import ProductionMode, Settings
from parallax.stages import PipelineState, _extract_character_image_frame, _resolve_still_refs, stage_stills


# ---------------------------------------------------------------------------
# image passthrough
# ---------------------------------------------------------------------------

def test_image_passthrough(tmp_path):
    """PNG/JPG paths are returned as-is — no ffmpeg call."""
    img = tmp_path / "hero.png"
    img.write_bytes(b"\x89PNG")
    with patch("parallax.stages.run_ffmpeg") as mock_ff:
        result = _extract_character_image_frame(str(img), tmp_path)
    assert result == str(img)
    mock_ff.assert_not_called()


def test_jpg_passthrough(tmp_path):
    img = tmp_path / "hero.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    with patch("parallax.stages.run_ffmpeg") as mock_ff:
        result = _extract_character_image_frame(str(img), tmp_path)
    assert result == str(img)
    mock_ff.assert_not_called()


# ---------------------------------------------------------------------------
# video → frame extraction
# ---------------------------------------------------------------------------

def _fake_ffmpeg_writes(cmd, **_):
    """Side effect: writes the last path arg as a PNG."""
    out = Path(cmd[-1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"\x89PNG")
    return MagicMock(returncode=0, stderr="")


def test_mp4_extracts_frame(tmp_path):
    """MP4 character_image triggers ffmpeg frame extraction."""
    video = tmp_path / "hero.mp4"
    video.write_bytes(b"fake")
    cache_png = tmp_path / "__parallax_cache__" / "character_frame.png"

    with patch("parallax.stages.run_ffmpeg", side_effect=_fake_ffmpeg_writes) as mock_ff:
        result = _extract_character_image_frame(str(video), tmp_path)

    assert result == str(cache_png)
    assert cache_png.exists()
    assert mock_ff.call_count == 1
    cmd = mock_ff.call_args[0][0]
    assert str(video) in cmd
    assert str(cache_png) in cmd


def test_mov_extension_triggers_extract(tmp_path):
    video = tmp_path / "hero.mov"
    video.write_bytes(b"fake")

    with patch("parallax.stages.run_ffmpeg", side_effect=_fake_ffmpeg_writes):
        result = _extract_character_image_frame(str(video), tmp_path)

    assert result == str(tmp_path / "__parallax_cache__" / "character_frame.png")


# ---------------------------------------------------------------------------
# cache hit
# ---------------------------------------------------------------------------

def test_cache_hit_skips_ffmpeg(tmp_path):
    """If the cache PNG already exists, ffmpeg is not called again."""
    video = tmp_path / "hero.mp4"
    video.write_bytes(b"fake")
    cache_png = tmp_path / "__parallax_cache__" / "character_frame.png"
    cache_png.parent.mkdir(parents=True)
    cache_png.write_bytes(b"\x89PNG")

    with patch("parallax.stages.run_ffmpeg") as mock_ff:
        result = _extract_character_image_frame(str(video), tmp_path)

    assert result == str(cache_png)
    mock_ff.assert_not_called()


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------

def test_ffmpeg_failure_raises(tmp_path):
    """ffmpeg non-zero exit raises RuntimeError naming the video path."""
    video = tmp_path / "hero.mp4"
    video.write_bytes(b"fake")

    with patch("parallax.stages.run_ffmpeg", return_value=MagicMock(returncode=1, stderr="error detail")):
        with pytest.raises(RuntimeError, match="hero.mp4"):
            _extract_character_image_frame(str(video), tmp_path)


# ---------------------------------------------------------------------------
# stage_stills reference routing
# ---------------------------------------------------------------------------

def _make_settings_with_char(tmp_path: Path, char_image: str | None = None) -> Settings:
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
        character_image=char_image,
        product_image=None,
        avatar_cfg=None,
        stills_only=False,
        mode=ProductionMode.TEST,
        events=lambda *a, **kw: None,
    )


def _make_state(tmp_path: Path) -> PipelineState:
    out = tmp_path / "output"
    out.mkdir(exist_ok=True)
    (out / "stills").mkdir(exist_ok=True)
    return PipelineState(
        out_dir=str(out),
        stills_dir=str(out / "stills"),
        video_dir=str(out / "video"),
        audio_dir=str(out / "audio"),
        version=1,
        short_id="abc123",
        convention_name="project-v1-abc123.mp4",
    )


def _fake_generate_image(captured: list[dict], result_path: str):
    def _inner(**kwargs):
        captured.append(kwargs)
        return result_path
    return _inner


def test_reference_true_uses_character_image_even_when_media_dir_exists(tmp_path):
    """reference: true on a character scene must use character_image frame,
    not the media/ heuristic."""
    char_png = tmp_path / "project" / "hero.png"
    char_png.parent.mkdir(parents=True, exist_ok=True)
    char_png.write_bytes(b"\x89PNG")

    # media/ exists with a decoy image — must NOT be picked for reference: true scenes
    media_dir = tmp_path / "project" / "media"
    media_dir.mkdir()
    decoy = media_dir / "product.png"
    decoy.write_bytes(b"\x89PNG")

    settings = _make_settings_with_char(tmp_path, char_image=str(char_png))
    state = _make_state(tmp_path)
    result_png = str(Path(state.stills_dir) / "scene_00.png")
    Path(result_png).write_bytes(b"\x89PNG")

    captured: list[dict] = []
    plan: dict[str, Any] = {
        "scenes": [{"index": 0, "vo_text": "hero walks in", "prompt": "hero walks", "shot_type": "character", "reference": True}],
        "image_model": "flux-schnell",
    }

    with (
        patch("parallax.openrouter.generate_image", side_effect=_fake_generate_image(captured, result_png)),
        patch("parallax.stills.check_aspect", return_value=MagicMock(within_tolerance=True)),
        patch("parallax.stills.normalize_aspect", side_effect=lambda p, r: p),
        patch("parallax.stages._lock_field_in_plan"),
    ):
        stage_stills(plan, settings, state)

    assert len(captured) == 1
    refs = captured[0].get("reference_images")
    assert refs is not None, "reference_images must be set for reference: true scene"
    assert len(refs) == 1
    assert refs[0] == str(char_png), "reference must point to character_image, not media/ decoy"


def test_reference_true_without_character_image_falls_through_to_media(tmp_path):
    """reference: true with no character_image set falls through to media/ heuristic."""
    media_dir = tmp_path / "project" / "media"
    media_dir.mkdir(parents=True)
    media_img = media_dir / "product.png"
    media_img.write_bytes(b"\x89PNG")

    settings = _make_settings_with_char(tmp_path, char_image=None)
    state = _make_state(tmp_path)
    result_png = str(Path(state.stills_dir) / "scene_00.png")
    Path(result_png).write_bytes(b"\x89PNG")

    captured: list[dict] = []
    plan: dict[str, Any] = {
        "scenes": [{"index": 0, "vo_text": "hero walks in", "prompt": "hero walks", "shot_type": "character", "reference": True}],
        "image_model": "flux-schnell",
    }

    with (
        patch("parallax.openrouter.generate_image", side_effect=_fake_generate_image(captured, result_png)),
        patch("parallax.stills.check_aspect", return_value=MagicMock(within_tolerance=True)),
        patch("parallax.stills.normalize_aspect", side_effect=lambda p, r: p),
        patch("parallax.stages._lock_field_in_plan"),
    ):
        stage_stills(plan, settings, state)

    assert len(captured) == 1
    refs = captured[0].get("reference_images")
    assert refs is not None
    assert str(media_img) in refs


def test_broll_scene_without_reference_uses_media_heuristic(tmp_path):
    """Broll scenes (no reference flag) still pick up media/ images."""
    media_dir = tmp_path / "project" / "media"
    media_dir.mkdir(parents=True)
    media_img = media_dir / "product.png"
    media_img.write_bytes(b"\x89PNG")

    char_png = tmp_path / "project" / "hero.png"
    char_png.write_bytes(b"\x89PNG")
    settings = _make_settings_with_char(tmp_path, char_image=str(char_png))
    state = _make_state(tmp_path)
    result_png = str(Path(state.stills_dir) / "scene_00.png")
    Path(result_png).write_bytes(b"\x89PNG")

    captured: list[dict] = []
    plan: dict[str, Any] = {
        "scenes": [{"index": 0, "vo_text": "wide shot", "prompt": "city skyline", "shot_type": "broll"}],
        "image_model": "flux-schnell",
    }

    with (
        patch("parallax.openrouter.generate_image", side_effect=_fake_generate_image(captured, result_png)),
        patch("parallax.stills.check_aspect", return_value=MagicMock(within_tolerance=True)),
        patch("parallax.stills.normalize_aspect", side_effect=lambda p, r: p),
        patch("parallax.stages._lock_field_in_plan"),
    ):
        stage_stills(plan, settings, state)

    assert len(captured) == 1
    refs = captured[0].get("reference_images")
    assert refs is not None
    assert str(media_img) in refs
    assert str(media_img) in refs


# ---------------------------------------------------------------------------
# warning when no reference images found
# ---------------------------------------------------------------------------

def test_warn_when_no_media_dir_and_no_explicit_refs(tmp_path, capsys):
    """A warning is printed when refs resolve to None (no media/ dir, no explicit refs)."""
    settings = _make_settings_with_char(tmp_path)  # no character_image, no product_image
    scene = {"index": 3, "shot_type": "broll"}
    result = _resolve_still_refs(scene, settings)
    assert result is None
    out = capsys.readouterr().out
    assert "[WARNING]" in out
    assert "scene 3" in out
    assert "no reference images found" in out


def test_no_warn_when_media_dir_has_images(tmp_path, capsys):
    """No warning when media/ supplies references."""
    media_dir = tmp_path / "project" / "media"
    media_dir.mkdir(parents=True)
    (media_dir / "hero.png").write_bytes(b"\x89PNG")
    settings = _make_settings_with_char(tmp_path)
    scene = {"index": 0, "shot_type": "broll"}
    result = _resolve_still_refs(scene, settings)
    assert result is not None
    out = capsys.readouterr().out
    assert "[WARNING]" not in out
