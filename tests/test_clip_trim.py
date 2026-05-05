"""Tests for clip_trim_start_s / clip_trim_end_s scene fields.

- plan.py accepts both fields
- _resolve_auto_trim resolves "auto" correctly
- _resolve_auto_trim raises on first-scene auto and mismatched clip_path
- ken_burns_assemble with clip_trim_start_s seeks into the source clip
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from parallax import assembly
from parallax.plan import Plan


# ─── plan schema ─────────────────────────────────────────────────────────────

def _minimal_plan_yaml(extra_scene_fields: str = "") -> str:
    return f"""
aspect: "9:16"
scenes:
  - index: 0
    vo_text: "hello"
    clip_path: "footage.mp4"
    {extra_scene_fields}
"""

def test_plan_accepts_clip_trim_start_s(tmp_path):
    p = tmp_path / "plan.yaml"
    p.write_text(_minimal_plan_yaml("clip_trim_start_s: 2.5"))
    plan = Plan.from_yaml(p)
    assert plan.scenes[0].clip_trim_start_s == 2.5

def test_plan_accepts_clip_trim_end_s(tmp_path):
    p = tmp_path / "plan.yaml"
    p.write_text(_minimal_plan_yaml("clip_trim_end_s: 5.0"))
    plan = Plan.from_yaml(p)
    assert plan.scenes[0].clip_trim_end_s == 5.0

def test_plan_accepts_clip_trim_start_s_auto(tmp_path):
    p = tmp_path / "plan.yaml"
    p.write_text(_minimal_plan_yaml("clip_trim_start_s: auto"))
    plan = Plan.from_yaml(p)
    assert plan.scenes[0].clip_trim_start_s == "auto"


# ─── _resolve_auto_trim ───────────────────────────────────────────────────────

def test_resolve_auto_trim_resolves_continuation():
    scenes = [
        {"index": 0, "clip_path": "a.mp4", "clip_trim_start_s": 1.0, "duration_s": 3.0},
        {"index": 1, "clip_path": "a.mp4", "clip_trim_start_s": "auto", "duration_s": 2.0},
    ]
    resolved = assembly._resolve_auto_trim(scenes)
    assert resolved[1]["clip_trim_start_s"] == pytest.approx(4.0)

def test_resolve_auto_trim_no_explicit_start_defaults_to_zero():
    scenes = [
        {"index": 0, "clip_path": "a.mp4", "duration_s": 3.0},  # no clip_trim_start_s
        {"index": 1, "clip_path": "a.mp4", "clip_trim_start_s": "auto", "duration_s": 2.0},
    ]
    resolved = assembly._resolve_auto_trim(scenes)
    assert resolved[1]["clip_trim_start_s"] == pytest.approx(3.0)

def test_resolve_auto_trim_raises_on_first_scene():
    scenes = [{"index": 0, "clip_path": "a.mp4", "clip_trim_start_s": "auto", "duration_s": 2.0}]
    with pytest.raises(RuntimeError, match="first scene"):
        assembly._resolve_auto_trim(scenes)

def test_resolve_auto_trim_raises_on_mismatched_clip_path():
    scenes = [
        {"index": 0, "clip_path": "a.mp4", "duration_s": 3.0},
        {"index": 1, "clip_path": "b.mp4", "clip_trim_start_s": "auto", "duration_s": 2.0},
    ]
    with pytest.raises(RuntimeError, match="same clip_path"):
        assembly._resolve_auto_trim(scenes)


# ─── ken_burns_assemble with clip_trim_start_s ───────────────────────────────

def _make_color_video(path: Path, color: str, duration: float, w: int = 270, h: int = 480) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color={color}:s={w}x{h}:r=30",
         "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )

def _probe_format_duration(path: Path) -> float:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    return float(p.stdout.strip())

def _make_silent_wav(path: Path, duration_s: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "anullsrc=cl=mono:r=44100",
         "-t", str(duration_s), "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True,
    )


def test_ken_burns_assemble_clip_trim_start_s_output_duration(tmp_path):
    """clip_trim_start_s seeks into the source; output scene duration is still duration_s."""
    clip = tmp_path / "source.mp4"
    audio = tmp_path / "audio.wav"
    out = tmp_path / "out.mp4"
    _make_color_video(clip, "blue", duration=10.0)
    _make_silent_wav(audio, 3.0)

    scenes = json.dumps([
        {"index": 0, "clip_path": str(clip), "clip_trim_start_s": 2.0,
         "duration_s": 3.0, "still_path": None}
    ])
    assembly.ken_burns_assemble(scenes, str(audio), str(out), "270x480")
    dur = _probe_format_duration(out)
    assert abs(dur - 3.0) < 0.5, f"expected ~3.0s output, got {dur:.2f}s"


def test_ken_burns_assemble_clip_trim_auto_two_scenes(tmp_path):
    """Two consecutive scenes with same clip_path; second uses auto."""
    clip = tmp_path / "source.mp4"
    audio = tmp_path / "audio.wav"
    out = tmp_path / "out.mp4"
    _make_color_video(clip, "green", duration=10.0)
    _make_silent_wav(audio, 4.0)

    scenes = json.dumps([
        {"index": 0, "clip_path": str(clip), "clip_trim_start_s": 0.0,
         "duration_s": 2.0, "still_path": None},
        {"index": 1, "clip_path": str(clip), "clip_trim_start_s": "auto",
         "duration_s": 2.0, "still_path": None},
    ])
    assembly.ken_burns_assemble(scenes, str(audio), str(out), "270x480")
    dur = _probe_format_duration(out)
    assert abs(dur - 4.0) < 0.5, f"expected ~4.0s output, got {dur:.2f}s"
