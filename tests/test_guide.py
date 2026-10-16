"""parallax guide: machine-readable workflow map that cannot drift from the CLI."""
from __future__ import annotations

import json

from parallax import cli


def test_guide_lists_only_commands_and_flags_that_exist():
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
            real = {"/".join(p.opts) for p in node.params if p.param_type_name == "option"}
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


def _topic(capsys, name):
    assert cli.main(["guide", name]) == 0
    return json.loads(capsys.readouterr().out)


def test_guide_topic_prints_a_small_parsable_slice(capsys):
    from parallax.guide import topic_names
    assert cli.main(["guide"]) == 0
    full = json.loads(capsys.readouterr().out)
    listed = full["topics"]["stages"] + full["topics"]["reference"]
    assert listed == topic_names()
    for name in listed:
        assert cli.main(["guide", name]) == 0
        out = capsys.readouterr().out
        assert json.loads(out)["topic"] == name
        assert len(out) < 16_000, f"guide {name} is too large to read in one call"


def test_guide_unknown_topic_names_the_choices(capsys):
    assert cli.main(["guide", "nope"]) == 2
    err = capsys.readouterr().err
    assert "unknown guide topic 'nope'" in err and "produce_loop" in err


def test_guide_topics_chain_and_cross_references_resolve(capsys):
    from parallax.guide import topic_names
    names = set(topic_names())
    full_stages = {}
    for name in names:
        data = _topic(capsys, name)
        if "stage" in data:
            full_stages[name] = data["stage"]
        target = data.get("next") if "stage" not in data else data["stage"]["next"]
        assert target is None or target in names
        for ref in (data["stage"].get("see", []) if "stage" in data else data.get("see", [])):
            assert ref in names
    assert {"produce_loop", "asset_locking", "models", "cost", "motion_graphic", "voiceover_broll"} <= names


def test_guide_topics_only_name_real_commands(capsys):
    """Every `parallax <cmd> [sub]` mentioned in an operator topic exists in the CLI."""
    import re
    import typer
    from parallax.guide_topics import TOPICS

    group = typer.main.get_group(cli.app)
    text = json.dumps(TOPICS)
    for mention in set(re.findall(r"`?parallax ((?:[a-z][a-z-]*)(?: [a-z][a-z-]*)?)", text)):
        words = mention.split()
        node = group
        assert words[0] in node.commands, f"`parallax {mention}` is not a command"
        node = node.commands[words[0]]
        if len(words) > 1 and hasattr(node, "commands"):
            assert words[1] in node.commands or words[1] in {"latest", "list", "scene"}, f"`parallax {mention}`: unknown subcommand"


def test_guide_topics_only_name_real_plan_fields():
    from parallax.plan import Plan, PlanScene
    plan_fields = {"stills_only", "audio_path", "words_path", "vo_text_hashes", "voice_postprocess", "trim_pauses", "voice_speed",
                   "image_model", "video_model", "voice_model", "style", "style_hint", "caption_style", "fontsize", "words_per_chunk",
                   "caption_shift_s", "captions", "resolution"}
    scene_fields = {"still_path", "clip_path", "end_frame_path", "regenerate", "reference", "reference_images", "image_model",
                    "video_model", "voice_model", "duration_s", "start_offset_s", "scene_type", "motion_prompt"}
    assert plan_fields <= set(Plan.model_fields)
    assert scene_fields <= set(PlanScene.model_fields)


def test_guide_says_there_is_no_yes_flag_on_paid_commands():
    import typer
    group = typer.main.get_group(cli.app)
    for name in ("produce", "plan"):
        opts = {opt for p in group.commands[name].params for opt in p.opts}
        assert "-y" not in opts and "--yes" not in opts


def _dry_run_plan(monkeypatch, tmp_path, scenes):
    import yaml
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    folder = tmp_path / "project"
    folder.mkdir()
    plan_path = folder / "plan.yaml"
    plan_path.write_text(yaml.safe_dump({"captions": "skip", "scenes": scenes}))
    return folder, plan_path


def test_dry_run_writes_no_locks_into_the_plan_so_real_estimates_stay_honest(monkeypatch, tmp_path):
    """Pins `guide produce_loop.dry_run`: a test-mode run leaves plan.yaml untouched and the real estimate still counts the scenes."""
    from parallax.preflight import compute_preflight
    from parallax.produce import run_plan

    folder, plan_path = _dry_run_plan(monkeypatch, tmp_path, [
        {"index": 0, "shot_type": "broll", "vo_text": "A typed pipeline.", "prompt": "A still."},
        {"index": 1, "shot_type": "broll", "vo_text": "Second beat.", "prompt": "Another.", "animate": True, "motion_prompt": "slow"},
    ])
    before = plan_path.read_text()

    assert run_plan(folder=folder, plan_path=plan_path).status == "ok"

    assert plan_path.read_text() == before
    monkeypatch.delenv("PARALLAX_TEST_MODE")
    import yaml
    estimate = compute_preflight(yaml.safe_load(plan_path.read_text()), folder=folder, output_resolution="720x1280")
    assert not any(sc.locked for sc in estimate.scenes) and estimate.estimated_total_usd > 0


def test_produce_without_ffmpeg_fails_fast_naming_it_and_how_to_install(monkeypatch, tmp_path):
    from parallax.produce import run_plan

    folder, plan_path = _dry_run_plan(monkeypatch, tmp_path, [{"index": 0, "vo_text": "Hi.", "prompt": "A still."}])
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    result = run_plan(folder=folder, plan_path=plan_path)

    assert result.status == "error"
    assert "ffmpeg" in result.error and "brew install ffmpeg-full" in result.error and "apt install ffmpeg" in result.error


def test_motion_graphic_placeholder_command_in_the_guide_renders_a_usable_clip(tmp_path, capsys):
    """The documented testsrc2 placeholder command runs and writes the clip."""
    import shlex
    import shutil
    import subprocess

    import pytest
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    cmd = _topic(capsys, "motion_graphic")["clip_convention"]["placeholder"]
    clip_cmd = next(c for c in cmd.split("`") if c.startswith("ffmpeg") and "final.mp4" in c).replace("<name>", "g")
    (tmp_path / "graphics" / "g").mkdir(parents=True)
    subprocess.run(shlex.split(clip_cmd), cwd=tmp_path, check=True)
    assert (tmp_path / "graphics" / "g" / "final.mp4").stat().st_size > 0
