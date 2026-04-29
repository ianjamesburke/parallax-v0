from __future__ import annotations

import pytest
import yaml

from parallax.plan import Plan


def _write_plan(tmp_path, payload):
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.safe_dump(payload, sort_keys=False))
    return p


def _minimal(**overrides):
    base = {
        "aspect": "9:16",
        "voice": "Kore",
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
    assert plan.voice == "Kore"
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
    assert plan.voice == "Kore"
    assert plan.voice_speed == 1.0
    assert plan.image_model == "mid"
    assert plan.video_model == "mid"
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
    assert d["voice"] == "Kore"
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
