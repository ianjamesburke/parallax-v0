"""Tests for src/parallax/preflight.py"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from parallax.preflight import compute_preflight, format_preflight


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
    assert f"${cost:.4f}" in text
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


def test_format_preflight_shows_overwrite_warning(tmp_path):
    assets = tmp_path / "parallax" / "assets"
    assets.mkdir(parents=True)
    (assets / "scene_00_still.png").touch()

    plan = _make_plan(scenes=[{"index": 0}])
    result = compute_preflight(plan, folder=tmp_path)
    assert "overwrite" in format_preflight(result).lower()


# ---------------------------------------------------------------------------
# 11. Locked-audio silent no-op warnings
# ---------------------------------------------------------------------------

def test_voice_speed_warning_when_audio_locked():
    plan = _make_plan(
        scenes=[{"index": 0}],
        audio_path="audio.mp3",
        voice_speed=1.5,
    )
    result = compute_preflight(plan)
    assert any("voice_speed" in w for w in result.warnings)
    assert any("locked" in w for w in result.warnings)


def test_no_voice_speed_warning_when_speed_is_default():
    plan = _make_plan(
        scenes=[{"index": 0}],
        audio_path="audio.mp3",
        voice_speed=1.0,
    )
    result = compute_preflight(plan)
    assert not any("voice_speed" in w for w in result.warnings)


def test_trim_pauses_warning_when_audio_locked():
    plan = _make_plan(
        scenes=[{"index": 0}],
        audio_path="audio.mp3",
        trim_pauses=True,
    )
    result = compute_preflight(plan)
    assert any("trim_pauses" in w for w in result.warnings)
    assert any("cap-pauses" in w for w in result.warnings)


def test_trim_pauses_float_warning_when_audio_locked():
    plan = _make_plan(
        scenes=[{"index": 0}],
        audio_path="audio.mp3",
        trim_pauses=0.3,
    )
    result = compute_preflight(plan)
    assert any("trim_pauses" in w for w in result.warnings)


def test_no_trim_pauses_warning_when_disabled():
    plan = _make_plan(
        scenes=[{"index": 0}],
        audio_path="audio.mp3",
        trim_pauses=False,
    )
    result = compute_preflight(plan)
    assert not any("trim_pauses" in w for w in result.warnings)


def test_no_locked_audio_warnings_when_no_audio_path():
    plan = _make_plan(
        scenes=[{"index": 0}],
        voice_speed=1.5,
        trim_pauses=True,
    )
    result = compute_preflight(plan)
    assert result.warnings == []


def test_vo_text_hash_mismatch_warning():
    import hashlib
    original_text = "Hello world"
    original_hash = hashlib.sha256(original_text.encode()).hexdigest()[:16]

    plan = _make_plan(
        scenes=[{"index": 0, "vo_text": "Changed text"}],
        audio_path="audio.mp3",
        vo_text_hashes={"0": original_hash},
    )
    result = compute_preflight(plan)
    assert any("scene 0" in w and "vo_text" in w for w in result.warnings)
    assert any("Clear audio_path" in w for w in result.warnings)


def test_no_vo_text_warning_when_text_unchanged():
    import hashlib
    text = "Hello world"
    text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

    plan = _make_plan(
        scenes=[{"index": 0, "vo_text": text}],
        audio_path="audio.mp3",
        vo_text_hashes={"0": text_hash},
    )
    result = compute_preflight(plan)
    assert not any("vo_text" in w for w in result.warnings)


def test_no_vo_text_warning_when_hashes_absent():
    plan = _make_plan(
        scenes=[{"index": 0, "vo_text": "Some text"}],
        audio_path="audio.mp3",
    )
    result = compute_preflight(plan)
    assert not any("vo_text" in w for w in result.warnings)


def test_warnings_appear_in_format_preflight():
    plan = _make_plan(
        scenes=[{"index": 0}],
        audio_path="audio.mp3",
        voice_speed=1.5,
    )
    result = compute_preflight(plan)
    text = format_preflight(result)
    assert "voice_speed" in text
    assert "WARNING" in text


def test_locked_audio_warnings_in_table():
    plan = _make_plan(
        scenes=[{"index": 0}],
        audio_path="audio.mp3",
        voice_speed=1.5,
    )
    result = compute_preflight(plan)
    assert "voice_speed" in format_preflight(result)


# ---------------------------------------------------------------------------
# Billed-duration and voiceover estimates (cost accuracy)
# ---------------------------------------------------------------------------

def test_clip_estimate_uses_billed_duration_not_scene_length():
    """A 3 s scene on seedance (min 4 s) is billed for 4 s; the estimate must say so."""
    from parallax.models import resolve
    plan = _make_plan([{"index": 0, "animate": True, "duration_s": 3.0, "still_path": "s.png"}], video_model="seedance")
    clip = [s for s in compute_preflight(plan).scenes if s.kind == "clip"][0]
    assert clip.duration_s == 4.0
    assert clip.cost_usd == pytest.approx(resolve("seedance", kind="video").cost_usd * 4)


def test_clip_estimate_defaults_to_default_clip_duration():
    from parallax.models import DEFAULT_CLIP_DURATION_S
    plan = _make_plan([{"index": 0, "animate": True, "still_path": "s.png"}], video_model="seedance")
    clip = [s for s in compute_preflight(plan).scenes if s.kind == "clip"][0]
    assert clip.duration_s == DEFAULT_CLIP_DURATION_S


def test_end_frame_clip_estimate_uses_catalog_measured_multiplier():
    """2026-10-19 evidence: 5s Seedance start+end billed $0.45738."""
    plan = _make_plan([{"index": 0, "animate": True, "duration_s": 5,
                        "still_path": "start.png", "end_frame_path": "end.png"}], video_model="draft")
    clip = [s for s in compute_preflight(plan).scenes if s.kind == "clip"][0]
    assert clip.cost_usd == pytest.approx(0.45738, rel=0.01)


def test_voiceover_cost_scales_with_characters():
    from parallax.models import resolve
    text = "x" * 2000
    plan = _make_plan([{"index": 0, "still_path": "s.png", "vo_text": text}], voice_model="tts-gemini")
    result = compute_preflight(plan)
    assert result.voiceover_cost_usd == pytest.approx(resolve("tts-gemini", kind="tts").estimate_tts_usd(2000))
    assert result.voiceover_cost_usd > 0
    assert result.estimated_total_usd == pytest.approx(result.voiceover_cost_usd)


def test_voiceover_cost_unknown_model_warns_instead_of_guessing():
    plan = _make_plan([{"index": 0, "still_path": "s.png", "vo_text": "hello"}], voice_model="elevenlabs")
    result = compute_preflight(plan)
    assert result.voiceover_cost_usd == 0.0
    assert any("elevenlabs" in w for w in result.warnings)


def test_tts_mini_estimate_matches_billed_short_and_long_scripts():
    """Billed 2026-10-14: 72 chars -> $0.000451..$0.000467; 180 chars -> $0.000674."""
    from parallax.models import resolve
    spec = resolve("tts-mini", kind="tts")
    assert spec.estimate_tts_usd(72) == pytest.approx(0.000459, rel=0.10)
    assert spec.estimate_tts_usd(180) == pytest.approx(0.000674, rel=0.10)


def test_no_trim_pauses_warning_when_stills_only_without_audio_path():
    plan = _make_plan(
        scenes=[{"index": 0}],
        stills_only=True,
        trim_pauses=True,
    )
    result = compute_preflight(plan)
    assert result.voiceover_locked is True
    assert not any("trim_pauses" in w for w in result.warnings)


def test_still_with_reference_images_includes_vision_estimate():
    from parallax.models import resolve
    plan = _make_plan(
        scenes=[
            {"index": 0},
            {"index": 1, "reference_images": ["ref.png"]},
        ],
        stills_only=True,
    )
    result = compute_preflight(plan)
    base = resolve("mid", kind="image").cost_usd
    assert result.scenes[0].cost_usd == pytest.approx(base)
    assert result.scenes[1].cost_usd == pytest.approx(base + 0.01)
