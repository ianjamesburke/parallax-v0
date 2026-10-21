"""Offline finished-cut storyboard coverage."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

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
