"""Tests for `parallax schema` — self-documenting CLI subcommand."""
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


# ---------------------------------------------------------------------------
# parallax schema cli
# ---------------------------------------------------------------------------

def test_schema_cli_exits_zero() -> None:
    rc = cli.main(["schema", "cli"])
    assert rc == 0


def test_schema_cli_is_valid_json(capsys: pytest.CaptureFixture) -> None:
    rc = cli.main(["schema", "cli"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "commands" in parsed


def test_schema_cli_contains_top_level_commands(capsys: pytest.CaptureFixture) -> None:
    cli.main(["schema", "cli"])
    parsed = json.loads(capsys.readouterr().out)
    names = {c["name"] for c in parsed["commands"]}
    expected = {"produce", "models", "audio", "video", "image", "log", "usage",
                "credits", "update", "completions", "verify", "schema", "validate"}
    assert expected.issubset(names), f"missing: {expected - names}"


def test_schema_cli_commands_have_args(capsys: pytest.CaptureFixture) -> None:
    cli.main(["schema", "cli"])
    parsed = json.loads(capsys.readouterr().out)
    produce = next(c for c in parsed["commands"] if c["name"] == "produce")
    arg_dests = {a["dest"] for a in produce["args"]}
    assert "folder" in arg_dests
    assert "plan" in arg_dests


def test_schema_cli_subcommands_nested(capsys: pytest.CaptureFixture) -> None:
    cli.main(["schema", "cli"])
    parsed = json.loads(capsys.readouterr().out)
    audio = next(c for c in parsed["commands"] if c["name"] == "audio")
    sub_names = {c["name"] for c in audio["commands"]}
    assert "transcribe" in sub_names
    assert "speed" in sub_names


def test_schema_cli_output_flag_writes_file(
    capsys: pytest.CaptureFixture, tmp_path
) -> None:
    out_file = tmp_path / "cli_schema.json"
    rc = cli.main(["schema", "cli", "--output", str(out_file)])
    assert rc == 0
    assert out_file.exists()
    parsed = json.loads(out_file.read_text())
    assert "commands" in parsed
    assert capsys.readouterr().out == ""
