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
    assert json.loads((package / "handoff.json").read_text()) == {k: v for k, v in manifest.items() if k != "package_dir"}
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
    assert generated["first_run"].startswith("parallax image generate") and "explicitly authorizes" in generated["human_decision"]


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
                                            "review_status": "pending", "source_url": "https://example.com/press"}]


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
