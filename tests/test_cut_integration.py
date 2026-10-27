"""End-to-end integration tests for `parallax cut` using synthetic ffmpeg fixtures."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import typer.testing
import yaml

from parallax import cli
from parallax.cut import check_manifest_xml_consistency
from parallax.ffmpeg_utils import probe_audio_duration, probe_duration


def _make_test_clip(path: Path, *, duration_s: float = 2.5, size: str = "270x480", fps: int = 12) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate={fps}:duration={duration_s}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def _make_sine_vo(path: Path, *, duration_s: float = 6.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=330:sample_rate=44100:duration={duration_s}",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture()
def synthetic_cut_fixture(tmp_path: Path) -> dict[str, Path]:
    clips_dir = tmp_path / "clips"
    clips_dir.mkdir()
    _make_test_clip(clips_dir / "Scene_01.mp4", duration_s=3.0, size="270x480", fps=12)
    _make_test_clip(clips_dir / "Scene_02.mp4", duration_s=3.0, size="270x480", fps=12)
    _make_test_clip(clips_dir / "Scene_03.mp4", duration_s=3.0, size="270x480", fps=12)
    _make_test_clip(clips_dir / "Scene_04.mp4", duration_s=3.0, size="270x480", fps=12)

    script_path = tmp_path / "script.txt"
    script_path.write_text(
        "45 AD. Harbour walls rose from the bay.\n"
        "Desperate crews poured volcanic ash into the waves.\n"
        "Seawater locked hydroxyapatite—the mineral—into stone.\n"
        "Two millennia later, those piers still stand.",
        encoding="utf-8",
    )

    vo_path = _make_sine_vo(tmp_path / "vo.wav", duration_s=6.0)
    words_path = tmp_path / "words.json"
    words_payload = {
        "total_duration_s": 6.0,
        "words": [
            {"word": "45", "start": 0.05, "end": 0.30},
            {"word": "AD.", "start": 0.30, "end": 0.55},
            {"word": "Harbour", "start": 0.60, "end": 0.85},
            {"word": "walls", "start": 0.85, "end": 1.05},
            {"word": "rose", "start": 1.05, "end": 1.20},
            {"word": "from", "start": 1.20, "end": 1.30},
            {"word": "the", "start": 1.30, "end": 1.38},
            {"word": "bay.", "start": 1.38, "end": 1.50},
            {"word": "Desperate", "start": 1.70, "end": 2.00},
            {"word": "crews", "start": 2.00, "end": 2.20},
            {"word": "poured", "start": 2.20, "end": 2.40},
            {"word": "volcanic", "start": 2.40, "end": 2.65},
            {"word": "ash", "start": 2.65, "end": 2.80},
            {"word": "into", "start": 2.80, "end": 2.90},
            {"word": "the", "start": 2.90, "end": 2.98},
            {"word": "waves.", "start": 2.98, "end": 3.10},
            {"word": "Seawater", "start": 3.30, "end": 3.60},
            {"word": "locked", "start": 3.60, "end": 3.85},
            {"word": "hydroxyapatite", "start": 3.85, "end": 4.20},
            {"word": "the", "start": 4.20, "end": 4.30},
            {"word": "mineral", "start": 4.30, "end": 4.50},
            {"word": "into", "start": 4.50, "end": 4.62},
            {"word": "stone.", "start": 4.62, "end": 4.75},
            {"word": "Two", "start": 4.95, "end": 5.10},
            {"word": "millennia", "start": 5.10, "end": 5.35},
            {"word": "later,", "start": 5.35, "end": 5.50},
            {"word": "those", "start": 5.50, "end": 5.65},
            {"word": "piers", "start": 5.65, "end": 5.80},
            {"word": "still", "start": 5.80, "end": 5.90},
            {"word": "stand.", "start": 5.90, "end": 5.98},
        ],
    }
    words_path.write_text(json.dumps(words_payload, indent=2), encoding="utf-8")

    map_path = tmp_path / "cut-map.yaml"
    map_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "segments": [
                    {"anchor": "45 AD.", "clip": "Scene_01.mp4", "in": 0.0, "note": "Opening"},
                    {"anchor": "Desperate crews", "clip": "Scene_02"},
                    {"anchor": "Seawater locked", "clip": "Scene_03.mp4"},
                    {"anchor": "Two millennia later,", "clip": "Scene_04"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    return {
        "tmp": tmp_path,
        "clips": clips_dir,
        "script": script_path,
        "vo": vo_path,
        "words": words_path,
        "map": map_path,
    }


def test_cut_plan_only_renders_nothing_and_calls_no_tts(
    synthetic_cut_fixture: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import parallax.voiceover as vo_mod

    monkeypatch.setattr(
        vo_mod,
        "generate_voiceover_dict",
        lambda **kw: (_ for _ in ()).throw(AssertionError("TTS must never be called in --plan-only")),
    )

    fx = synthetic_cut_fixture
    out_mp4 = fx["tmp"] / "plan_only_out.mp4"
    proj_dir = fx["tmp"] / "plan_only.cut"

    runner = typer.testing.CliRunner()
    res = runner.invoke(
        cli.app,
        [
            "cut",
            "--script",
            str(fx["script"]),
            "--clips",
            str(fx["clips"]),
            "--map",
            str(fx["map"]),
            "--project",
            str(proj_dir),
            "--out",
            str(out_mp4),
            "--fps",
            "12",
            "--plan-only",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["status"] == "plan_only"
    assert data["timing_estimated"] is True
    assert data["segments_count"] == 4
    assert not out_mp4.exists()
    assert (proj_dir / "plan.json").is_file()
    assert (proj_dir / "manifest.yaml").is_file()


def test_cut_mapping_needed_exits_2_and_writes_template(
    synthetic_cut_fixture: dict[str, Path],
) -> None:
    fx = synthetic_cut_fixture
    # Write a 2-sentence script with 4 clips and no --map -> ambiguous, must exit 2
    short_script = fx["tmp"] / "two_sentences.txt"
    short_script.write_text("First sentence only. Second sentence only.", encoding="utf-8")
    out_mp4 = fx["tmp"] / "unmapped.mp4"
    proj_dir = fx["tmp"] / "unmapped.cut"

    runner = typer.testing.CliRunner()
    res = runner.invoke(
        cli.app,
        [
            "cut",
            "--script",
            str(short_script),
            "--clips",
            str(fx["clips"]),
            "--project",
            str(proj_dir),
            "--out",
            str(out_mp4),
        ],
    )
    assert res.exit_code == 2
    assert "MAPPING NEEDED" in res.output
    assert (proj_dir / "cut-map.template.yaml").is_file()


def test_cut_overrun_exits_2_without_allow_hold(
    synthetic_cut_fixture: dict[str, Path],
) -> None:
    fx = synthetic_cut_fixture
    runner = typer.testing.CliRunner()
    res_fail = runner.invoke(
        cli.app,
        [
            "cut",
            "--script",
            str(fx["script"]),
            "--clips",
            str(fx["clips"]),
            "--map",
            str(fx["map"]),
            "--audio",
            str(fx["vo"]),
            "--words",
            str(fx["words"]),
            "--max-clip-s",
            "1.0",
            "--plan-only",
            "--out",
            str(fx["tmp"] / "overrun.mp4"),
        ],
    )
    assert res_fail.exit_code == 2
    assert "SLOT OVERRUN DETECTED" in res_fail.output

    res_hold = runner.invoke(
        cli.app,
        [
            "cut",
            "--script",
            str(fx["script"]),
            "--clips",
            str(fx["clips"]),
            "--map",
            str(fx["map"]),
            "--audio",
            str(fx["vo"]),
            "--words",
            str(fx["words"]),
            "--max-clip-s",
            "1.0",
            "--allow-hold",
            "--plan-only",
            "--out",
            str(fx["tmp"] / "hold.mp4"),
        ],
    )
    assert res_hold.exit_code == 0, res_hold.output


def test_cut_full_cli_run_and_from_manifest_round_trip(
    synthetic_cut_fixture: dict[str, Path],
) -> None:
    fx = synthetic_cut_fixture
    out_mp4 = fx["tmp"] / "final.mp4"
    proj_dir = fx["tmp"] / "final.cut"

    runner = typer.testing.CliRunner()
    res = runner.invoke(
        cli.app,
        [
            "cut",
            "--script",
            str(fx["script"]),
            "--clips",
            str(fx["clips"]),
            "--map",
            str(fx["map"]),
            "--audio",
            str(fx["vo"]),
            "--words",
            str(fx["words"]),
            "--project",
            str(proj_dir),
            "--out",
            str(out_mp4),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "consistency: manifest==xml ok" in res.output
    assert out_mp4.is_file()

    # Video duration must match voiceover within 1 frame (1/24s = 0.0417s)
    vid_dur = probe_duration(out_mp4)
    vo_dur = probe_audio_duration(fx["vo"])
    assert vid_dur is not None and vo_dur is not None
    assert abs(vid_dur - vo_dur) <= (1.0 / 24.0) + 0.05

    manifest_path = proj_dir / "manifest.yaml"
    xml_path = proj_dir / "assets" / "timeline.xml"
    assert manifest_path.is_file()
    assert xml_path.is_file()
    assert (proj_dir / "assets" / "captions" / "captions.srt").is_file()
    assert (proj_dir / "captions" / "captions.srt").is_file()

    ok, probs = check_manifest_xml_consistency(manifest_path, xml_path)
    assert ok is True, probs

    # Now hand-edit manifest.yaml (adjust scene 0..1 boundary from its planned value to 1.50s)
    # and run `--from-manifest` to verify re-render + re-export keeps MP4 and XML in sync
    manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest_data["scenes"][0]["end_s"] = 1.5
    manifest_data["scenes"][0]["duration_s"] = 1.5
    manifest_data["scenes"][1]["start_s"] = 1.5
    manifest_data["scenes"][1]["duration_s"] = round(float(manifest_data["scenes"][1]["end_s"]) - 1.5, 4)
    manifest_path.write_text(yaml.safe_dump(manifest_data, sort_keys=False), encoding="utf-8")

    out_re = fx["tmp"] / "rerendered.mp4"
    rc = cli.main(
        [
            "cut",
            "--from-manifest",
            str(manifest_path),
            "--out",
            str(out_re),
            "--json",
        ]
    )
    assert rc == 0
    assert out_re.is_file()
    ok2, probs2 = check_manifest_xml_consistency(manifest_path, xml_path)
    assert ok2 is True, probs2
