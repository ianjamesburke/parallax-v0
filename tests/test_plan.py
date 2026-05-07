from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from parallax.plan import Plan
from parallax.settings import resolve_settings


def _write_plan(tmp_path, payload):
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.safe_dump(payload, sort_keys=False))
    return p


def _minimal(**overrides):
    base = {
        "aspect": "9:16",
        "voice": "nova",
        "voice_speed": 1.0,
        "image_model": "mid",
        "video_model": "mid",
        "scenes": [
            {
                "index": 0,
                "shot_type": "broll",
                "vo_text": "First line.",
                "prompt": "A sunlit kitchen.",
            },
        ],
    }
    base.update(overrides)
    return base


def test_minimal_plan_parses(tmp_path):
    p = _write_plan(tmp_path, _minimal())
    plan = Plan.from_yaml(p)
    assert plan.aspect == "9:16"
    assert plan.voice == "nova"
    assert plan.voice_speed == 1.0
    assert plan.image_model == "mid"
    assert plan.video_model == "mid"
    assert plan.voice_model == "tts-mini"  # default applied
    assert len(plan.scenes) == 1


def test_defaults_apply_when_optional_fields_omitted(tmp_path):
    p = _write_plan(tmp_path, {
        "scenes": [{"index": 0, "vo_text": "v", "prompt": "p"}],
    })
    plan = Plan.from_yaml(p)
    assert plan.aspect == "9:16"
    assert plan.voice == "nova"
    assert plan.voice_speed == 1.0
    assert plan.image_model == "mid"
    assert plan.video_model == "draft"
    assert plan.voice_model == "tts-mini"
    assert plan.scenes[0].shot_type == "broll"
    assert plan.scenes[0].animate is False


def test_unknown_top_level_field_rejected(tmp_path):
    payload = _minimal()
    payload["mystery_top_level"] = "boo"
    p = _write_plan(tmp_path, payload)
    with pytest.raises(Exception):
        Plan.from_yaml(p)


def test_unknown_scene_field_rejected(tmp_path):
    payload = _minimal()
    payload["scenes"][0]["mystery_scene_field"] = "boo"
    p = _write_plan(tmp_path, payload)
    with pytest.raises(Exception):
        Plan.from_yaml(p)


def test_old_top_level_model_field_rejected_with_helpful_message(tmp_path):
    payload = _minimal()
    payload.pop("image_model")
    payload["model"] = "mid"
    p = _write_plan(tmp_path, payload)
    with pytest.raises(Exception) as exc:
        Plan.from_yaml(p)
    msg = str(exc.value)
    assert "model" in msg
    assert "image_model" in msg


def test_old_top_level_animate_model_field_rejected_with_helpful_message(tmp_path):
    payload = _minimal()
    payload["animate_model"] = "kling"
    p = _write_plan(tmp_path, payload)
    with pytest.raises(Exception) as exc:
        Plan.from_yaml(p)
    msg = str(exc.value)
    assert "animate_model" in msg
    assert "video_model" in msg


def test_old_per_scene_animate_model_rejected(tmp_path):
    payload = _minimal()
    payload["scenes"][0]["animate_model"] = "kling"
    p = _write_plan(tmp_path, payload)
    with pytest.raises(Exception) as exc:
        Plan.from_yaml(p)
    assert "video_model" in str(exc.value)


def test_invalid_aspect_rejected(tmp_path):
    payload = _minimal()
    payload["aspect"] = "5:7"
    p = _write_plan(tmp_path, payload)
    with pytest.raises(Exception):
        Plan.from_yaml(p)


def test_per_scene_model_overrides(tmp_path):
    payload = _minimal()
    payload["scenes"][0]["image_model"] = "nano-banana"
    payload["scenes"][0]["video_model"] = "kling"
    payload["scenes"][0]["voice_model"] = "tts-mini"
    p = _write_plan(tmp_path, payload)
    plan = Plan.from_yaml(p)
    assert plan.scenes[0].image_model == "nano-banana"
    assert plan.scenes[0].video_model == "kling"
    assert plan.scenes[0].voice_model == "tts-mini"


def test_to_dict_round_trip_shape_preserved(tmp_path):
    payload = _minimal(
        resolution="1080x1920",
        captions="skip",
        headline="HELLO",
    )
    p = _write_plan(tmp_path, payload)
    plan = Plan.from_yaml(p)
    d = plan.to_dict()
    assert d["aspect"] == "9:16"
    assert d["voice"] == "nova"
    assert d["image_model"] == "mid"
    assert d["video_model"] == "mid"
    assert d["voice_model"] == "tts-mini"
    assert d["resolution"] == "1080x1920"
    assert d["captions"] == "skip"
    assert d["headline"] == "HELLO"
    assert isinstance(d["scenes"], list)
    assert d["scenes"][0]["index"] == 0


def test_avatar_block_parsed(tmp_path):
    payload = _minimal()
    payload["avatar"] = {
        "avatar_track": "media/avatar.mov",
        "position": "bottom_right",
        "size": 0.45,
        "chroma_key": "0x00FF00",
    }
    p = _write_plan(tmp_path, payload)
    plan = Plan.from_yaml(p)
    assert plan.avatar is not None
    assert plan.avatar.avatar_track == "media/avatar.mov"
    assert plan.avatar.position == "bottom_right"
    assert plan.avatar.size == 0.45
    assert plan.avatar.chroma_key == "0x00FF00"


