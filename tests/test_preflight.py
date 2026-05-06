"""Tests for src/parallax/preflight.py"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from parallax.preflight import compute_preflight, format_preflight, prompt_proceed


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_plan(scenes: list[dict], **kwargs) -> dict[str, Any]:
    base: dict[str, Any] = {
        "image_model": "mid",
        "video_model": "mid",
        "voice_model": "tts-mini",
    }
    base.update(kwargs)
    base["scenes"] = scenes
    return base


# ---------------------------------------------------------------------------
# 1. All scenes locked → total_usd = 0, all locked=True
# ---------------------------------------------------------------------------

def test_all_locked_zero_cost():
    plan = _make_plan(
        scenes=[
            {"index": 0, "still_path": "path/to/still0.png"},
            {"index": 1, "still_path": "path/to/still1.png", "animate": True, "clip_path": "path/to/clip1.mp4"},
        ],
        audio_path="path/to/audio.mp3",
    )
    result = compute_preflight(plan)
    assert result.estimated_total_usd == 0.0
    for scene in result.scenes:
        assert scene.locked is True
    assert result.voiceover_locked is True


# ---------------------------------------------------------------------------
# 2. No scenes locked, 1 still + 1 clip → correct cost sum
# ---------------------------------------------------------------------------

def test_unlocked_still_and_clip_cost():
    from parallax.models import resolve

    plan = _make_plan(
        scenes=[
            {"index": 0},                                     # still, unlocked
            {"index": 1, "animate": True, "duration_s": 3},  # clip, unlocked
        ]
    )
    result = compute_preflight(plan)

    still_cost = resolve("mid", kind="image").cost_usd          # 0.039
    clip_cost = resolve("mid", kind="video").cost_usd * 3       # 0.112 * 3

    expected = still_cost + still_cost + clip_cost  # still for scene 0, still+clip for scene 1
    assert abs(result.estimated_total_usd - expected) < 1e-6

    still_scenes = [s for s in result.scenes if s.kind == "still"]
    clip_scenes = [s for s in result.scenes if s.kind == "clip"]
    assert all(not s.locked for s in still_scenes)
    assert all(not s.locked for s in clip_scenes)
    assert len(clip_scenes) == 1
    assert clip_scenes[0].duration_s == 3.0


# ---------------------------------------------------------------------------
# 3. Per-scene model override → uses scene's model alias
# ---------------------------------------------------------------------------

def test_per_scene_model_override():
    from parallax.models import resolve

    plan = _make_plan(
        scenes=[
            {"index": 0, "image_model": "draft"},  # override image model
        ]
    )
    result = compute_preflight(plan)
    still_scenes = [s for s in result.scenes if s.kind == "still"]
    assert len(still_scenes) == 1
    scene = still_scenes[0]
    assert scene.model_alias == "draft"
    expected_cost = resolve("draft", kind="image").cost_usd
    assert abs(scene.cost_usd - expected_cost) < 1e-6


# ---------------------------------------------------------------------------
# 4. Voiceover locked when audio_path is set
# ---------------------------------------------------------------------------

def test_voiceover_locked_when_audio_path_set():
    plan = _make_plan(scenes=[{"index": 0}], audio_path="path/to/audio.mp3")
    result = compute_preflight(plan)
    assert result.voiceover_locked is True


def test_voiceover_unlocked_when_no_audio_path():
    plan = _make_plan(scenes=[{"index": 0}])
    result = compute_preflight(plan)
    assert result.voiceover_locked is False


# ---------------------------------------------------------------------------
# 5. format_preflight returns string containing expected model alias and cost
# ---------------------------------------------------------------------------

def test_format_preflight_contains_model_and_cost():
    from parallax.models import resolve

    plan = _make_plan(scenes=[{"index": 0}])
    result = compute_preflight(plan)
    text = format_preflight(result)

    # Should contain the model alias
    assert "mid" in text
    # Should contain the cost of a mid image
    cost = resolve("mid", kind="image").cost_usd
    assert f"${cost:.3f}" in text
    # Should contain the header marker
    assert "pre-flight cost estimate" in text


def test_format_preflight_shows_locked_status():
    plan = _make_plan(
        scenes=[{"index": 0, "still_path": "locked.png"}],
        audio_path="audio.mp3",
    )
    result = compute_preflight(plan)
    text = format_preflight(result)
    assert "locked" in text


def test_format_preflight_shows_balance_when_provided():
    plan = _make_plan(scenes=[{"index": 0}])
    result = compute_preflight(plan, balance_usd=12.20)
    text = format_preflight(result)
    assert "12.20" in text


# ---------------------------------------------------------------------------
# 6. prompt_proceed with yes=True returns True without prompting
# ---------------------------------------------------------------------------

def test_prompt_proceed_yes_flag_returns_true(capsys):
    plan = _make_plan(scenes=[{"index": 0}])
    result = compute_preflight(plan)
    ret = prompt_proceed(result, yes=True)
    assert ret is True
    captured = capsys.readouterr()
    assert "proceeding" in captured.out


# ---------------------------------------------------------------------------
# 7. prompt_proceed with stdout not a TTY returns True
# ---------------------------------------------------------------------------

def test_prompt_proceed_non_tty_returns_true():
    plan = _make_plan(scenes=[{"index": 0}])
    result = compute_preflight(plan)
    with patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = False
        # We need write/flush to work so the output is captured
        import io
        buf = io.StringIO()
        mock_stdout.write = buf.write
        mock_stdout.flush = buf.flush
        ret = prompt_proceed(result, yes=False)
    assert ret is True


# ---------------------------------------------------------------------------
# 8. compute_preflight passes balance_usd through to result
# ---------------------------------------------------------------------------

def test_balance_usd_passed_through():
    plan = _make_plan(scenes=[{"index": 0}])
    result = compute_preflight(plan, balance_usd=99.99)
    assert result.balance_usd == pytest.approx(99.99)


# ---------------------------------------------------------------------------
# 9. Clip uses default duration of 5.0 when duration_s not specified
# ---------------------------------------------------------------------------

def test_clip_default_duration():
    plan = _make_plan(scenes=[{"index": 0, "animate": True}])
    result = compute_preflight(plan)
    clip_scenes = [s for s in result.scenes if s.kind == "clip"]
    assert len(clip_scenes) == 1
    assert clip_scenes[0].duration_s == 5.0


# ---------------------------------------------------------------------------
# 10. Overwrite detection — folder param
# ---------------------------------------------------------------------------

def test_overwrite_detected_for_unlocked_still(tmp_path):
    assets = tmp_path / "parallax" / "assets"
    assets.mkdir(parents=True)
    (assets / "scene_00_still.png").touch()

    plan = _make_plan(scenes=[{"index": 0}])
    result = compute_preflight(plan, folder=tmp_path)

    still_scenes = [s for s in result.scenes if s.kind == "still" and s.index == 0]
    assert len(still_scenes) == 1
    assert still_scenes[0].will_overwrite is True
    assert result.has_overwrites is True


def test_overwrite_detected_for_unlocked_clip(tmp_path):
    assets = tmp_path / "parallax" / "assets"
    assets.mkdir(parents=True)
    (assets / "scene_01_still.png").touch()
    (assets / "scene_01_animated.mp4").touch()

    plan = _make_plan(scenes=[{"index": 1, "animate": True}])
    result = compute_preflight(plan, folder=tmp_path)

    clip_scenes = [s for s in result.scenes if s.kind == "clip" and s.index == 1]
    assert len(clip_scenes) == 1
    assert clip_scenes[0].will_overwrite is True
    assert result.has_overwrites is True


def test_no_overwrite_when_still_locked(tmp_path):
    assets = tmp_path / "parallax" / "assets"
    assets.mkdir(parents=True)
    (assets / "scene_00_still.png").touch()

    plan = _make_plan(scenes=[{"index": 0, "still_path": "parallax/assets/scene_00_still.png"}])
    result = compute_preflight(plan, folder=tmp_path)

    still_scenes = [s for s in result.scenes if s.kind == "still"]
    assert all(not s.will_overwrite for s in still_scenes)
    assert result.has_overwrites is False


def test_no_overwrite_when_file_does_not_exist(tmp_path):
    plan = _make_plan(scenes=[{"index": 0}])
    result = compute_preflight(plan, folder=tmp_path)
    assert result.has_overwrites is False


def test_format_preflight_shows_overwrite_warning(tmp_path):
    assets = tmp_path / "parallax" / "assets"
    assets.mkdir(parents=True)
    (assets / "scene_00_still.png").touch()

    plan = _make_plan(scenes=[{"index": 0}])
    result = compute_preflight(plan, folder=tmp_path)
    text = format_preflight(result)
    assert "overwrite" in text.lower()


def test_prompt_proceed_shows_overwrite_warning(tmp_path, capsys):
    assets = tmp_path / "parallax" / "assets"
    assets.mkdir(parents=True)
    (assets / "scene_00_still.png").touch()

    plan = _make_plan(scenes=[{"index": 0}])
    result = compute_preflight(plan, folder=tmp_path)
    prompt_proceed(result, yes=True)
    captured = capsys.readouterr()
    assert "overwrite" in captured.out.lower()
