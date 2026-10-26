"""Tests for editor handoff export (xmeml v4 + FCPXML 1.10 + assets + SRT + README)."""

from __future__ import annotations

import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import typer.testing
import yaml
from PIL import Image

from parallax import cli
from parallax.editor_export import export_editor_package, validate_editor_package


def _make_sine_audio(path: Path, duration_s: float = 3.0, freq: int = 440, sample_rate: int = 44100) -> Path:
    """Create a real audio file of known duration using ffmpeg lavfi sine."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate={sample_rate}:duration={duration_s}",
            str(path),
        ],
        check=True,
    )
    return path


def _make_color_mp4(path: Path, duration_s: float = 1.0, size: str = "360x640", fps: int = 30, color: str = "blue") -> Path:
    """Create a tiny real MP4 clip using ffmpeg lavfi color."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s={size}:r={fps}:d={duration_s}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def _make_pil_image(path: Path, size: tuple[int, int] = (360, 640), color: tuple[int, int, int] = (30, 80, 160)) -> Path:
    """Create a solid-color PNG using Pillow."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


@pytest.fixture()
def mock_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Build a mock project fixture under PARALLAX_TEST_MODE=1 with real media files."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    proj = tmp_path / "demo-project"
    out_v1 = proj / "parallax" / "output" / "v1"
    assets_dir = proj / "parallax" / "assets"
    audio_dir = out_v1 / "audio"
    out_v1.mkdir(parents=True)
    assets_dir.mkdir(parents=True)
    audio_dir.mkdir(parents=True)

    vo_path = _make_sine_audio(audio_dir / "voiceover.wav", duration_s=3.0)
    words_path = audio_dir / "vo_words.json"
    words_payload = {
        "total_duration_s": 3.0,
        "words": [
            {"word": "First", "start": 0.10, "end": 0.45},
            {"word": "scene.", "start": 0.50, "end": 0.90},
            {"word": "Second", "start": 1.05, "end": 1.45},
            {"word": "motion", "start": 1.50, "end": 1.85},
            {"word": "beat.", "start": 1.90, "end": 2.00},
            {"word": "Final", "start": 2.10, "end": 2.50},
            {"word": "call.", "start": 2.55, "end": 2.95},
        ],
    }
    words_path.write_text(json.dumps(words_payload, indent=2), encoding="utf-8")

    still_0 = _make_pil_image(assets_dir / "scene_00_still.png", size=(360, 640), color=(40, 100, 180))
    motion_1 = _make_color_mp4(assets_dir / "scene_01_motion.mp4", duration_s=1.2, size="360x640", fps=30)
    still_2 = _make_pil_image(assets_dir / "scene_02_still.png", size=(360, 640), color=(180, 70, 50))
    ref_mp4 = _make_color_mp4(out_v1 / "demo-project-v1-abc123.mp4", duration_s=3.0, size="360x640", fps=30)

    logo_png = _make_pil_image(proj / "logo.png", size=(120, 120), color=(255, 200, 0))
    music_wav = _make_sine_audio(tmp_path / "bgm.wav", duration_s=5.0, freq=220)
    short_music_wav = _make_sine_audio(tmp_path / "short_bgm.wav", duration_s=1.5, freq=330)

    manifest_data = {
        "version": 1,
        "resolution": "360x640",
        "audio_path": str(vo_path.relative_to(proj)),
        "words_path": str(words_path.relative_to(proj)),
        "scenes": [
            {
                "index": 0,
                "shot_type": "broll",
                "scene_type": "still",
                "vo_text": "First scene.",
                "still_path": str(still_0.relative_to(proj)),
                "start_s": 0.0,
                "end_s": 1.0,
                "duration_s": 1.0,
            },
            {
                "index": 1,
                "shot_type": "screen",
                "scene_type": "motion_graphic",
                "vo_text": "Second motion beat.",
                "clip_path": str(motion_1.relative_to(proj)),
                "start_s": 1.0,
                "end_s": 2.0,
                "duration_s": 1.0,
            },
            {
                "index": 2,
                "shot_type": "broll",
                "scene_type": "still",
                "vo_text": "Final call.",
                "still_path": str(still_2.relative_to(proj)),
                "start_s": 2.0,
                "end_s": 3.0,
                "duration_s": 1.0,
            },
        ],
    }
    manifest_file = out_v1 / "manifest.yaml"
    manifest_file.write_text(yaml.safe_dump(manifest_data, sort_keys=False), encoding="utf-8")

    return {
        "folder": proj,
        "manifest": manifest_file,
        "vo": vo_path,
        "words": words_path,
        "still_0": still_0,
        "motion_1": motion_1,
        "still_2": still_2,
        "ref_mp4": ref_mp4,
        "logo": logo_png,
        "music": music_wav,
        "short_music": short_music_wav,
    }


def test_export_xml_parses_v1_contiguous_and_no_absolute_paths(mock_project: dict[str, Path]) -> None:
    folder = mock_project["folder"]
    summary = export_editor_package(folder, logo=mock_project["logo"])
    out_dir = Path(summary["out_dir"])

    assert out_dir == folder / "editor-handoff"
    assert summary["problems"] == []
    assert validate_editor_package(out_dir) == []

    xml_path = out_dir / "timeline.xml"
    xml_text = xml_path.read_text(encoding="utf-8")
    assert "<!DOCTYPE xmeml>" in xml_text
    # Ensure no absolute paths exist anywhere in timeline.xml
    assert str(folder) not in xml_text
    assert "file://" not in xml_text

    root = ET.fromstring(xml_text)
    assert root.tag == "xmeml"
    assert root.attrib.get("version") == "4"

    seq = root.find("sequence")
    assert seq is not None
    assert seq.findtext("name") == folder.name
    vo_frames = int(seq.findtext("duration") or "0")
    assert vo_frames == 90  # 3.0s @ 30fps
    assert summary["sequence_duration_frames"] == vo_frames

    # Check V1 track contiguity and frame sum
    v1_clips = seq.findall("media/video/track/clipitem")
    assert len(v1_clips) == 3

    starts = [int(c.findtext("start") or "-1") for c in v1_clips]
    ends = [int(c.findtext("end") or "-1") for c in v1_clips]
    assert starts[0] == 0
    for i in range(1, len(v1_clips)):
        assert starts[i] == ends[i - 1], "V1 clipitems must be contiguous"
    assert ends[-1] == vo_frames
    assert sum(e - s for s, e in zip(starts, ends)) == vo_frames

    # Every <pathurl> must be relative and exist on disk under out_dir
    pathurls = [el.text or "" for el in root.iter("pathurl")]
    assert len(pathurls) == 4  # 3 scenes + 1 voiceover (no music)
    for purl in pathurls:
        assert not purl.startswith("/")
        assert (out_dir / purl).is_file(), f"Missing asset for pathurl {purl}"

    # No A2 track when no music is provided
    a_tracks = seq.findall("media/audio/track")
    assert len(a_tracks) == 1

    # Logo and reference video copied
    assert (out_dir / "assets" / "logo.png").is_file()
    assert (out_dir / "reference" / mock_project["ref_mp4"].name).is_file()
    assert (out_dir / "README.txt").is_file()
    assert (out_dir / "handoff.json").is_file()


def test_music_track_gain_filter_and_clamping(mock_project: dict[str, Path]) -> None:
    folder = mock_project["folder"]
    # 1) Music longer than sequence (5.0s > 3.0s) -> clamped to sequence length (90 frames)
    summary = export_editor_package(
        folder,
        music=mock_project["music"],
        music_db=-18.0,
    )
    out_dir = Path(summary["out_dir"])
    root = ET.parse(out_dir / "timeline.xml").getroot()
    a_tracks = root.findall("sequence/media/audio/track")
    assert len(a_tracks) == 2

    a2_clip = a_tracks[1].find("clipitem")
    assert a2_clip is not None
    assert int(a2_clip.findtext("start") or "-1") == 0
    assert int(a2_clip.findtext("end") or "-1") == summary["sequence_duration_frames"]
    assert int(a2_clip.findtext("in") or "-1") == 0
    assert int(a2_clip.findtext("out") or "-1") == summary["sequence_duration_frames"]

    # Check Audio Levels filter linear gain = 10 ** (-18 / 20)
    level_val: float | None = None
    for param in a2_clip.findall("filter/effect/parameter"):
        if param.findtext("parameterid") == "level":
            level_val = float(param.findtext("value") or "0")
    assert level_val is not None
    assert level_val == pytest.approx(10.0 ** (-18.0 / 20.0), rel=1e-4)

    # 2) Music shorter than sequence (1.5s < 3.0s) -> clamped to music length (45 frames) + warning
    summary_short = export_editor_package(
        folder,
        music=mock_project["short_music"],
        music_db=-12.0,
    )
    assert any("shorter than sequence" in w for w in summary_short["warnings"])
    root_short = ET.parse(out_dir / "timeline.xml").getroot()
    a2_short = root_short.findall("sequence/media/audio/track")[1].find("clipitem")
    assert a2_short is not None
    assert int(a2_short.findtext("end") or "-1") == 45
    assert int(a2_short.findtext("out") or "-1") == 45


def test_fcpxml_parses_and_references_existing_relative_files(mock_project: dict[str, Path]) -> None:
    folder = mock_project["folder"]
    summary = export_editor_package(
        folder,
        music=mock_project["music"],
        music_db=-18.0,
        fcpxml=True,
    )
    out_dir = Path(summary["out_dir"])
    fcpxml_path = out_dir / "timeline.fcpxml"
    assert fcpxml_path.is_file()

    text = fcpxml_path.read_text(encoding="utf-8")
    assert "<!DOCTYPE fcpxml>" in text
    assert str(folder) not in text

    root = ET.fromstring(text)
    assert root.tag == "fcpxml"
    assert root.attrib.get("version") == "1.10"

    assets = root.findall("resources/asset")
    assert len(assets) == 5  # 3 scenes + voiceover + music
    for asset in assets:
        src = asset.attrib.get("src", "")
        assert src.startswith("assets/")
        assert (out_dir / src).is_file()

    spine_clips = root.findall("library/event/project/sequence/spine/asset-clip")
    assert len(spine_clips) == 3
    # Voiceover on lane -1 and music on lane -2 attached to first spine clip
    lane_clips = spine_clips[0].findall("asset-clip")
    lanes = {c.attrib.get("lane"): c for c in lane_clips}
    assert "-1" in lanes
    assert "-2" in lanes
    vol = lanes["-2"].find("adjust-volume")
    assert vol is not None and vol.attrib.get("amount") == "-18dB"


def test_srt_generated_and_monotonic(mock_project: dict[str, Path]) -> None:
    folder = mock_project["folder"]
    summary = export_editor_package(folder)
    out_dir = Path(summary["out_dir"])
    srt_path = out_dir / "captions" / "captions.srt"
    assert srt_path.is_file()

    raw_srt = srt_path.read_text(encoding="utf-8").strip()
    blocks = [b.strip() for b in re.split(r"\n\s*\n", raw_srt) if b.strip()]
    assert len(blocks) >= 3

    def _tc_to_ms(tc: str) -> int:
        h, m, rest = tc.split(":")
        s, ms = rest.split(",")
        return ((int(h) * 3600 + int(m) * 60 + int(s)) * 1000) + int(ms)

    prev_end = 0
    for expected_idx, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        assert int(lines[0].strip()) == expected_idx
        start_str, end_str = [p.strip() for p in lines[1].split("-->")]
        start_ms = _tc_to_ms(start_str)
        end_ms = _tc_to_ms(end_str)
        assert start_ms >= prev_end
        assert end_ms > start_ms
        assert lines[2].strip()
        prev_end = end_ms


def test_validate_editor_package_catches_deleted_asset(mock_project: dict[str, Path]) -> None:
    folder = mock_project["folder"]
    summary = export_editor_package(folder)
    out_dir = Path(summary["out_dir"])
    assert validate_editor_package(out_dir) == []

    # Delete one scene asset and verify validate_editor_package reports the problem
    deleted = out_dir / "assets" / "scene_01_motion.mp4"
    deleted.unlink()
    problems = validate_editor_package(out_dir)
    assert problems
    assert any("scene_01_motion.mp4" in p for p in problems)


def test_cli_export_editor_via_main_and_clirunner(mock_project: dict[str, Path], tmp_path: Path) -> None:
    folder = mock_project["folder"]
    custom_out = tmp_path / "cli-handoff-out"

    # 1) Invoke via cli.main
    rc = cli.main([
        "export", "editor",
        "--folder", str(folder),
        "--out", str(custom_out),
        "--music", str(mock_project["music"]),
        "--music-db", "-15",
        "--logo", str(mock_project["logo"]),
        "--json",
    ])
    assert rc == 0
    assert (custom_out / "timeline.xml").is_file()
    assert (custom_out / "assets" / "logo.png").is_file()

    # 2) Invoke via typer.testing.CliRunner
    runner = typer.testing.CliRunner()
    res = runner.invoke(
        cli.app,
        [
            "export", "editor",
            "--folder", str(folder),
            "--no-fcpxml",
            "--stills-as-is",
        ],
    )
    assert res.exit_code == 0, res.output
    default_out = folder / "editor-handoff"
    assert (default_out / "timeline.xml").is_file()
    assert not (default_out / "timeline.fcpxml").exists()
    assert (default_out / "assets" / "scene_00_still.png").is_file()


def test_refuses_out_dir_outside_safe_bounds(mock_project: dict[str, Path], tmp_path: Path) -> None:
    folder = mock_project["folder"]

    # Equal to project folder itself
    with pytest.raises(ValueError, match="equal to project folder"):
        export_editor_package(folder, out_dir=folder)

    # Parent/ancestor of project folder
    with pytest.raises(ValueError, match="outside safe bounds"):
        export_editor_package(folder, out_dir=folder.parent)

    # Root / shallow system dir
    with pytest.raises(ValueError, match="outside safe bounds"):
        export_editor_package(folder, out_dir=Path("/tmp"))

    # Inside project's parallax state dir
    with pytest.raises(ValueError, match="parallax state directory"):
        export_editor_package(folder, out_dir=folder / "parallax" / "output")

    # Non-empty unrelated directory outside project folder
    unrelated = tmp_path / "unrelated-non-empty"
    unrelated.mkdir()
    (unrelated / "important.txt").write_text("do not delete", encoding="utf-8")
    with pytest.raises(ValueError, match="Refusing to overwrite non-empty directory"):
        export_editor_package(folder, out_dir=unrelated)
    assert (unrelated / "important.txt").is_file()


def test_manifest_with_absolute_paths_and_different_fps(mock_project: dict[str, Path], tmp_path: Path) -> None:
    folder = mock_project["folder"]
    manifest_path = mock_project["manifest"]
    manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    # Rewrite manifest to use absolute paths inside project folder + test short user clip clamping
    short_clip = _make_color_mp4(folder / "parallax" / "assets" / "short_user.mp4", duration_s=0.5, size="360x640", fps=24)
    manifest_data["audio_path"] = str(mock_project["vo"].resolve())
    manifest_data["words_path"] = str(mock_project["words"].resolve())
    manifest_data["scenes"][0]["still_path"] = str(mock_project["still_0"].resolve())
    manifest_data["scenes"][1]["clip_path"] = str(short_clip.resolve())
    manifest_data["scenes"][1]["scene_type"] = "animation"
    manifest_data["scenes"][2]["still_path"] = str(mock_project["still_2"].resolve())

    abs_manifest = folder / "abs_manifest.yaml"
    abs_manifest.write_text(yaml.safe_dump(manifest_data, sort_keys=False), encoding="utf-8")

    # Export at 24 fps
    out_24 = tmp_path / "export-24fps"
    summary_24 = export_editor_package(
        folder,
        out_dir=out_24,
        manifest_path=abs_manifest,
        fps=24,
    )
    assert summary_24["fps"] == 24
    assert summary_24["sequence_duration_frames"] == 72  # 3.0s * 24fps
    # Short clip (12 frames) in a 24-frame slot triggers warning and clamps <out>
    assert any("Scene 01" in w and "shorter than scene slot" in w for w in summary_24["warnings"])

    xml_text = (out_24 / "timeline.xml").read_text(encoding="utf-8")
    assert str(folder) not in xml_text
    root = ET.fromstring(xml_text)
    assert root.findtext("sequence/rate/timebase") == "24"
    v1_clips = root.findall("sequence/media/video/track/clipitem")
    # Scene 1 slot is 24..48, next scene starts at 48 (untouched), but Scene 1 <out> is clamped to 12
    assert int(v1_clips[1].findtext("start") or "-1") == 24
    assert int(v1_clips[1].findtext("end") or "-1") == 48
    assert int(v1_clips[1].findtext("out") or "-1") == 12
    assert int(v1_clips[2].findtext("start") or "-1") == 48
    assert int(v1_clips[2].findtext("end") or "-1") == 72


def test_e2e_produce_in_test_mode_then_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run `parallax produce` in PARALLAX_TEST_MODE=1 and export the resulting manifest."""
    from parallax.produce import run_plan

    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))

    proj = tmp_path / "e2e-proj"
    proj.mkdir()
    brand_logo = _make_pil_image(proj / "brand_logo.png", size=(100, 100), color=(10, 200, 100))
    (proj / "brand.yaml").write_text(
        yaml.safe_dump({"name": "E2E Brand", "logo": "brand_logo.png"}),
        encoding="utf-8",
    )

    plan_path = proj / "plan.yaml"
    plan_path.write_text(
        yaml.safe_dump({
            "aspect": "9:16",
            "captions": "skip",
            "words_per_chunk": 3,
            "scenes": [
                {"index": 0, "shot_type": "broll", "vo_text": "Opening hook in test mode.", "prompt": "A bright room."},
                {"index": 1, "shot_type": "broll", "vo_text": "Closing call to action now.", "prompt": "Product close up."},
            ],
        }),
        encoding="utf-8",
    )

    res = run_plan(folder=proj, plan_path=plan_path)
    assert res.status == "ok"

    summary = export_editor_package(proj)
    out_dir = Path(summary["out_dir"])
    assert validate_editor_package(out_dir) == []
    assert (out_dir / "timeline.xml").is_file()
    assert (out_dir / "timeline.fcpxml").is_file()
    assert (out_dir / "captions" / "captions.srt").is_file()
    assert (out_dir / "assets" / "logo.png").is_file()
    assert (out_dir / "reference").is_dir()
