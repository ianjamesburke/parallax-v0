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
    if (package / "storyboard.pdf").is_file():
        expected.add("storyboard.pdf")
    assert {f["path"] for f in manifest["files"]} == expected
    assert expected <= {p.name for p in package.iterdir()}
    assert not [p for p in package.rglob("*") if p.name.startswith(".")], "no hidden files or directories"
    assert (package / "assets/stills/a.png").is_file()
    assert manifest["contact_sheet_scenes"] == [0]
    assert (package / "brief.yaml").read_bytes() == brief.read_bytes()
    assert manifest["requires_parallax"] is False and manifest["provider_calls"] == 0 and manifest["approved"] is False
    on_disk = json.loads((package / "handoff.json").read_text())
    assert {k: v for k, v in on_disk.items() if k not in {"gates", "next_steps"}} == {
        k: v for k, v in manifest.items() if k not in {"package_dir", "html", "pdf", "gates", "next_steps"}}
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
    (["--gate", "nonsense", "--choice", "no_reference", "--reason", "r"], "not a known gate"),
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
    declared = {"assets": {"provided": [{"path": "s.png", "kind": "style_ref"}]}}
    manifest = _storyboard(tmp_path, _scenes_brief(tmp_path, scenes, **declared), capsys)
    open_ = _pacing(manifest)
    assert open_ and all(w["remedies"][0]["how"] and w["locked_by_narration"] is False for w in open_)
    assert "pacing_warnings" not in manifest and "variety" not in manifest
    locked = _pacing(_storyboard(tmp_path, _scenes_brief(tmp_path, scenes, narration_locked=True, **declared), capsys))
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
    assert "editorial-reference (scenes 00, 01): a human must answer it" in capsys.readouterr().err


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
    _png(tmp_path / "s.png")
    brief = _scenes_brief(tmp_path, [{"vo_text": "one two three four five six seven eight", "image_refs": ["s.png"]}], narration_locked=True,
                          assets={"provided": [{"path": "s.png", "kind": "style_ref"}]})
    assert cli.main(["validate", "--folder", str(tmp_path), "--brief", str(brief)]) in (0, 1)
    warnings = json.loads(capsys.readouterr().out)["warnings"]
    pacing = [w for w in warnings if w["code"].startswith("opening_")]
    assert pacing and all(w["remedies"][0]["how"] and w["locked_by_narration"] is True for w in pacing)


