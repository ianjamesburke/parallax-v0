"""Tests for parallax validate — brief and plan dry-run validation."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.dump(data))


# ──────────────────────── Brief validation ────────────────────────


def test_validate_brief_valid(tmp_path):
    brief = tmp_path / "brief.yaml"
    _write_yaml(brief, {
        "goal": "Test",
        "script": {"scenes": [{"index": 0, "vo_text": "hi", "prompt": "a scene"}]},
    })
    from parallax.validate import validate_brief
    result = validate_brief(brief, tmp_path)
    assert result["valid"] is True
    assert result["errors"] == []


def test_validate_brief_missing_provided_asset(tmp_path):
    brief = tmp_path / "brief.yaml"
    _write_yaml(brief, {
        "goal": "Test",
        "assets": {"provided": [{"path": "missing.png", "kind": "style_ref"}]},
        "script": {"scenes": [{"index": 0, "vo_text": "hi", "prompt": "scene"}]},
    })
    from parallax.validate import validate_brief
    result = validate_brief(brief, tmp_path)
    assert result["valid"] is False
    assert any("missing.png" in e["message"] for e in result["errors"])


def test_validate_brief_product_ref_warns(tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"fake")
    brief = tmp_path / "brief.yaml"
    _write_yaml(brief, {
        "goal": "Test",
        "assets": {"provided": [{"path": "logo.png", "kind": "product_ref"}]},
        "script": {"scenes": [{"index": 0, "vo_text": "hi", "prompt": "scene"}]},
    })
    from parallax.validate import validate_brief
    result = validate_brief(brief, tmp_path)
    assert result["valid"] is True
    assert any("#83" in w["message"] for w in result["warnings"])


def test_validate_brief_bad_schema(tmp_path):
    brief = tmp_path / "brief.yaml"
    _write_yaml(brief, {"goal": "Test", "aspect": "bad", "script": {"scenes": []}})
    from parallax.validate import validate_brief
    result = validate_brief(brief, tmp_path)
    assert result["valid"] is False
    assert any("schema error" in e["message"] for e in result["errors"])


def test_validate_brief_missing_image_refs(tmp_path):
    brief = tmp_path / "brief.yaml"
    _write_yaml(brief, {
        "goal": "Test",
        "script": {"scenes": [
            {"index": 0, "vo_text": "hi", "prompt": "scene", "image_refs": ["ref.png"]},
        ]},
    })
    from parallax.validate import validate_brief
    result = validate_brief(brief, tmp_path)
    assert result["valid"] is False
    assert any("image_refs" in e["field"] for e in result["errors"])


def test_validate_brief_file_not_found(tmp_path):
    from parallax.validate import validate_brief
    result = validate_brief(tmp_path / "no.yaml", tmp_path)
    assert result["valid"] is False
    assert any("brief" in e["field"] for e in result["errors"])


# ──────────────────────── Plan validation ────────────────────────


def test_validate_plan_valid(tmp_path):
    plan = tmp_path / "plan.yaml"
    _write_yaml(plan, {
        "scenes": [{"index": 0, "vo_text": "hi", "prompt": "scene"}],
    })
    from parallax.validate import validate_plan
    result = validate_plan(plan, tmp_path)
    assert result["valid"] is True
    assert result["errors"] == []


def test_validate_plan_missing_still_path(tmp_path):
    plan = tmp_path / "plan.yaml"
    _write_yaml(plan, {
        "scenes": [{"index": 0, "vo_text": "hi", "prompt": "scene", "still_path": "no.png"}],
    })
    from parallax.validate import validate_plan
    result = validate_plan(plan, tmp_path)
    assert result["valid"] is False
    assert any("still_path" in e["field"] for e in result["errors"])


def test_validate_plan_locked_audio_exists(tmp_path):
    audio = tmp_path / "vo.mp3"
    audio.write_bytes(b"fake")
    plan = tmp_path / "plan.yaml"
    _write_yaml(plan, {
        "audio_path": "vo.mp3",
        "scenes": [{"index": 0, "vo_text": "hi", "prompt": "scene"}],
    })
    from parallax.validate import validate_plan
    result = validate_plan(plan, tmp_path)
    assert result["valid"] is True


def test_validate_plan_locked_audio_missing(tmp_path):
    plan = tmp_path / "plan.yaml"
    _write_yaml(plan, {
        "audio_path": "missing_vo.mp3",
        "scenes": [{"index": 0, "vo_text": "hi", "prompt": "scene"}],
    })
    from parallax.validate import validate_plan
    result = validate_plan(plan, tmp_path)
    assert result["valid"] is False
    assert any("audio_path" in e["field"] for e in result["errors"])


def test_validate_plan_bad_schema(tmp_path):
    plan = tmp_path / "plan.yaml"
    _write_yaml(plan, {"aspect": "bad", "scenes": []})
    from parallax.validate import validate_plan
    result = validate_plan(plan, tmp_path)
    assert result["valid"] is False
    assert any("schema error" in e["message"] for e in result["errors"])


def test_validate_plan_file_not_found(tmp_path):
    from parallax.validate import validate_plan
    result = validate_plan(tmp_path / "no.yaml", tmp_path)
    assert result["valid"] is False
    assert any("plan" in e["field"] for e in result["errors"])


def test_validate_plan_reference_images_missing(tmp_path):
    plan = tmp_path / "plan.yaml"
    _write_yaml(plan, {
        "scenes": [{"index": 0, "vo_text": "hi", "prompt": "scene",
                    "reference_images": ["ref1.png", "ref2.png"]}],
    })
    from parallax.validate import validate_plan
    result = validate_plan(plan, tmp_path)
    assert result["valid"] is False
    assert len([e for e in result["errors"] if "reference_images" in e["field"]]) == 2


def test_validate_plan_reference_images_resolve_from_folder_not_plan_dir(tmp_path):
    """Issue #64: validate_plan must resolve relative reference_images against --folder, not plan dir."""
    folder = tmp_path / "github" / "PX0001"
    folder.mkdir(parents=True)
    plan_dir = tmp_path / "drive" / "PX0001" / "parallax" / "scratch"
    plan_dir.mkdir(parents=True)

    ref_file = folder / "bottle.png"
    ref_file.write_bytes(b"")

    plan = plan_dir / "plan.yaml"
    _write_yaml(plan, {
        "scenes": [{"index": 0, "vo_text": "hi", "prompt": "scene",
                    "reference_images": ["bottle.png"]}],
    })

    from parallax.validate import validate_plan
    result = validate_plan(plan, folder)
    # bottle.png exists in folder, so no reference_images errors
    ref_errors = [e for e in result["errors"] if "reference_images" in e["field"]]
    assert ref_errors == [], f"Expected no reference_images errors but got: {ref_errors}"


