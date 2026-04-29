from __future__ import annotations

from pathlib import Path

import yaml

from parallax.planner import PlanResult, plan_from_brief


# ---------------------------------------------------------------- helpers

def _write_brief(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "brief.yaml"
    p.write_text(yaml.safe_dump(payload, sort_keys=False))
    return p


def _payload(**overrides) -> dict:
    base = {
        "goal": "Promote the new Lion energy drink",
        "aspect": "9:16",
        "voice": "Kore",
        "voice_speed": 1.0,
        "assets": {
            "provided": [
                {"path": "brand/logo.png", "kind": "product_ref",
                 "description": "Lion can"},
                {"path": "brand/founder.png", "kind": "character_ref",
                 "description": "Founder portrait"},
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
                {
                    "index": 1,
                    "shot_type": "broll",
                    "animate": True,
                    "vo_text": "Power is silent.",
                    "prompt": "Close-up of the can on a wood table.",
                    "motion_prompt": "Slow zoom in",
                },
            ],
        },
    }
    base.update(overrides)
    return base


def _materialize_assets(folder: Path) -> None:
    """Create the provided assets used by `_payload` so validation passes."""
    (folder / "brand").mkdir(exist_ok=True)
    (folder / "brand" / "logo.png").write_bytes(b"\x89PNG")
    (folder / "brand" / "founder.png").write_bytes(b"\x89PNG")


# ----------------------------------------------------------------- tests

def test_happy_path_writes_plan(tmp_path):
    brief_path = _write_brief(tmp_path, _payload())
    _materialize_assets(tmp_path)

    result = plan_from_brief(brief_path, folder=tmp_path)

    assert isinstance(result, PlanResult)
    assert result.ok is True
    assert result.questions_path is None
    assert result.missing_assets == []
    assert result.scene_count == 2
    assert result.plan_path is not None
    assert result.plan_path == (tmp_path / "parallax" / "scratch" / "plan.yaml")
    assert result.plan_path.is_file()

    plan = yaml.safe_load(result.plan_path.read_text())
    assert plan["aspect"] == "9:16"
    assert plan["voice"] == "Kore"
    assert plan["speed"] == 1.0
    assert plan["model"] == "mid"
    assert plan["caption_style"] == "anton"
    assert "character_image" in plan
    assert plan["character_image"].endswith("brand/founder.png")
    assert len(plan["scenes"]) == 2


def test_missing_asset_writes_questions_and_skips_plan(tmp_path):
    brief_path = _write_brief(tmp_path, _payload())
    # Intentionally do NOT create the assets.

    result = plan_from_brief(brief_path, folder=tmp_path)

    assert result.ok is False
    assert result.plan_path is None
    assert result.questions_path == (
        tmp_path / "parallax" / "scratch" / "questions.yaml"
    )
    assert result.questions_path.is_file()
    assert len(result.missing_assets) == 2

    # plan.yaml must NOT be written when assets are missing.
    assert not (tmp_path / "parallax" / "scratch" / "plan.yaml").exists()

    questions = yaml.safe_load(result.questions_path.read_text())
    assert "reason" in questions
    assert isinstance(questions["missing"], list)
    assert len(questions["missing"]) == 2
    kinds = {m["kind"] for m in questions["missing"]}
    assert kinds == {"product_ref", "character_ref"}
    # Every entry has the original description carried through.
    for entry in questions["missing"]:
        assert "path" in entry
        assert Path(entry["path"]).is_absolute()
        assert "description" in entry


def test_plan_output_round_trips(tmp_path):
    payload = _payload()
    brief_path = _write_brief(tmp_path, payload)
    _materialize_assets(tmp_path)

    result = plan_from_brief(brief_path, folder=tmp_path)
    assert result.ok is True

    plan = yaml.safe_load(result.plan_path.read_text())
    # Every scene from the brief is represented in the plan, in order,
    # carrying its prompt and vo_text verbatim.
    assert [s["index"] for s in plan["scenes"]] == [0, 1]
    for brief_scene, plan_scene in zip(payload["script"]["scenes"], plan["scenes"]):
        assert plan_scene["vo_text"] == brief_scene["vo_text"]
        assert plan_scene["prompt"] == brief_scene["prompt"]


def test_out_path_override(tmp_path):
    brief_path = _write_brief(tmp_path, _payload())
    _materialize_assets(tmp_path)
    custom = tmp_path / "custom" / "my_plan.yaml"

    result = plan_from_brief(brief_path, folder=tmp_path, out_path=custom)

    assert result.ok is True
    assert result.plan_path == custom.resolve()
    assert custom.is_file()
    # Default scratch path was NOT written.
    assert not (tmp_path / "parallax" / "scratch" / "plan.yaml").exists()


def test_character_image_set_from_first_character_ref(tmp_path):
    brief_path = _write_brief(tmp_path, _payload())
    _materialize_assets(tmp_path)

    result = plan_from_brief(brief_path, folder=tmp_path)
    plan = yaml.safe_load(result.plan_path.read_text())

    assert "character_image" in plan
    expected = str((tmp_path / "brand" / "founder.png").resolve())
    assert plan["character_image"] == expected


def test_character_image_omitted_when_no_character_ref(tmp_path):
    payload = _payload()
    # Strip the character_ref; keep only the product_ref.
    payload["assets"]["provided"] = [
        {"path": "brand/logo.png", "kind": "product_ref",
         "description": "Lion can"},
    ]
    brief_path = _write_brief(tmp_path, payload)
    (tmp_path / "brand").mkdir()
    (tmp_path / "brand" / "logo.png").write_bytes(b"\x89PNG")

    result = plan_from_brief(brief_path, folder=tmp_path)
    plan = yaml.safe_load(result.plan_path.read_text())

    assert "character_image" not in plan


def test_plan_preserves_motion_prompt_and_animate(tmp_path):
    brief_path = _write_brief(tmp_path, _payload())
    _materialize_assets(tmp_path)

    result = plan_from_brief(brief_path, folder=tmp_path)
    plan = yaml.safe_load(result.plan_path.read_text())

    scene_one = plan["scenes"][1]
    assert scene_one["index"] == 1
    assert scene_one["animate"] is True
    assert scene_one["motion_prompt"] == "Slow zoom in"
