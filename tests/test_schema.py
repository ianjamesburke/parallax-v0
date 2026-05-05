"""Tests for `parallax schema` — self-documenting CLI subcommand."""
from __future__ import annotations

import json

import pytest

from parallax import cli
from parallax.brief import Brief
from parallax.plan import Plan


def test_schema_brief_exits_zero(capsys: pytest.CaptureFixture) -> None:
    rc = cli.main(["schema", "brief"])
    assert rc == 0


def test_schema_plan_exits_zero(capsys: pytest.CaptureFixture) -> None:
    rc = cli.main(["schema", "plan"])
    assert rc == 0


def test_schema_bare_exits_zero(capsys: pytest.CaptureFixture) -> None:
    rc = cli.main(["schema"])
    assert rc == 0


def test_schema_brief_contains_all_top_level_fields(capsys: pytest.CaptureFixture) -> None:
    cli.main(["schema", "brief"])
    out = capsys.readouterr().out
    schema = Brief.model_json_schema()
    for field in schema["properties"]:
        assert field in out, f"expected field '{field}' in schema brief output"


def test_schema_plan_contains_all_top_level_fields(capsys: pytest.CaptureFixture) -> None:
    cli.main(["schema", "plan"])
    out = capsys.readouterr().out
    schema = Plan.model_json_schema()
    for field in schema["properties"]:
        assert field in out, f"expected field '{field}' in schema plan output"


def test_schema_bare_contains_both(capsys: pytest.CaptureFixture) -> None:
    cli.main(["schema"])
    out = capsys.readouterr().out
    # Brief header
    assert "brief.yaml" in out.lower() or "brief" in out
    # Plan header
    assert "plan.yaml" in out.lower() or "plan" in out
    # Spot-check a field from each
    assert "goal" in out   # Brief field
    assert "scenes" in out  # shared


def test_schema_brief_json_is_valid_json_schema(capsys: pytest.CaptureFixture) -> None:
    rc = cli.main(["schema", "brief", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "properties" in parsed


def test_schema_plan_json_is_valid_json_schema(capsys: pytest.CaptureFixture) -> None:
    rc = cli.main(["schema", "plan", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "properties" in parsed


def test_schema_bare_json_errors(capsys: pytest.CaptureFixture) -> None:
    """--json without a target should error (ambiguous which schema)."""
    rc = cli.main(["schema", "--json"])
    assert rc != 0
    err = capsys.readouterr().err
    assert err.strip()


def test_schema_brief_nested_fields_present(capsys: pytest.CaptureFixture) -> None:
    """Nested fields should appear with dot notation."""
    cli.main(["schema", "brief"])
    out = capsys.readouterr().out
    # ProvidedAsset fields nested under assets.provided[]
    assert "assets" in out
    assert "script" in out


def test_schema_plan_nested_scene_fields_present(capsys: pytest.CaptureFixture) -> None:
    """PlanScene fields should appear flattened in plan output."""
    cli.main(["schema", "plan"])
    out = capsys.readouterr().out
    assert "scenes" in out
