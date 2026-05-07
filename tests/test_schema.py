"""Tests for `parallax schema` — self-documenting CLI subcommand.

Layer: CONTRACT — validates schema subcommand parsing and Pydantic JSON Schema output.
No pipeline calls; no PARALLAX_TEST_MODE required."""
from __future__ import annotations

import json

import pytest

from parallax import cli
from parallax.brief import Brief
from parallax.plan import Plan


def test_schema_brief_exits_zero() -> None:
    rc = cli.main(["schema", "brief"])
    assert rc == 0


def test_schema_plan_exits_zero() -> None:
    rc = cli.main(["schema", "plan"])
    assert rc == 0


def test_schema_bare_exits_zero() -> None:
    rc = cli.main(["schema"])
    assert rc == 0


def test_schema_brief_is_valid_json_schema(capsys: pytest.CaptureFixture) -> None:
    rc = cli.main(["schema", "brief"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "properties" in parsed


def test_schema_plan_is_valid_json_schema(capsys: pytest.CaptureFixture) -> None:
    rc = cli.main(["schema", "plan"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "properties" in parsed


def test_schema_brief_contains_all_top_level_fields(capsys: pytest.CaptureFixture) -> None:
    cli.main(["schema", "brief"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    expected = Brief.model_json_schema()
    for field in expected["properties"]:
        assert field in parsed["properties"], f"expected field '{field}' in brief JSON Schema"


def test_schema_plan_contains_all_top_level_fields(capsys: pytest.CaptureFixture) -> None:
    cli.main(["schema", "plan"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    expected = Plan.model_json_schema()
    for field in expected["properties"]:
        assert field in parsed["properties"], f"expected field '{field}' in plan JSON Schema"


def test_schema_bare_contains_both(capsys: pytest.CaptureFixture) -> None:
    cli.main(["schema"])
    out = capsys.readouterr().out
    assert "brief.yaml" in out.lower() or "brief" in out
    assert "plan.yaml" in out.lower() or "plan" in out
    assert "goal" in out   # Brief field
    assert "scenes" in out  # shared


def test_schema_brief_nested_fields_present(capsys: pytest.CaptureFixture) -> None:
    """Nested models appear in $defs."""
    cli.main(["schema", "brief"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "assets" in parsed["properties"]
    assert "script" in parsed["properties"]


def test_schema_plan_nested_scene_fields_present(capsys: pytest.CaptureFixture) -> None:
    """PlanScene model appears in $defs."""
    cli.main(["schema", "plan"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "scenes" in parsed["properties"]


def test_schema_output_flag_writes_file(
    capsys: pytest.CaptureFixture, tmp_path
) -> None:
    out_file = tmp_path / "brief_schema.json"
    rc = cli.main(["schema", "brief", "--output", str(out_file)])
    assert rc == 0
    assert out_file.exists()
    parsed = json.loads(out_file.read_text())
    assert "properties" in parsed
    # stdout should be empty (wrote to file)
    assert capsys.readouterr().out == ""


def test_schema_output_flag_requires_target(capsys: pytest.CaptureFixture, tmp_path) -> None:
    rc = cli.main(["schema", "--output", str(tmp_path / "out.json")])
    assert rc != 0
    assert capsys.readouterr().err.strip()
