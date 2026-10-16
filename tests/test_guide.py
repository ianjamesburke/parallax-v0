"""parallax guide: machine-readable workflow map that cannot drift from the CLI."""
from __future__ import annotations

import json

from parallax import cli


def test_guide_lists_only_commands_and_flags_that_exist():
    import click
    import typer
    from parallax.guide import build_guide

    group = typer.main.get_group(cli.app)
    guide = build_guide(group)
    ids = [s["id"] for s in guide["stages"]]
    assert ids[:4] == ["intake", "brief", "storyboard", "reference_gates"]
    assert all(s["next"] in ids or s["next"] is None for s in guide["stages"])
    for stage in guide["stages"]:
        for command in stage["commands"]:
            node = group
            for part in command["command"].split()[1:]:
                node = node.commands[part]
            real = {"/".join(p.opts) for p in node.params if isinstance(p, click.Option)}
            assert {f["name"] for f in command["flags"] if f["name"].startswith("-")} <= real
            required = {f["name"] for f in command["flags"] if f["required"]}
            if command["command"] != "parallax reference resolve":
                assert required <= set(command.get("pass", [])), f"{command['command']} requires {required}"
            assert set(command.get("pass", [])) <= {f["name"] for f in command["flags"]}
    gate_stage = next(s for s in guide["stages"] if s["id"] == "reference_gates")
    assert "supplied reference" in gate_stage["human_decision"]["question"]
    assert any(s["spends_money"] for s in guide["stages"]) and not guide["stages"][2]["spends_money"]


def test_guide_command_prints_parsable_json(capsys):
    assert cli.main(["guide"]) == 0
    guide = json.loads(capsys.readouterr().out)
    storyboard = next(c for s in guide["stages"] for c in s["commands"] if c["command"] == "parallax storyboard")
    assert {"--folder", "--brief"} <= {f["name"] for f in storyboard["flags"] if f["required"]}
    assert "--json" in {f["name"] for f in storyboard["flags"]}


def test_guide_documents_brief_shape_and_gate_wiring():
    from parallax.brief import Brief
    from parallax.guide import build_guide
    import typer
    guide = build_guide(typer.main.get_group(cli.app))["brief"]
    Brief.model_validate(guide["example"])
    assert guide["scene_fields"]["shot_type"]["values"] == ["broll", "character", "screen"]
    assert guide["scene_fields"]["scene_type"]["values"] == ["still", "animation", "motion_graphic"]
    detection = guide["reference_gates"]["detection"]
    assert "image_refs" in detection["editorial-reference"]["rule"] and "prompt" in detection["product-reference"]["rule"]
    assert "product gate before the editorial gate" in guide["reference_gates"]["order"]
    assert "never approves" in guide["reference_gates"]["review_status"]


def test_every_top_level_command_has_one_line_help():
    import typer
    group = typer.main.get_group(cli.app)
    missing = [name for name, command in group.commands.items() if not (command.get_short_help_str() or "").strip()]
    assert missing == []
