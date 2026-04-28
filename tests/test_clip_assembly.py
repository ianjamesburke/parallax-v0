"""assemble_clip_video / _make_clip_segment characterization.

Locks in:
  - assemble_clip_video accepts scenes with clip_paths (list of files) and
    duration_s, normalizes each, concatenates, and muxes audio.
  - Resolution auto-detects from the first available video clip.
  - A scene with a single clip shorter than its duration_s is looped
    (stream_loop=-1 then -t) to fill.
  - Mixed image+video clip_paths route through the right normalization
    (Ken Burns for stills, scale+pad for videos).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from parallax import tools_video


def _make_video(path: Path, duration_s: float, color: str = "red",
                w: int = 720, h: int = 1280) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color={color}:s={w}x{h}:d={duration_s}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-r", "30", str(path)],
        check=True, capture_output=True,
    )


def _make_still(path: Path, color: str = "blue", w: int = 720, h: int = 1280) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color={color}:s={w}x{h}",
         "-frames:v", "1", str(path)],
        check=True, capture_output=True,
    )


def _make_silent_wav(path: Path, duration_s: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "anullsrc=cl=mono:r=44100",
         "-t", str(duration_s), str(path)],
        check=True, capture_output=True,
    )


def _probe_format_duration(path: Path) -> float:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    return float(p.stdout.strip())


def _probe_resolution(path: Path) -> tuple[int, int]:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    parts = p.stdout.strip().split(",")
    return int(parts[0]), int(parts[1])


def test_make_clip_segment_loops_short_clip_to_target_duration(tmp_path):
    clip = tmp_path / "src.mp4"
    _make_video(clip, 0.5)
    out = tmp_path / "seg.mp4"
    tools_video._make_clip_segment(
        [str(clip)], duration_s=1.5, output_path=str(out),
        out_w=720, out_h=1280, tmp_dir=str(tmp_path), scene_idx=0,
    )
    assert out.exists()
    dur = _probe_format_duration(out)
    assert abs(dur - 1.5) < 0.2


def test_make_clip_segment_concatenates_multiple_clips(tmp_path):
    c1 = tmp_path / "c1.mp4"
    c2 = tmp_path / "c2.mp4"
    _make_video(c1, 0.5, "red")
    _make_video(c2, 0.5, "green")
    out = tmp_path / "seg.mp4"
    tools_video._make_clip_segment(
        [str(c1), str(c2)], duration_s=2.0, output_path=str(out),
        out_w=720, out_h=1280, tmp_dir=str(tmp_path), scene_idx=0,
    )
    assert out.exists()
    dur = _probe_format_duration(out)
    assert abs(dur - 2.0) < 0.3


def test_make_clip_segment_handles_image_input(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    still = tmp_path / "img.png"
    _make_still(still)
    out = tmp_path / "seg.mp4"
    tools_video._make_clip_segment(
        [str(still)], duration_s=1.0, output_path=str(out),
        out_w=720, out_h=1280, tmp_dir=str(tmp_path), scene_idx=0,
    )
    assert out.exists()


def test_make_clip_segment_no_valid_clips_raises(tmp_path):
    out = tmp_path / "seg.mp4"
    with pytest.raises(RuntimeError, match="no valid clips"):
        tools_video._make_clip_segment(
            [str(tmp_path / "missing.mp4")], duration_s=1.0, output_path=str(out),
            out_w=720, out_h=1280, tmp_dir=str(tmp_path), scene_idx=0,
        )


# ─── assemble_clip_video ────────────────────────────────────────────────


def test_assemble_clip_video_basic(tmp_path):
    c1 = tmp_path / "c1.mp4"
    c2 = tmp_path / "c2.mp4"
    _make_video(c1, 1.0, "red")
    _make_video(c2, 1.0, "green")
    audio = tmp_path / "n.wav"
    _make_silent_wav(audio, 2.0)
    scenes = [
        {"index": 0, "clip_paths": [str(c1)], "duration_s": 1.0},
        {"index": 1, "clip_paths": [str(c2)], "duration_s": 1.0},
    ]
    out = tmp_path / "assembled.mp4"
    result = tools_video.assemble_clip_video(
        json.dumps(scenes), str(audio), str(out),
    )
    assert Path(result) == out
    assert out.exists()
    dur = _probe_format_duration(out)
    assert abs(dur - 2.0) < 0.4


def test_assemble_clip_video_auto_detects_resolution(tmp_path):
    """When resolution is None, output resolution comes from first clip."""
    clip = tmp_path / "c.mp4"
    _make_video(clip, 1.0, "red", w=540, h=960)
    audio = tmp_path / "n.wav"
    _make_silent_wav(audio, 1.0)
    scenes = [{"index": 0, "clip_paths": [str(clip)], "duration_s": 1.0}]
    out = tmp_path / "assembled.mp4"
    tools_video.assemble_clip_video(
        json.dumps(scenes), str(audio), str(out), resolution=None,
    )
    w, h = _probe_resolution(out)
    assert (w, h) == (540, 960)


def test_assemble_clip_video_empty_raises(tmp_path):
    with pytest.raises(ValueError, match="No scenes"):
        tools_video.assemble_clip_video("[]", str(tmp_path / "n.wav"), str(tmp_path / "o.mp4"))
