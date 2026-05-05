from __future__ import annotations

import pytest
import yaml

from parallax.brief import Brief


def _write_brief(tmp_path, payload):
    p = tmp_path / "brief.yaml"
    p.write_text(yaml.safe_dump(payload, sort_keys=False))
    return p


def _minimal_payload(**overrides):
    base = {
        "goal": "Promote the new Lion energy drink",
        "aspect": "9:16",
        "voice": "nova",
        "voice_speed": 1.0,
        "success_criteria": ["Hook lands in <2s"],
        "assets": {
            "provided": [
                {"path": "brand/logo.png", "kind": "product_ref"},
            ],
            "generated": [
                {"kind": "still", "scene_index": 0},
            ],
        },
        "script": {
            "scenes": [
                {
                    "index": 0,
                    "shot_type": "character",
                    "vo_text": "Lions don't apologize.",
                    "prompt": "Founder holding the can in golden hour...",
                },
            ],
        },
    }
    base.update(overrides)
    return base


def test_minimal_brief_parses(tmp_path):
    p = _write_brief(tmp_path, _minimal_payload())
    brief = Brief.from_yaml(p)
    assert brief.goal.startswith("Promote")
    assert brief.aspect == "9:16"
    assert brief.voice == "nova"
    assert brief.voice_speed == 1.0
    assert len(brief.script.scenes) == 1
    assert brief.assets.provided[0].kind == "product_ref"


def test_brief_defaults_apply_when_optional_fields_omitted(tmp_path):
    p = _write_brief(tmp_path, {
        "goal": "x",
        "script": {"scenes": [{"index": 0, "vo_text": "v", "prompt": "p"}]},
    })
    brief = Brief.from_yaml(p)
    assert brief.aspect == "9:16"
    assert brief.voice == "nova"
    assert brief.voice_speed == 1.0
    assert brief.script.scenes[0].shot_type == "broll"
    assert brief.script.scenes[0].animate is False


def test_invalid_aspect_raises(tmp_path):
    p = _write_brief(tmp_path, _minimal_payload(aspect="5:7"))
    with pytest.raises(Exception):
        Brief.from_yaml(p)


def test_invalid_shot_type_raises(tmp_path):
    payload = _minimal_payload()
    payload["script"]["scenes"][0]["shot_type"] = "lifestyle"
    p = _write_brief(tmp_path, payload)
    with pytest.raises(Exception):
        Brief.from_yaml(p)


def test_invalid_provided_kind_raises(tmp_path):
    payload = _minimal_payload()
    payload["assets"]["provided"][0]["kind"] = "logo_ref"
    p = _write_brief(tmp_path, payload)
    with pytest.raises(Exception):
        Brief.from_yaml(p)


def test_extra_field_at_top_rejected(tmp_path):
    payload = _minimal_payload()
    payload["mystery_field"] = "boo"
    p = _write_brief(tmp_path, payload)
    with pytest.raises(Exception):
        Brief.from_yaml(p)


def test_validate_assets_returns_missing_paths(tmp_path):
    payload = _minimal_payload()
    p = _write_brief(tmp_path, payload)
    brief = Brief.from_yaml(p)
    missing = brief.validate_assets(tmp_path)
    assert len(missing) == 1
    assert "brand/logo.png" in missing[0]


def test_validate_assets_passes_when_files_exist(tmp_path):
    (tmp_path / "brand").mkdir()
    (tmp_path / "brand" / "logo.png").write_bytes(b"\x89PNG")
    p = _write_brief(tmp_path, _minimal_payload())
    brief = Brief.from_yaml(p)
    assert brief.validate_assets(tmp_path) == []


def test_validate_assets_ignores_generated_inventory(tmp_path):
    """Generated assets shouldn't be required to exist — they're hints."""
    payload = _minimal_payload()
    payload["assets"]["provided"] = []  # nothing required
    payload["assets"]["generated"] = [
        {"kind": "video_clip", "scene_index": 99},  # not present anywhere
    ]
    p = _write_brief(tmp_path, payload)
    brief = Brief.from_yaml(p)
    assert brief.validate_assets(tmp_path) == []