def test_avatar_unknown_field_rejected(tmp_path):
    payload = _minimal()
    payload["avatar"] = {
        "avatar_track": "media/avatar.mov",
        "mystery": "x",
    }
    p = _write_plan(tmp_path, payload)
    with pytest.raises(Exception):
        Plan.from_yaml(p)


# --------------------------------------------------------------------------
# Regression: typed Plan flows into resolve_settings
# --------------------------------------------------------------------------

def test_resolve_settings_accepts_plan_object(tmp_path):
    """resolve_settings must accept a Plan model directly, not only a dict."""
    p = _write_plan(tmp_path, _minimal())
    plan = Plan.from_yaml(p)
    settings = resolve_settings(plan, tmp_path, p)
    assert settings.image_model == "mid"
    assert settings.voice == "nova"
    assert settings.aspect == "9:16"
    assert settings.voice_model == "tts-mini"


def test_resolve_settings_plan_defaults_match_dict_defaults(tmp_path):
    """Plan object and its to_dict() must produce identical Settings."""
    p = _write_plan(tmp_path, _minimal())
    plan = Plan.from_yaml(p)
    s_typed = resolve_settings(plan, tmp_path, p)
    s_dict = resolve_settings(plan.to_dict(), tmp_path, p)
    assert s_typed.image_model == s_dict.image_model
    assert s_typed.video_model == s_dict.video_model
    assert s_typed.voice == s_dict.voice
    assert s_typed.voice_model == s_dict.voice_model
    assert s_typed.aspect == s_dict.aspect
    assert s_typed.resolution == s_dict.resolution
    assert s_typed.skip_captions == s_dict.skip_captions
    assert s_typed.stills_only == s_dict.stills_only


def test_resolve_settings_plan_with_animate_resolution(tmp_path):
    """Plan-level animate_resolution is honoured by resolve_settings."""
    payload = _minimal()
    payload["animate_resolution"] = "720x1280"
    p = _write_plan(tmp_path, payload)
    plan = Plan.from_yaml(p)
    assert plan.animate_resolution == "720x1280"
    settings = resolve_settings(plan, tmp_path, p)
    assert settings.animate_resolution == "720x1280"


def test_per_scene_model_overrides_preserved_in_plan(tmp_path):
    """Per-scene image_model / video_model / voice_model survive Plan round-trip."""
    payload = _minimal()
    payload["scenes"][0]["image_model"] = "nano-banana"
    payload["scenes"][0]["video_model"] = "kling"
    payload["scenes"][0]["voice_model"] = "tts-hd"
    p = _write_plan(tmp_path, payload)
    plan = Plan.from_yaml(p)
    sc = plan.scenes[0]
    assert sc.image_model == "nano-banana"
    assert sc.video_model == "kling"
    assert sc.voice_model == "tts-hd"
    # Must also survive to_dict() so the stage blackboard sees them.
    d = plan.to_dict()
    assert d["scenes"][0]["image_model"] == "nano-banana"
    assert d["scenes"][0]["video_model"] == "kling"
    assert d["scenes"][0]["voice_model"] == "tts-hd"


def test_unknown_scene_field_rejected_at_load(tmp_path):
    """Unknown scene fields must raise at Plan.from_yaml time, not silently pass."""
    payload = _minimal()
    payload["scenes"][0]["typo_field"] = "oops"
    p = _write_plan(tmp_path, payload)
    with pytest.raises(Exception) as exc:
        Plan.from_yaml(p)
    assert "typo_field" in str(exc.value)


def test_renamed_scene_field_speed_rejected(tmp_path):
    """Old `speed:` key on a scene must be rejected with a rename hint."""
    payload = _minimal()
    payload["scenes"][0]["speed"] = 1.25
    p = _write_plan(tmp_path, payload)
    with pytest.raises(Exception) as exc:
        Plan.from_yaml(p)
    assert "voice_speed" in str(exc.value)


def test_per_scene_aspect_override_valid(tmp_path):
    """A scene-level `aspect:` override from the allowed set must parse."""
    payload = _minimal()
    payload["scenes"][0]["aspect"] = "16:9"
    p = _write_plan(tmp_path, payload)
    plan = Plan.from_yaml(p)
    assert plan.scenes[0].aspect == "16:9"


def test_per_scene_aspect_override_invalid_rejected(tmp_path):
    """A scene-level `aspect:` with an unsupported value must raise."""
    payload = _minimal()
    payload["scenes"][0]["aspect"] = "5:7"
    p = _write_plan(tmp_path, payload)
    with pytest.raises(Exception):
        Plan.from_yaml(p)


def test_plan_trim_pauses_defaults_to_true():
    plan = Plan.model_validate({
        "aspect": "9:16",
        "voice": "nova",
        "scenes": [{"index": 0, "vo_text": "hello"}],
    })
    assert plan.trim_pauses is True


def test_plan_trim_pauses_false():
    plan = Plan.model_validate({
        "aspect": "9:16",
        "voice": "nova",
        "trim_pauses": False,
        "scenes": [{"index": 0, "vo_text": "hello"}],
    })
    assert plan.trim_pauses is False


def test_plan_trim_pauses_float():
    plan = Plan.model_validate({
        "aspect": "9:16",
        "voice": "nova",
        "trim_pauses": 0.8,
        "scenes": [{"index": 0, "vo_text": "hello"}],
    })
    assert plan.trim_pauses == 0.8
