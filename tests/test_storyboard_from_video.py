"""Offline finished-cut storyboard coverage."""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

from parallax.cli import main


def _video(path: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=160x284:d=0.4",
                    "-f", "lavfi", "-i", "color=c=blue:s=160x284:d=0.4",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-pix_fmt", "yuv420p", str(path)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_from_video_extracts_package_frames_and_uses_editorial_pages(tmp_path, capsys):
    video = tmp_path / "cut.mp4"; _video(video)
    scenes = tmp_path / "scenes.yaml"
    scenes.write_text("title: Finished cut\nscenes:\n  - id: red\n    title: Red opening\n  - id: blue\n    title: Blue close\n")
    assert main(["storyboard", "--folder", str(tmp_path), "--from-video", str(video), "--scenes", str(scenes), "--no-pdf", "--json"]) == 0
    result = json.loads(capsys.readouterr().out); package = Path(result["package_dir"])
    html = (package / "storyboard.html").read_text()
    assert html.count('class="scene-page"') == 2 and 'class="cover"' in html
    assert (package / "assets/frames/scene-red.jpg").is_file()
    assert result["source_video"]["duration_s"] > 0 and result["source_video"]["sha256"]


def test_from_video_cut_count_mismatch_explains_remedy(tmp_path, capsys):
    video = tmp_path / "cut.mp4"; _video(video)
    scenes = tmp_path / "scenes.yaml"
    scenes.write_text("title: Finished cut\nscenes:\n  - id: only\n    title: Only scene\n")
    assert main(["storyboard", "--folder", str(tmp_path), "--from-video", str(video), "--scenes", str(scenes), "--no-pdf"]) == 2
    err = capsys.readouterr().err
    assert "detected" in err and "Add explicit start/end" in err


def test_finished_cut_preserves_scene_presentation_and_skips_brief_review(tmp_path, capsys):
    video = tmp_path / "cut.mp4"; _video(video)
    scenes = tmp_path / "scenes.yaml"
    scenes.write_text("""title: Finished cut
version: v9
scenes:
  - id: \"00\"
    title: Opening headline
    treatment: Wide pool reveal
    kicker: OPENING
    start: 0
    end: .4
  - id: 12a
    title: Detail title
    treatment: Barefoot detail
    motion: Slow drift
    start: .4
    end: .8
""")
    assert main(["storyboard", "--folder", str(tmp_path), "--from-video", str(video), "--scenes", str(scenes), "--no-pdf", "--json"]) == 0
    result = json.loads(capsys.readouterr().out); package = Path(result["package_dir"])
    html = (package / "storyboard.html").read_text()
    assert "SCENE 00 · OPENING" in html and "SCENE 12a · Barefoot detail" in html
    assert "<h2>Opening headline</h2>" in html and "<strong>Barefoot detail.</strong> — Slow drift" in html
    assert not result["advisories"] and not result["gates"] and not (package / "brief.yaml").exists()
    assert "parallax plan" not in json.dumps(result) and "rerun_after_review" in json.dumps(result)
    assert re.search(r"<article class=\"scene-page\"", html)
    assert html.count('class="scene-page"') == 2 and html.count('<img ') == 2
    visible = re.sub(r"<[^>]+>", "", html)
    assert not any(token in visible for token in ("http", "www.", "/home", "/workspace", "/Users", "C:\\"))
    rerun = next(step["command"] for step in result["next_steps"] if step["step"] == "rerun_after_review")
    argv = shlex.split(rerun)
    assert argv[argv.index("--from-video") + 1] == str(video.resolve())


def test_explicit_boundaries_snap_to_cuts_and_note_distant_boundaries(tmp_path, capsys):
    video = tmp_path / "cut.mp4"; _video(video)
    scenes = tmp_path / "scenes.yaml"
    scenes.write_text("""title: Finished cut
scenes:
  - id: red
    title: Red
    start: 0
    end: .39
  - id: blue
    title: Blue
    start: .39
    end: .8
""")
    assert main(["storyboard", "--folder", str(tmp_path), "--from-video", str(video), "--scenes", str(scenes), "--no-pdf", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    cut = result["source_video"]["detected_cuts_s"][0]
    package_scenes = yaml.safe_load((Path(result["package_dir"]) / "scenes.yaml").read_text())
    assert package_scenes["scenes"][0]["end"] == pytest.approx(cut)
    assert package_scenes["scenes"][1]["start"] == pytest.approx(cut)

    scenes.write_text("""title: Finished cut
scenes:
  - id: red
    title: Red
    start: 0
    end: .1
  - id: blue
    title: Blue
    start: .1
    end: .8
""")
    assert main(["storyboard", "--folder", str(tmp_path), "--from-video", str(video), "--scenes", str(scenes), "--no-pdf", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert any(note["kind"] == "boundary_no_nearby_cut" and note["requested_s"] == .1
               for note in result["source_video"]["boundary_notes"])