def test_undeclared_image_ref_fails_fast_in_storyboard_and_validate(tmp_path, capsys):
    _png(tmp_path / "refs/crunch.png")
    brief = _scenes_brief(tmp_path, [{"image_refs": ["refs/crunch.png"]}])
    for argv in (["storyboard", "--folder", str(tmp_path), "--brief", str(brief), "--json"],
                 ["storyboard", "--folder", str(tmp_path), "--brief", str(brief), "--check"]):
        assert cli.main(argv) == 2
        err = capsys.readouterr().err
        assert "'refs/crunch.png' is not declared in assets.provided" in err and "Declare it there with a kind" in err
    assert not (tmp_path / "storyboard-package").exists()
    assert cli.main(["validate", "--folder", str(tmp_path), "--brief", str(brief)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is False and "not declared in assets.provided" in result["errors"][0]["message"]


def test_scoped_asset_exempts_only_its_own_scenes(tmp_path, capsys):
    _png(tmp_path / "p.png")
    _png(tmp_path / "c.png")
    assets = {"provided": [{"path": "c.png", "kind": "character_ref"},
                           {"path": "p.png", "kind": "product_ref", "scene_scope": [2]}]}
    scenes = [{"shot_type": "character", "prompt": "fox"}, {"shot_type": "broll", "prompt": "the product box"},
              {"shot_type": "broll", "prompt": "the product hero", "image_refs": ["p.png"]},
              {"shot_type": "broll", "prompt": "the product again"}]
    brief = _scenes_brief(tmp_path, scenes, assets=assets)
    assert _gate_scenes(tmp_path, brief, capsys)["product-reference"] == [1, 3], "a scoped product_ref exempts only scene 2"
    scenes[2].pop("image_refs")
    brief = _scenes_brief(tmp_path, scenes, assets=assets)
    assert 2 in _gate_scenes(tmp_path, brief, capsys)["product-reference"], "scope alone feeds nothing; the scene must list the file"


def test_gate_explains_why_a_global_reference_does_not_reach_a_scene(tmp_path, capsys):
    _png(tmp_path / "p.png")
    _png(tmp_path / "c.png")
    assets = {"provided": [{"path": "c.png", "kind": "character_ref"}, {"path": "p.png", "kind": "product_ref"}]}
    brief = _scenes_brief(tmp_path, [{"shot_type": "character", "prompt": "fox pours the brand cereal product"}], assets=assets)
    gate = next(g for g in _storyboard(tmp_path, brief, capsys)["gates"] if g["id"] == "product-reference")
    note = gate["scene_notes"]["0"]
    assert "a product_ref exists but scene 0 is shot_type: character" in note
    assert "only reaches shot_type: broll scenes" in note and "Add the file to scene 0 image_refs or change its shot_type" in note
    assert note in gate["question"]


def _graphic_brief(tmp_path, **scene):
    return _scenes_brief(tmp_path, [{"scene_type": "motion_graphic", "prompt": "timeline graphic", **scene}])


def test_missing_motion_graphic_clip_is_an_advisory_with_a_plain_remedy(tmp_path, capsys):
    brief = _graphic_brief(tmp_path, clip_path="clips/collage.mp4")
    manifest = _storyboard(tmp_path, brief, capsys)
    assert manifest["status"] == "ready_for_review", "non-blocking"
    advisory = next(a for a in manifest["advisories"] if a["kind"] == "motion_graphic_clip_missing")
    assert advisory["scene"] == 0 and "clips/collage.mp4" in advisory["message"]
    how = advisory["remedies"][0]["how"]
    assert "does not author" in how and "enforces no clip contract" in how and "not validated" in how
    assert "remotion-best-practices" in how and "create-remotion-geist" in how
    (tmp_path / "clips").mkdir()
    (tmp_path / "clips/collage.mp4").write_bytes(b"x")
    assert not [a for a in _storyboard(tmp_path, brief, capsys)["advisories"] if a["kind"] == "motion_graphic_clip_missing"]
    unset = _storyboard(tmp_path, _graphic_brief(tmp_path), capsys)
    assert any("no clip_path is set" in a["message"] for a in unset["advisories"])


def test_produce_refuses_motion_graphic_scenes_without_a_clip(tmp_path, capsys, monkeypatch):
    from parallax import produce as produce_mod
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setattr(produce_mod, "run_plan", lambda **kw: pytest.fail("run_plan reached without the clip"))
    missing = _graphic_brief(tmp_path, clip_path="clips/collage.mp4")
    assert cli.main(["produce", "--folder", str(tmp_path), "--brief", str(missing)]) == 1
    assert "required asset(s) missing" in capsys.readouterr().err
    unset = _graphic_brief(tmp_path)
    assert cli.main(["produce", "--folder", str(tmp_path), "--brief", str(unset)]) == 1
    assert "motion_graphic needs a locally rendered clip_path" in capsys.readouterr().err
    monkeypatch.undo()
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    plan = tmp_path / "plan.yaml"
    plan.write_text(yaml.safe_dump({"scenes": [{"index": 0, "scene_type": "motion_graphic", "clip_path": "clips/collage.mp4", "duration_s": 1.0}]}))
    assert cli.main(["produce", "--folder", str(tmp_path), "--plan", str(plan)]) == 1
    assert "clip_path is missing" in capsys.readouterr().err


def test_guide_states_that_a_direction_only_beat_cannot_be_its_own_scene():
    import typer
    from parallax.guide import build_guide
    mapping = build_guide(typer.main.get_group(cli.app))["brief"]["script_mapping"]["direction_only_beats"]
    assert "cannot be its own scene" in mapping and "0.05s" in mapping


def _two_scene_brief(tmp_path):
    return _scenes_brief(tmp_path, [{"prompt": "desk"}, {"prompt": "street"}])


def _answer(tmp_path, brief, scenes, choice, *flags, capsys):
    capsys.readouterr()
    assert _resolve(tmp_path, brief, "--gate", "editorial-reference", "--choice", choice, "--scenes", scenes, *flags) == 0
    return json.loads(capsys.readouterr().out)


def _decisions(brief):
    return [(d.choice, d.scenes, d.asset) for d in Brief.from_yaml(brief).reference_decisions]


def test_changing_an_answer_replaces_only_the_named_scenes(tmp_path, capsys):
    brief = _two_scene_brief(tmp_path)
    _answer(tmp_path, brief, "0,1", "no_reference", "--reason", "b-roll", capsys=capsys)
    assert _gate_scenes(tmp_path, brief, capsys) == {}
    result = _answer(tmp_path, brief, "1", "generated_anchor", "--authorized-by", "Ian", capsys=capsys)
    assert result["replaced"] == [{"choice": "no_reference", "scenes": [1]}]
    assert _decisions(brief) == [("no_reference", [0], None), ("generated_anchor", [1], None)]
    manifest = _storyboard(tmp_path, brief, capsys)
    assert [(g["id"], g["state"], g["scenes"]) for g in manifest["gates"]] == [("editorial-reference", "authorized_pending_generation", [1])]
    refs = yaml.safe_load((Path(manifest["package_dir"]) / "references.yaml").read_text())
    assert [(d["choice"], d["scenes"]) for d in refs["decisions"]] == [("no_reference", [0]), ("generated_anchor", [1])]
    assert manifest["produce_blocked"] is True


def test_changing_a_supplied_asset_detaches_the_old_one_without_leftovers(tmp_path, capsys):
    brief = _two_scene_brief(tmp_path)
    _png(tmp_path / "refs/a.png")
    _png(tmp_path / "refs/b.png")
    _answer(tmp_path, brief, "0,1", "supplied", "--asset", "refs/a.png", capsys=capsys)
    result = _answer(tmp_path, brief, "1", "supplied", "--asset", "refs/b.png", capsys=capsys)
    assert result["replaced"] == [{"choice": "supplied", "scenes": [1], "asset": "refs/a.png"}]
    resolved = Brief.from_yaml(brief)
    assert [s.image_refs for s in resolved.script.scenes] == [["refs/a.png"], ["refs/b.png"]]
    assert {a.path: a.scene_scope for a in resolved.assets.provided} == {"refs/a.png": [0], "refs/b.png": [1]}
    assert _decisions(brief) == [("supplied", [0], "refs/a.png"), ("supplied", [1], "refs/b.png")]
    _answer(tmp_path, brief, "0", "no_reference", "--reason", "changed my mind", capsys=capsys)
    resolved = Brief.from_yaml(brief)
    assert [a.path for a in resolved.assets.provided] == ["refs/b.png"], "an asset no scene uses any more is dropped"
    assert resolved.script.scenes[0].image_refs == []
    assert _decisions(brief) == [("supplied", [1], "refs/b.png"), ("no_reference", [0], None)]


def test_replacing_everything_removes_empty_decisions_and_stray_scenes_still_error(tmp_path, capsys):
    brief = _two_scene_brief(tmp_path)
    _answer(tmp_path, brief, "0,1", "no_reference", "--reason", "x", capsys=capsys)
    _answer(tmp_path, brief, "0,1", "generated_anchor", "--authorized-by", "Ian", capsys=capsys)
    assert _decisions(brief) == [("generated_anchor", [0, 1], None)]
    capsys.readouterr()
    assert _resolve(tmp_path, brief, "--gate", "product-reference", "--choice", "no_reference", "--reason", "x", "--scenes", "0") == 2
    assert "not part of gate" in capsys.readouterr().err
    assert _resolve(tmp_path, brief, "--gate", "product-reference", "--choice", "no_reference", "--reason", "x") == 2
    err = capsys.readouterr().err
    assert "no open scenes" in err and "--scenes" in err


def test_resolve_output_reports_what_it_wrote_and_what_awaits_approval(tmp_path, capsys):
    brief = _two_scene_brief(tmp_path)
    _png(tmp_path / "refs/a.png")
    result = _answer(tmp_path, brief, "0,1", "supplied", "--asset", "refs/a.png", capsys=capsys)
    assert result["wrote"] == [str(brief)] and result["replaced"] == []
    assert result["pending_human_approval"][0]["asset"] == "refs/a.png" and "never approves" in result["pending_human_approval"][0]["note"]
    none = _answer(tmp_path, brief, "0", "no_reference", "--reason", "x", capsys=capsys)
    assert none["pending_human_approval"] == []


def test_manifest_states_whether_produce_is_blocked_and_by_what(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    manifest = _storyboard(folder, brief, capsys)
    assert manifest["produce_blocked"] is True
    assert {b["id"] for b in manifest["blocked_by"] if b["kind"] == "reference_gate"} == {"editorial-reference", "product-reference"}
    on_disk = json.loads((Path(manifest["package_dir"]) / "handoff.json").read_text())
    assert on_disk["produce_blocked"] is True and on_disk["blocked_by"] == manifest["blocked_by"]
    for gate in ("editorial-reference", "product-reference"):
        _resolve(folder, brief, "--gate", gate, "--choice", "no_reference", "--reason", "x")
    clear = _storyboard(folder, brief, capsys)
    assert clear["produce_blocked"] is False and clear["blocked_by"] == [] and clear["status"] == "ready_for_review"
    graphic = _storyboard(tmp_path, _graphic_brief(tmp_path, clip_path="clips/x.mp4"), capsys)
    assert graphic["blocked_by"][0]["kind"] == "motion_graphic_clip_missing" and graphic["status"] == "ready_for_review"


def test_validate_reports_each_pacing_warning_once(tmp_path, capsys):
    brief = _scenes_brief(tmp_path, [{"vo_text": "one two three four five six seven eight"}])
    cli.main(["validate", "--folder", str(tmp_path), "--brief", str(brief)])
    result = json.loads(capsys.readouterr().out)
    assert any(w["code"] == "opening_long_hold" for w in result["warnings"])
    assert "warnings" not in result["opening_pacing"] and result["opening_pacing"]["cuts_before_6s"] == 0


def test_answered_reference_reaches_the_plan_and_is_not_called_ignored(tmp_path, capsys):
    from parallax.planner import plan_from_brief
    brief = _scenes_brief(tmp_path, [{"prompt": "desk"}, {"prompt": "street"}],
                          assets={"provided": [{"path": "unused.png", "kind": "style_ref"}]})
    _png(tmp_path / "unused.png")
    _png(tmp_path / "refs/crunch.png")
    _resolve(tmp_path, brief, "--gate", "editorial-reference", "--choice", "supplied", "--asset", "refs/crunch.png", "--scenes", "1")
    _resolve(tmp_path, brief, "--gate", "editorial-reference", "--choice", "no_reference", "--reason", "x", "--scenes", "0")
    capsys.readouterr()
    plan = yaml.safe_load(plan_from_brief(brief, tmp_path).plan_path.read_text())
    out = capsys.readouterr().out
    assert Brief.from_yaml(brief).assets.provided[1].kind == "style_ref"
    assert plan["scenes"][1]["reference_images"] == ["refs/crunch.png"] and "reference_images" not in plan["scenes"][0]
    assert "refs/crunch.png" not in out, "a file consumed through image_refs is not ignored"
    assert "'unused.png' provided but not wired into plan" in out, "genuinely unused assets still warn"


def test_prior_scene_still_path_is_allowed_in_continuity_image_refs(tmp_path, capsys):
    from parallax.reference_gates import undeclared_image_refs
    _png(tmp_path / "parallax/assets/scene_00_still.png")
    brief = _scenes_brief(tmp_path, [
        {"prompt": "first shot", "still_path": "parallax/assets/scene_00_still.png"},
        {"prompt": "same setting continuity shot", "image_refs": ["parallax/assets/scene_00_still.png"]},
    ])
    assert undeclared_image_refs(Brief.from_yaml(brief)) == []
    assert cli.main(["validate", "--folder", str(tmp_path), "--brief", str(brief)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    manifest = _storyboard(tmp_path, brief, capsys)
    assert manifest["missing_assets"] == []


def test_real_run_syncs_locked_still_and_clip_into_brief_and_refreshes_storyboard_package(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from parallax import openrouter
    from parallax.planner import plan_from_brief
    from parallax.produce import run_plan
    from parallax.settings import ProductionMode
    from parallax.stages import _lock_field_in_plan

    monkeypatch.delenv("PARALLAX_TEST_MODE", raising=False)
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))

    brief_path = tmp_path / "brief.yaml"
    brief_path.write_text(
        "# Preserve this comment in brief.yaml\n"
        "goal: Sync stills into storyboard\n"
        "aspect: '9:16'\n"
        "reference_decisions:\n"
        "  - gate: editorial-reference\n"
        "    choice: no_reference\n"
        "    scenes: [0]\n"
        "    note: intentional\n"
        "script:\n"
        "  scenes:\n"
        "    - index: 0\n"
        "      shot_type: broll\n"
        "      scene_type: still\n"
        "      vo_text: One two.\n"
        "      prompt: Morning mug on a wooden desk\n",
        encoding="utf-8",
    )

    plan_res = plan_from_brief(brief_path, tmp_path, stills_only=True)
    assert plan_res.ok and plan_res.plan_path is not None

    def fake_generate_image(*, out_dir, **kwargs):
        out = Path(out_dir) / "raw_00.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (720, 1280), "#224466").save(out)
        return str(out)

    monkeypatch.setattr(openrouter, "check_credits", lambda **kw: SimpleNamespace(remaining=10.0, total=10.0, used=0.0))
    monkeypatch.setattr(openrouter, "generate_image", fake_generate_image)

    result = run_plan(tmp_path, plan_res.plan_path, mode=ProductionMode.REAL)
    assert result.status == "ok", result.error

    brief_text = brief_path.read_text(encoding="utf-8")
    assert "# Preserve this comment in brief.yaml" in brief_text
    reloaded = Brief.from_yaml(brief_path)
    assert reloaded.script.scenes[0].still_path == "parallax/assets/scene_00_still.png"

    # Also verify _lock_field_in_plan syncs clip_path back to brief.yaml
    clip_file = tmp_path / "parallax/assets/scene_00_animated.mp4"
    clip_file.write_bytes(b"clip-bytes")
    plan_dict = yaml.safe_load(plan_res.plan_path.read_text(encoding="utf-8"))
    _lock_field_in_plan(plan_res.plan_path, plan_dict, 0, "clip_path", str(clip_file), tmp_path)
    assert Brief.from_yaml(brief_path).script.scenes[0].clip_path == "parallax/assets/scene_00_animated.mp4"

    pkg = tmp_path / "storyboard-package"
    assert (pkg / "storyboard.html").is_file()
    assert (pkg / "contact-sheet.jpg").is_file()
    assert (pkg / "assets/parallax/assets/scene_00_still.png").is_file()
    assert "Supplied still" in (pkg / "storyboard.html").read_text(encoding="utf-8")


def test_render_pdf_handles_missing_renderer_and_missing_file(tmp_path):
    from parallax.storyboard_package import render_pdf
    html = tmp_path / "test.html"
    pdf = tmp_path / "test.pdf"
    assert render_pdf(html, pdf, renderer=None) is False
    html.write_text("<h1>Hi</h1>", encoding="utf-8")
    assert render_pdf(html, pdf, renderer=["nonexistent-binary-xyz", "{input}", "{output}"]) is False


def test_write_package_with_no_pdf_skips_pdf_generation(tmp_path, capsys):
    folder, brief = _project(tmp_path)
    custom_pkg = tmp_path / "no-pdf-pkg"
    manifest = cli.main(["storyboard", "--folder", str(folder), "--brief", str(brief),
                         "--package-dir", str(custom_pkg), "--no-pdf", "--json"])
    assert manifest == 0
    data = json.loads(capsys.readouterr().out)
    assert not (custom_pkg / "storyboard.pdf").exists()
    assert "storyboard.pdf" not in {f["path"] for f in data["files"]}
    assert "pdf" not in data


def test_pdf_without_renderer_keeps_html_and_prints_install_hint(tmp_path, capsys, monkeypatch):
    import parallax.storyboard_package as sbp
    folder, brief = _project(tmp_path)
    monkeypatch.setattr(sbp, "find_pdf_renderer", lambda: None)
    assert cli.main(["storyboard", "--folder", str(folder), "--brief", str(brief), "--pdf"]) == 2
    assert (folder / "storyboard-package/storyboard.html").is_file()
    assert "playwright install chromium" in capsys.readouterr().err


def test_write_package_registers_pdf_when_created(tmp_path, monkeypatch):
    import parallax.storyboard_package as sbp
    folder, brief = _project(tmp_path)
    custom_pkg = tmp_path / "pdf-pkg"

    def mock_render(html, pdf, **kwargs):
        Path(pdf).write_bytes(b"%PDF-1.4 mock")
        return True

    monkeypatch.setattr(sbp, "render_pdf", mock_render)
    res = sbp.write_package(brief, folder, custom_pkg, pdf=True)
    assert (custom_pkg / "storyboard.pdf").is_file()
    assert "storyboard.pdf" in {f["path"] for f in res["files"]}
    disk_manifest = json.loads((custom_pkg / "handoff.json").read_text(encoding="utf-8"))
    assert "storyboard.pdf" in {f["path"] for f in disk_manifest["files"]}
