"""Storyboard package, reference gates, guide, and the no-spend guarantee."""
from __future__ import annotations

import json
import shlex
import socket
from pathlib import Path

import pytest
import yaml
from PIL import Image

from parallax import cli
from parallax.brief import Brief


@pytest.fixture(autouse=True)
def no_provider_calls(monkeypatch):
    """Any socket connection in this module's tests is a provider call."""
    def forbidden(*args, **kwargs):
        pytest.fail("provider/network call attempted during a no-spend storyboard flow")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def _png(path: Path, color="#aabbcc") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 96), color).save(path)


def _project(tmp_path: Path, **extra) -> tuple[Path, Path]:
    brief = {
        "goal": "Demo ad",
        "concept_name": "Demo",
        "headline": "Wake up sharp",
        "script": {"scenes": [
            {"index": 0, "vo_text": "One two three.", "prompt": "A person at a desk", "still_path": "stills/a.png"},
            {"index": 1, "vo_text": "Show it.", "prompt": "The product app logo on a phone", "on_screen_text": "SHORT COPY"},
        ]},
        **extra,
    }
    _png(tmp_path / "stills/a.png")
    path = tmp_path / "brief.yaml"
    path.write_text("# authored by hand\n" + yaml.safe_dump(brief, sort_keys=False))
    return tmp_path, path