def test_to_plan_skeleton_carries_aspect_voice_and_scenes(tmp_path):
    payload = _minimal_payload(aspect="16:9", voice="Puck", voice_speed=1.2)
    payload["script"]["scenes"].append({
        "index": 1, "shot_type": "broll", "animate": True,
        "vo_text": "v2", "prompt": "p2", "motion_prompt": "slow zoom",
    })
    p = _write_brief(tmp_path, payload)
    brief = Brief.from_yaml(p)
    plan = brief.to_plan_skeleton()
    assert plan["aspect"] == "16:9"
    assert plan["voice"] == "Puck"
    assert plan["voice_speed"] == 1.2
    assert len(plan["scenes"]) == 2
    assert plan["scenes"][1]["animate"] is True
    assert plan["scenes"][1]["motion_prompt"] == "slow zoom"


def test_image_refs_list_parses(tmp_path):
    payload = _minimal_payload()
    payload["script"]["scenes"][0]["image_refs"] = ["assets/char.png", "assets/product.png"]
    p = _write_brief(tmp_path, payload)
    brief = Brief.from_yaml(p)
    assert brief.script.scenes[0].image_refs == ["assets/char.png", "assets/product.png"]


def test_image_refs_flows_into_plan_skeleton_as_reference_images(tmp_path):
    payload = _minimal_payload()
    payload["script"]["scenes"][0]["image_refs"] = ["assets/char.png", "assets/product.png"]
    p = _write_brief(tmp_path, payload)
    brief = Brief.from_yaml(p)
    plan = brief.to_plan_skeleton()
    assert plan["scenes"][0]["reference_images"] == ["assets/char.png", "assets/product.png"]


def test_deprecated_image_ref_migrates_to_image_refs(tmp_path):
    payload = _minimal_payload()
    payload["script"]["scenes"][0]["image_ref"] = "assets/bottle.png"
    p = _write_brief(tmp_path, payload)
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        brief = Brief.from_yaml(p)
    assert brief.script.scenes[0].image_refs == ["assets/bottle.png"]
    assert any("deprecated" in str(warning.message).lower() for warning in w)


def test_deprecated_image_ref_flows_into_plan_skeleton_as_reference_images(tmp_path):
    payload = _minimal_payload()
    payload["script"]["scenes"][0]["image_ref"] = "assets/bottle.png"
    p = _write_brief(tmp_path, payload)
    import warnings
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        brief = Brief.from_yaml(p)
    plan = brief.to_plan_skeleton()
    assert plan["scenes"][0]["reference_images"] == ["assets/bottle.png"]


def test_scene_without_image_refs_has_no_reference_images_in_skeleton(tmp_path):
    payload = _minimal_payload()
    p = _write_brief(tmp_path, payload)
    brief = Brief.from_yaml(p)
    plan = brief.to_plan_skeleton()
    assert "reference_images" not in plan["scenes"][0]


def test_character_reference_sets_reference_flag_on_character_scenes(tmp_path):
    payload = _minimal_payload()
    payload["character_reference"] = True
    # _minimal_payload scene 0 is shot_type "character"
    p = _write_brief(tmp_path, payload)
    brief = Brief.from_yaml(p)
    plan = brief.to_plan_skeleton()
    assert plan["scenes"][0]["reference"] is True


def test_character_reference_does_not_affect_broll_scenes(tmp_path):
    payload = _minimal_payload()
    payload["character_reference"] = True
    payload["script"]["scenes"].append({
        "index": 1,
        "shot_type": "broll",
        "vo_text": "broll line",
        "prompt": "wide shot",
    })
    p = _write_brief(tmp_path, payload)
    brief = Brief.from_yaml(p)
    plan = brief.to_plan_skeleton()
    assert plan["scenes"][0]["reference"] is True      # character
    assert "reference" not in plan["scenes"][1]        # broll


def test_character_reference_false_leaves_no_reference_flag(tmp_path):
    payload = _minimal_payload()
    payload["character_reference"] = False
    p = _write_brief(tmp_path, payload)
    brief = Brief.from_yaml(p)
    plan = brief.to_plan_skeleton()
    assert "reference" not in plan["scenes"][0]


def test_per_scene_aspect_override_validated(tmp_path):
    payload = _minimal_payload()
    payload["script"]["scenes"][0]["aspect"] = "16:9"
    p = _write_brief(tmp_path, payload)
    brief = Brief.from_yaml(p)
    assert brief.script.scenes[0].aspect == "16:9"


def test_per_scene_aspect_override_rejected_if_invalid(tmp_path):
    payload = _minimal_payload()
    payload["script"]["scenes"][0]["aspect"] = "21:9"
    p = _write_brief(tmp_path, payload)
    with pytest.raises(Exception):
        Brief.from_yaml(p)
