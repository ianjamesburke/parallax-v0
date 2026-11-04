"""Drive-safe encode tests for `parallax cut` and `parallax video assemble`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import typer.testing
import yaml

from parallax import cli
from parallax.ffmpeg_utils import drive_safe_encode_args, even_dimensions


def _ffprobe_json(mp4_path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(mp4_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def _assert_drive_safe_mp4(mp4_path: Path) -> None:
    assert mp4_path.is_file()
    info = _ffprobe_json(mp4_path)
    streams = info.get("streams") or []
    v_streams = [s for s in streams if s.get("codec_type") == "video"]
    a_streams = [s for s in streams if s.get("codec_type") == "audio"]
    assert len(v_streams) == 1
    assert len(a_streams) == 1

    vs = v_streams[0]
    assert vs.get("codec_name") == "h264"
    assert vs.get("profile") == "High"
    assert int(vs.get("level", 0)) == 40
    assert vs.get("pix_fmt") == "yuv420p"
    assert vs.get("r_frame_rate") == "24/1"
    assert vs.get("avg_frame_rate") == "24/1"
    assert int(vs.get("width", 1)) % 2 == 0
    assert int(vs.get("height", 1)) % 2 == 0

    aus = a_streams[0]
    assert aus.get("codec_name") == "aac"
    assert aus.get("profile") == "LC"
    assert int(aus.get("sample_rate", 0)) == 48000
    assert int(aus.get("channels", 0)) == 2
    a_bitrate = int(aus.get("bit_rate") or 0)
    assert 0 < a_bitrate <= 175_000

    fmt_tags = (info.get("format") or {}).get("tags") or {}
    assert "encoder" not in {k.lower() for k in fmt_tags}

    head = mp4_path.read_bytes()[:65536]
    moov_idx = head.find(b"moov")
    mdat_idx = head.find(b"mdat")
    assert moov_idx != -1, "moov atom not found in first 64KB"
    assert mdat_idx == -1 or moov_idx < mdat_idx, f"moov ({moov_idx}) must appear before mdat ({mdat_idx})"


def _make_clip(path: Path, *, duration_s: float = 2.0, size: str = "270x480", fps: int = 12) -> Path:
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


def _make_mono_vo(path: Path, *, duration_s: float = 4.0) -> Path:
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
            "-ac",
            "1",
            str(path),
        ],
        check=True,
    )
    return path


def test_even_dimensions_and_drive_safe_args_helpers() -> None:
    assert even_dimensions(271, 481) == (270, 480)
    assert even_dimensions(720, 1280) == (720, 1280)
    args = drive_safe_encode_args(fps=24, include_audio=True)
    assert "-profile:v" in args and "high" in args
    assert "-level:v" in args and "4.0" in args
    assert "-movflags" in args and "+faststart" in args


def test_cut_default_drive_safe_encode_and_odd_resolution_rounding(tmp_path: Path) -> None:
    clips_dir = tmp_path / "clips"
    _make_clip(clips_dir / "Scene_01.mp4", duration_s=2.5, size="270x480", fps=12)
    _make_clip(clips_dir / "Scene_02.mp4", duration_s=2.5, size="270x480", fps=12)

    script_path = tmp_path / "script.txt"
    script_path.write_text(
        "Ancient piers rose from the harbour.\nVolcanic ash sealed the stone.",
        encoding="utf-8",
    )

    vo_path = _make_mono_vo(tmp_path / "mono_vo.wav", duration_s=4.0)
    words_path = tmp_path / "words.json"
    words_path.write_text(
        json.dumps(
            {
                "total_duration_s": 4.0,
                "words": [
                    {"word": "Ancient", "start": 0.1, "end": 0.4},
                    {"word": "piers", "start": 0.4, "end": 0.7},
                    {"word": "rose", "start": 0.7, "end": 1.0},
                    {"word": "from", "start": 1.0, "end": 1.2},
                    {"word": "the", "start": 1.2, "end": 1.4},
                    {"word": "harbour.", "start": 1.4, "end": 1.8},
                    {"word": "Volcanic", "start": 2.0, "end": 2.4},
                    {"word": "ash", "start": 2.4, "end": 2.7},
                    {"word": "sealed", "start": 2.7, "end": 3.1},
                    {"word": "the", "start": 3.1, "end": 3.3},
                    {"word": "stone.", "start": 3.3, "end": 3.8},
                ],
            }
        ),
        encoding="utf-8",
    )

    map_path = tmp_path / "map.yaml"
    map_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "segments": [
                    {"anchor": "Ancient piers", "clip": "Scene_01.mp4"},
                    {"anchor": "Volcanic ash", "clip": "Scene_02.mp4"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    out_mp4 = tmp_path / "cut_drive_safe.mp4"
    runner = typer.testing.CliRunner()
    res = runner.invoke(
        cli.app,
        [
            "cut",
            "--no-strict-vo",
            "--script",
            str(script_path),
            "--clips",
            str(clips_dir),
            "--map",
            str(map_path),
            "--audio",
            str(vo_path),
            "--words",
            str(words_path),
            "--resolution",
            "271x481",
            "--out",
            str(out_mp4),
        ],
    )
    assert res.exit_code == 0, res.output
    _assert_drive_safe_mp4(out_mp4)


def test_video_assemble_encode_preset_drive_safe(tmp_path: Path) -> None:
    clip1 = _make_clip(tmp_path / "shot1.mp4", duration_s=1.5, size="270x480", fps=15)
    clip2 = _make_clip(tmp_path / "shot2.mp4", duration_s=1.5, size="270x480", fps=15)
    vo = _make_mono_vo(tmp_path / "vo.wav", duration_s=3.0)
    out_mp4 = tmp_path / "assembled_drive_safe.mp4"

    runner = typer.testing.CliRunner()
    res = runner.invoke(
        cli.app,
        [
            "video",
            "assemble",
            "--shot",
            f"{clip1}:1.5",
            "--shot",
            f"{clip2}:1.5",
            "--voice",
            str(vo),
            "--resolution",
            "271x481",
            "--encode-preset",
            "drive-safe",
            "--out",
            str(out_mp4),
        ],
    )
    assert res.exit_code == 0, res.output
    _assert_drive_safe_mp4(out_mp4)