def _storyboard(folder: Path, brief: Path, capsys, *extra) -> dict:
    capsys.readouterr()  # drop output from earlier commands in the test
    assert cli.main(["storyboard", "--folder", str(folder), "--brief", str(brief), "--json", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def test_package_contains_every_visible_file_and_needs_no_hidden_state(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    manifest = _storyboard(folder, brief, capsys)
    package = Path(manifest["package_dir"])
    assert package == folder.resolve() / "storyboard-package"
    expected = {"brief.yaml", "script.md", "references.yaml", "scenes.yaml", "storyboard.html",
                "contact-sheet.jpg", "qa-notes.md", "handoff.json"}
    assert {f["path"] for f in manifest["files"]} == expected
    assert expected <= {p.name for p in package.iterdir()}
    assert not [p for p in package.rglob("*") if p.name.startswith(".")], "no hidden files or directories"
    assert (package / "assets/stills/a.png").is_file()
    assert manifest["contact_sheet_scenes"] == [0]
    assert (package / "brief.yaml").read_bytes() == brief.read_bytes()
    assert manifest["requires_parallax"] is False and manifest["provider_calls"] == 0 and manifest["approved"] is False
    on_disk = json.loads((package / "handoff.json").read_text())
    assert {k: v for k, v in on_disk.items() if k not in {"gates", "next_steps"}} == {
        k: v for k, v in manifest.items() if k not in {"package_dir", "html", "gates", "next_steps"}}
    assert [g["id"] for g in on_disk["gates"]] == [g["id"] for g in manifest["gates"]]
    assert manifest["html"] == str(package / "storyboard.html") and Path(manifest["html"]).is_file()
    script = (package / "script.md").read_text()
    assert "Wake up sharp" in script and "One two three." in script
    scenes = yaml.safe_load((package / "scenes.yaml").read_text())
    assert scenes["asset_root"] == "assets" and [s["index"] for s in scenes["scenes"]] == [0, 1]
    assert scenes["scenes"][0]["end_s"] > scenes["scenes"][0]["start_s"] == 0
    qa = (package / "qa-notes.md").read_text()
    assert "editorial-reference" in qa and "not approval" in qa


def test_package_html_is_portable(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    package = Path(_storyboard(folder, brief, capsys)["package_dir"])
    html = (package / "storyboard.html").read_text()
    assert 'href="assets/stills/a.png"' in html
    assert "file://" not in html
    moved = tmp_path / "elsewhere" / "pkg"
    moved.parent.mkdir()
    package.rename(moved)
    assert (moved / "assets/stills/a.png").is_file()


def test_missing_assets_are_listed_and_outside_assets_are_rejected(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    (folder / "stills/a.png").unlink()
    manifest = _storyboard(folder, brief, capsys)
    assert manifest["missing_assets"] == ["stills/a.png"] and manifest["contact_sheet_scenes"] == []
    assert not (Path(manifest["package_dir"]) / "contact-sheet.jpg").exists()
    outside = tmp_path.parent / "outside.png"
    _png(outside)
    brief.write_text(yaml.safe_dump({"goal": "g", "script": {"scenes": [
        {"index": 0, "vo_text": "x", "prompt": "p", "still_path": str(outside)}]}}))
    assert cli.main(["storyboard", "--folder", str(folder), "--brief", str(brief)]) == 2
    assert "outside the project folder" in capsys.readouterr().err


def test_rerun_refreshes_and_foreign_directories_are_protected(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    _storyboard(folder, brief, capsys)
    again = _storyboard(folder, brief, capsys)
    assert again["status"] == "needs_reference_decision"
    foreign = tmp_path / "notes"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("mine")
    assert cli.main(["storyboard", "--folder", str(folder), "--brief", str(brief), "--package-dir", str(foreign)]) == 2
    assert "not a storyboard package" in capsys.readouterr().err
    assert (foreign / "keep.txt").read_text() == "mine"


def test_gates_carry_question_choices_and_exact_commands(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    manifest = _storyboard(folder, brief, capsys)
    assert manifest["status"] == "needs_reference_decision"
    gates = {g["id"]: g for g in manifest["gates"]}
    editorial, product = gates["editorial-reference"], gates["product-reference"]
    assert editorial["scenes"] == [0, 1] and product["scenes"] == [1]
    assert [c["id"] for c in editorial["choices"]] == ["supplied", "official", "generated_anchor", "no_reference"]
    assert [c["id"] for c in product["choices"]] == ["supplied", "official", "no_reference"]
    assert product["excluded_choices"][0]["id"] == "generated_anchor"
    for gate in gates.values():
        assert gate["question"] and gate["blocks"] == ["parallax produce --brief"]
        for choice in gate["choices"]:
            argv = shlex.split(choice["command"])
            assert argv[:3] == ["parallax", "reference", "resolve"]
            assert argv[argv.index("--gate") + 1] == gate["id"] and argv[argv.index("--choice") + 1] == choice["id"]
            assert argv[argv.index("--brief") + 1] == str(brief)
            assert choice["human_decision"] and choice["fill"]
    generated = next(c for c in editorial["choices"] if c["id"] == "generated_anchor")
    assert "--authorized-by" in generated["command"] and "--asset" not in generated["command"]
    assert generated["then"][0].startswith("parallax image generate") and "--asset <asset>" in generated["then"][1]
    assert "explicitly authorizes" in generated["human_decision"] and editorial["state"] == "open"
    assert {"reads", "rule", "order_note"} <= set(editorial["detection"]) and "image_refs" in editorial["detection"]["rule"]
    assert "product_ref" in product["detection"]["order_note"]


def test_scene_gate_commands_resolve_through_the_cli(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    manifest = _storyboard(folder, brief, capsys)
    fills = {"<asset>": "refs/style.png", "<source-url>": "https://example.com/brand", "<reason>": "generic placeholder b-roll"}
    _png(folder / "refs/style.png")
    for gate_id, choice_id in (("editorial-reference", "supplied"), ("product-reference", "no_reference")):
        choice = next(c for g in manifest["gates"] if g["id"] == gate_id for c in g["choices"] if c["id"] == choice_id)
        argv = [fills.get(t, t) for t in shlex.split(choice["command"])][1:]
        assert cli.main(argv) == 0, argv
        capsys.readouterr()
        manifest = _storyboard(folder, brief, capsys)
        if gate_id == "editorial-reference":
            assert [g["id"] for g in manifest["gates"]] == ["product-reference"], "a style reference does not answer the product gate"
    assert manifest["gates"] == [] and manifest["status"] == "ready_for_review"
    assert manifest["next_steps"][0]["step"] == "human_review"
    resolved = Brief.from_yaml(brief)
    assert [r.choice for r in resolved.reference_decisions] == ["supplied", "no_reference"]
    assert resolved.script.scenes[1].image_refs == ["refs/style.png"]
    assert resolved.assets.provided[0].kind == "style_ref" and resolved.assets.provided[0].origin == "supplied" and resolved.assets.provided[0].review_status == "pending"
    assert brief.read_text().startswith("# authored by hand"), "comments survive the edit"


def test_resolution_edits_only_the_intended_keys(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    before = yaml.safe_load(brief.read_text())
    _png(folder / "refs/logo.png")
    argv = ["reference", "resolve", "--folder", str(folder), "--brief", str(brief), "--gate", "product-reference",
            "--choice", "official", "--asset", "refs/logo.png", "--source-url", "https://example.com/press"]
    assert cli.main(argv) == 0
    after = yaml.safe_load(brief.read_text())
    assert set(after) - set(before) == {"assets", "reference_decisions"}
    assert after["script"]["scenes"][0] == before["script"]["scenes"][0]
    assert after["script"]["scenes"][1] == {**before["script"]["scenes"][1], "image_refs": ["refs/logo.png"]}
    assert after["assets"]["provided"] == [{"path": "refs/logo.png", "kind": "product_ref", "origin": "official",
                                            "review_status": "pending", "scene_scope": [1],
                                            "source_url": "https://example.com/press"}]


@pytest.mark.parametrize("flags, message", [
    (["--gate", "editorial-reference", "--choice", "supplied"], "--asset is required"),
    (["--gate", "editorial-reference", "--choice", "supplied", "--asset", "../escape.png"], "inside"),
    (["--gate", "editorial-reference", "--choice", "supplied", "--asset", "refs/absent.png"], "not found"),
    (["--gate", "editorial-reference", "--choice", "official", "--asset", "stills/a.png"], "--source-url is required"),
    (["--gate", "editorial-reference", "--choice", "generated_anchor", "--asset", "stills/a.png"], "--authorized-by is required"),
    (["--gate", "editorial-reference", "--choice", "no_reference"], "--reason is required"),
    (["--gate", "product-reference", "--choice", "generated_anchor", "--asset", "stills/a.png", "--authorized-by", "Ian"], "not allowed"),
    (["--gate", "nonsense", "--choice", "no_reference", "--reason", "r"], "not open"),
    (["--gate", "product-reference", "--choice", "no_reference", "--reason", "r", "--scenes", "0"], "not part of gate"),
])
def test_resolution_refuses_incomplete_or_disallowed_answers(tmp_path, capsys, flags, message):
    folder, brief = _project(tmp_path)
    original = brief.read_text()
    assert cli.main(["reference", "resolve", "--folder", str(folder), "--brief", str(brief), *flags]) == 2
    assert message in capsys.readouterr().err
    assert brief.read_text() == original


def test_authorized_generated_anchor_is_recorded_pending_and_attributed(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    _png(folder / "refs/anchor.png")
    assert cli.main(["reference", "resolve", "--folder", str(folder), "--brief", str(brief), "--gate", "editorial-reference",
                     "--choice", "generated_anchor", "--asset", "refs/anchor.png", "--authorized-by", "Ian, $0.05 cap"]) == 0
    resolved = Brief.from_yaml(brief)
    assert resolved.assets.provided[0].origin == "generated" and resolved.assets.provided[0].review_status == "pending"
    assert resolved.reference_decisions[0].note == "authorized by Ian, $0.05 cap"


def test_compound_direction_is_flagged_unless_a_local_composite_is_selected(tmp_path, capsys):
    folder = tmp_path
    scenes = [
        {"index": 0, "vo_text": "a", "prompt": "Dynamic diptych: the same adult turns off a light, then wakes alert"},
        {"index": 1, "vo_text": "b", "prompt": "Before/after split screen of the routine", "scene_type": "motion_graphic", "clip_path": "g.mp4"},
    ]
    brief = tmp_path / "brief.yaml"
    brief.write_text(yaml.safe_dump({"goal": "g", "script": {"scenes": scenes}}))
    kinds = [(a["scene"], a["kind"]) for a in _storyboard(folder, brief, capsys)["advisories"]]
    assert (0, "compound_direction") in kinds and (1, "compound_direction") not in kinds


def test_unsafe_vertical_text_is_flagged_in_manifest(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    data = yaml.safe_load(brief.read_text())
    data["script"]["scenes"][1]["on_screen_text"] = "THIS LINE IS FAR TOO LONG FOR 9:16"
    brief.write_text(yaml.safe_dump(data))
    advisories = _storyboard(folder, brief, capsys)["advisories"]
    assert any(a["kind"] == "unsafe_vertical_text" and a["scene"] == 1 for a in advisories)


def _resolve(folder, brief, *flags):
    return cli.main(["reference", "resolve", "--folder", str(folder), "--brief", str(brief), *flags])


def test_generated_anchor_records_authorization_then_attaches_the_file(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    assert _resolve(folder, brief, "--gate", "editorial-reference", "--choice", "generated_anchor",
                    "--authorized-by", "Ian, $0.05 cap") == 0
    capsys.readouterr()
    manifest = _storyboard(folder, brief, capsys)
    gate = next(g for g in manifest["gates"] if g["id"] == "editorial-reference")
    assert gate["state"] == "authorized_pending_generation" and gate["authorized_scenes"]["0"] == "authorized by Ian, $0.05 cap"
    choice = next(c for c in gate["choices"] if c["id"] == "generated_anchor")
    assert "--asset <asset>" in choice["command"] and "--authorized-by" not in choice["command"]
    assert "not yet generated" in (Path(manifest["package_dir"]) / "qa-notes.md").read_text()
    assert Brief.from_yaml(brief).script.scenes[0].image_refs == [], "authorization alone attaches nothing"
    assert _resolve(folder, brief, "--gate", "editorial-reference", "--choice", "generated_anchor", "--authorized-by", "x") == 2
    assert "already have an authorized" in capsys.readouterr().err
    _resolve(folder, brief, "--gate", "product-reference", "--choice", "no_reference", "--reason", "placeholder")
    pending = _storyboard(folder, brief, capsys)
    assert pending["status"] == "ready_for_review", "review precedes generation; only produce stays blocked"
    assert [g["state"] for g in pending["gates"]] == ["authorized_pending_generation"]
    assert [s["step"] for s in pending["next_steps"]] == ["human_review", "attach_authorized_anchor", "plan"]
    assert "not yet generated" in (Path(pending["package_dir"]) / "qa-notes.md").read_text()
    assert cli.main(["produce", "--folder", str(folder), "--brief", str(brief)]) == 1

    _png(folder / "refs/anchor.png")
    assert _resolve(folder, brief, "--gate", "editorial-reference", "--choice", "generated_anchor", "--asset", "refs/anchor.png") == 0
    resolved = Brief.from_yaml(brief)
    decision = next(d for d in resolved.reference_decisions if d.choice == "generated_anchor")
    assert decision.asset == "refs/anchor.png" and decision.note == "authorized by Ian, $0.05 cap"
    assert len([d for d in resolved.reference_decisions if d.choice == "generated_anchor"]) == 1
    assert resolved.assets.provided[0].origin == "generated" and resolved.script.scenes[0].image_refs == ["refs/anchor.png"]
    assert _storyboard(folder, brief, capsys)["status"] == "ready_for_review"


def test_pending_anchor_still_blocks_produce_and_attach_needs_prior_authorization(tmp_path, capsys, monkeypatch):
    from parallax import produce as produce_mod
    folder, brief = _project(tmp_path)
    _resolve(folder, brief, "--gate", "editorial-reference", "--choice", "generated_anchor", "--authorized-by", "Ian")
    monkeypatch.setattr(produce_mod, "run_plan", lambda **kw: pytest.fail("produce must not run while an anchor is ungenerated"))
    assert cli.main(["produce", "--folder", str(folder), "--brief", str(brief)]) == 1
    assert "editorial-reference" in capsys.readouterr().err
    other, other_brief = _project(tmp_path / "other")
    _png(other / "refs/a.png")
    assert _resolve(other, other_brief, "--gate", "editorial-reference", "--choice", "generated_anchor", "--asset", "refs/a.png") == 2
    assert "--authorized-by is required" in capsys.readouterr().err


def _scenes_brief(tmp_path, scenes, **top):
    brief = tmp_path / "brief.yaml"
    brief.write_text(yaml.safe_dump({"goal": "g", **top, "script": {"scenes": [
        {"vo_text": "word", "prompt": "plain desk", **s, "index": i} for i, s in enumerate(scenes)]}}))
    return brief


def _gate_scenes(folder, brief, capsys):
    return {g["id"]: g["scenes"] for g in _storyboard(folder, brief, capsys)["gates"]}


def test_detector_mirrors_production_wiring(tmp_path, capsys):
    scenes = [{"shot_type": "character"}, {"shot_type": "broll"}, {"shot_type": "screen"}]
    brief = _scenes_brief(tmp_path, scenes)
    assert _gate_scenes(tmp_path, brief, capsys) == {"editorial-reference": [0, 1, 2]}
    brief = _scenes_brief(tmp_path, scenes, assets={"provided": [{"path": "s.png", "kind": "style_ref"}]})
    _png(tmp_path / "s.png")
    assert _gate_scenes(tmp_path, brief, capsys) == {"editorial-reference": [0, 1, 2]}, "a global style_ref covers nothing"
    brief = _scenes_brief(tmp_path, scenes, assets={"provided": [{"path": "c.png", "kind": "character_ref"}]})
    _png(tmp_path / "c.png")
    assert _gate_scenes(tmp_path, brief, capsys) == {"editorial-reference": [1, 2]}, "character_ref covers shot_type character"
    brief = _scenes_brief(tmp_path, scenes, assets={"provided": [{"path": "p.png", "kind": "product_ref"}]})
    _png(tmp_path / "p.png")
    assert _gate_scenes(tmp_path, brief, capsys) == {"editorial-reference": [0, 2]}, "product_ref covers shot_type broll"


def test_product_gate_reads_prompt_words_not_shot_type_or_brand_names(tmp_path, capsys):
    brief = _scenes_brief(tmp_path, [{"shot_type": "screen", "prompt": "TallyLeaf dashboard"},
                                      {"prompt": "the app home screen"}], assets={"provided": []})
    assert _gate_scenes(tmp_path, brief, capsys)["product-reference"] == [1]


def test_advisories_carry_numeric_threshold_and_machine_readable_remedies(tmp_path, capsys):
    brief = _scenes_brief(tmp_path, [
        {"prompt": "Before/after split screen", "on_screen_text": "Fuel your morning with CrunchO."},
        {"on_screen_text": "Fuel your morning\nwith CrunchO."},
    ])
    advisories = _storyboard(tmp_path, brief, capsys)["advisories"]
    unsafe = [a for a in advisories if a["kind"] == "unsafe_vertical_text"]
    assert [a["scene"] for a in unsafe] == [0]
    assert unsafe[0]["threshold"] == {"max_chars_per_line": 18, "longest_line_chars": 31}
    assert {r["id"] for r in unsafe[0]["remedies"]} == {"wrap", "shorten"}
    compound = next(a for a in advisories if a["kind"] == "compound_direction")
    remedies = {r["id"]: r for r in compound["remedies"]}
    assert set(remedies) == {"representative_frame", "split_scenes", "motion_graphic"}
    assert "exactly" in remedies["split_scenes"]["how"] and remedies["split_scenes"]["narration"] == "distribute_verbatim"


def _pacing(manifest):
    return [a for a in manifest["advisories"] if a["kind"].startswith("opening_")]


def test_pacing_advisories_have_remedies_and_locked_narration_acknowledges_them(tmp_path, capsys):
    scenes = [{"vo_text": "one two three four five six seven eight", "image_refs": ["s.png"]}]
    _png(tmp_path / "s.png")
    manifest = _storyboard(tmp_path, _scenes_brief(tmp_path, scenes), capsys)
    open_ = _pacing(manifest)
    assert open_ and all(w["remedies"][0]["how"] and w["locked_by_narration"] is False for w in open_)
    assert "pacing_warnings" not in manifest and "variety" not in manifest
    locked = _pacing(_storyboard(tmp_path, _scenes_brief(tmp_path, scenes, narration_locked=True), capsys))
    assert [w["kind"] for w in locked] == [w["kind"] for w in open_] and all(w["locked_by_narration"] for w in locked)
    assert "locked_by_narration" in (tmp_path / "storyboard-package/qa-notes.md").read_text()


def test_package_files_carry_no_machine_paths_and_commands_use_placeholders(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    manifest = _storyboard(folder, brief, capsys)
    package = Path(manifest["package_dir"])
    here = {str(tmp_path), str(tmp_path.resolve())}
    for name in ("handoff.json", "qa-notes.md", "storyboard.html", "references.yaml", "scenes.yaml", "script.md"):
        text = (package / name).read_text()
        assert not any(path in text for path in here), f"{name} leaks a machine path"
    command = json.loads((package / "handoff.json").read_text())["gates"][0]["choices"][0]["command"]
    assert "--folder <project-folder> --brief <brief.yaml>" in command
    # stdout keeps exact, runnable commands
    assert str(brief) in manifest["gates"][0]["choices"][0]["command"]


def test_produce_plan_cannot_bypass_an_open_gate(tmp_path, capsys, monkeypatch):
    from parallax import produce as produce_mod
    monkeypatch.setattr(produce_mod, "run_plan", lambda **kw: pytest.fail("spend reached with an open gate"))
    monkeypatch.setattr(produce_mod, "test_scene", lambda **kw: pytest.fail("scene render reached with an open gate"))
    folder, brief = _project(tmp_path)
    assert cli.main(["plan", "--folder", str(folder), "--brief", str(brief)]) == 0
    plan = folder / "parallax/scratch/plan.yaml"
    assert yaml.safe_load(plan.read_text())["source_brief"] == "brief.yaml"
    capsys.readouterr()
    for extra in ([], ["--scene", "0"]):
        assert cli.main(["produce", "--folder", str(folder), "--plan", str(plan), *extra]) == 1
        assert "reference gate(s) block generation" in capsys.readouterr().err
    # the check follows the brief: removing it is an error, not a silent pass
    brief.rename(tmp_path / "moved.yaml")
    assert cli.main(["produce", "--folder", str(folder), "--plan", str(plan)]) == 1
    assert "no longer exists" in capsys.readouterr().err


def test_produce_plan_proceeds_once_gates_are_answered(tmp_path, capsys, monkeypatch):
    from parallax import produce as produce_mod
    from parallax.produce import ProductionResult
    folder, brief = _project(tmp_path)
    for gate in ("editorial-reference", "product-reference"):
        assert _resolve(folder, brief, "--gate", gate, "--choice", "no_reference", "--reason", "test") == 0
    assert cli.main(["plan", "--folder", str(folder), "--brief", str(brief)]) == 0
    called = {}
    monkeypatch.setattr(produce_mod, "run_plan", lambda **kw: called.update(kw) or ProductionResult(
        status="ok", run_id="t", output_dir=tmp_path, final_video=tmp_path / "o.mp4", stills_dir=None, cost_usd=0.0, error=None))
    assert cli.main(["produce", "--folder", str(folder), "--plan", str(folder / "parallax/scratch/plan.yaml")]) == 0
    assert called["plan_path"].endswith("plan.yaml")


def test_produce_refusal_names_the_real_blocker_for_a_pending_anchor(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    _resolve(folder, brief, "--gate", "editorial-reference", "--choice", "generated_anchor", "--authorized-by", "Ian")
    _resolve(folder, brief, "--gate", "product-reference", "--choice", "no_reference", "--reason", "placeholder")
    capsys.readouterr()
    assert cli.main(["produce", "--folder", str(folder), "--brief", str(brief)]) == 1
    err = capsys.readouterr().err
    assert "authorized (authorized by Ian) but not generated or attached" in err
    assert "--choice generated_anchor --asset <asset>" in err
    assert "must answer" not in err


def test_open_gate_refusal_still_asks_for_a_human_answer(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    assert cli.main(["produce", "--folder", str(folder), "--brief", str(brief)]) == 1
    assert "editorial-reference: a human must answer it" in capsys.readouterr().err


def test_pending_anchor_scenes_get_a_pending_note_not_a_contradictory_instruction(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    _resolve(folder, brief, "--gate", "editorial-reference", "--choice", "generated_anchor", "--authorized-by", "Ian")
    manifest = _storyboard(folder, brief, capsys)
    kinds = {(a["scene"], a["kind"]) for a in manifest["advisories"]}
    assert (0, "generated_anchor_pending") in kinds and (0, "unanchored_editorial_scenes") not in kinds
    notes = (Path(manifest["package_dir"]) / "qa-notes.md").read_text()
    scene_0 = [line for line in notes.splitlines() if line.startswith("- Scene 00")]
    assert scene_0 and not any("resolve the reference gate" in line for line in scene_0)


def test_style_ref_only_gate_message_says_what_covers_a_scene(tmp_path, capsys):
    _png(tmp_path / "s.png")
    brief = _scenes_brief(tmp_path, [{}], assets={"provided": [{"path": "s.png", "kind": "style_ref"}]})
    question = _storyboard(tmp_path, brief, capsys)["gates"][0]["question"]
    assert "style_ref never covers a scene by itself" in question and "image_refs" in question and "character_ref covers shot_type: character" in question


def test_check_previews_gates_without_writing_a_package(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    assert cli.main(["storyboard", "--folder", str(folder), "--brief", str(brief), "--check"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["checked_only"] is True and checked["status"] == "needs_reference_decision"
    assert {g["id"] for g in checked["gates"]} == {"editorial-reference", "product-reference"}
    assert not (folder / "storyboard-package").exists()
    assert cli.main(["storyboard", "--folder", str(folder), "--brief", str(tmp_path / "absent.yaml"), "--check"]) == 2


def test_scene_scoped_product_answer_does_not_cover_other_scenes(tmp_path, capsys):
    scenes = [{"shot_type": "broll", "prompt": "person at a desk"}, {"shot_type": "screen", "prompt": "the app on a phone"},
              {"shot_type": "screen", "prompt": "the app dashboard"}, {"shot_type": "broll", "prompt": "walking outside"}]
    brief = _scenes_brief(tmp_path, scenes)
    _png(tmp_path / "refs/app.png")
    capsys.readouterr()
    assert _resolve(tmp_path, brief, "--gate", "product-reference", "--choice", "supplied", "--asset", "refs/app.png", "--scenes", "1,2") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["scenes_newly_covered"] == [1, 2]
    assert result["scenes_still_open"] == {"0": ["editorial-reference"], "3": ["editorial-reference"]}
    assert result["open_gates"] == [{"id": "editorial-reference", "state": "open", "scenes": [0, 3]}]
    resolved = Brief.from_yaml(brief)
    assert resolved.assets.provided[0].scene_scope == [1, 2]
    assert [s.image_refs for s in resolved.script.scenes] == [[], ["refs/app.png"], ["refs/app.png"], []]
    assert _gate_scenes(tmp_path, brief, capsys) == {"editorial-reference": [0, 3]}
    from parallax.planner import plan_from_brief
    plan = yaml.safe_load(plan_from_brief(brief, tmp_path).plan_path.read_text())
    assert "product_image" not in plan, "a scoped answer must not become global production wiring"


def test_hand_authored_global_product_ref_still_covers_broll(tmp_path, capsys):
    _png(tmp_path / "p.png")
    brief = _scenes_brief(tmp_path, [{"shot_type": "broll"}], assets={"provided": [{"path": "p.png", "kind": "product_ref"}]})
    assert _gate_scenes(tmp_path, brief, capsys) == {}


def test_validate_pacing_warnings_carry_remedies(tmp_path, capsys):
    brief = _scenes_brief(tmp_path, [{"vo_text": "one two three four five six seven eight", "image_refs": ["s.png"]}], narration_locked=True)
    assert cli.main(["validate", "--folder", str(tmp_path), "--brief", str(brief)]) in (0, 1)
    warnings = json.loads(capsys.readouterr().out)["warnings"]
    pacing = [w for w in warnings if w["code"].startswith("opening_")]
    assert pacing and all(w["remedies"][0]["how"] and w["locked_by_narration"] is True for w in pacing)