def test_validate_plan_bare_colon_in_prompt(tmp_path):
    """validate_plan must catch bare colons in prompt with a specific error."""
    plan = tmp_path / "plan.yaml"
    # Write raw YAML with an unquoted colon in prompt — yaml.safe_load misparsing
    plan.write_text(
        "scenes:\n"
        "  - index: 0\n"
        "    vo_text: hello\n"
        "    prompt: movement: she leans in\n"
    )
    from parallax.validate import validate_plan
    result = validate_plan(plan, tmp_path)
    assert result["valid"] is False
    colon_errors = [e for e in result["errors"] if "bare colon" in e["message"]]
    assert len(colon_errors) >= 1
    assert "prompt" in colon_errors[0]["field"]


def test_validate_plan_bare_colon_in_vo_text(tmp_path):
    """validate_plan catches bare colons in vo_text field."""
    plan = tmp_path / "plan.yaml"
    plan.write_text(
        "scenes:\n"
        "  - index: 0\n"
        "    vo_text: key: value style text\n"
        "    prompt: normal prompt\n"
    )
    from parallax.validate import validate_plan
    result = validate_plan(plan, tmp_path)
    assert result["valid"] is False
    assert any("vo_text" in e["field"] for e in result["errors"])


def test_validate_plan_quoted_colon_ok(tmp_path):
    """validate_plan must accept properly quoted colons in prompts."""
    plan = tmp_path / "plan.yaml"
    _write_yaml(plan, {
        "scenes": [{"index": 0, "vo_text": "hello", "prompt": "movement: she leans in"}],
    })
    from parallax.validate import validate_plan
    result = validate_plan(plan, tmp_path)
    assert result["valid"] is True
