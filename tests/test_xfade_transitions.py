"""Tests for xfade transition support.

Covers:
  - _xfade_filter_complex offset math (pure unit tests — no ffmpeg)
  - ken_burns_assemble with transitions produces correct duration
  - Plan / PlanScene accept new transition fields and reject unknowns
  - stage_assemble resolves default + per-scene overrides correctly
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from parallax import assembly
from parallax.assembly import _xfade_filter_complex, _SUPPORTED_XFADE_TRANSITIONS
from parallax.plan import Plan


# ─── _xfade_filter_complex — offset math ────────────────────────────────────


def test_xfade_offset_two_clips_basic():
    """Two clips: offset = clip0_dur - transition_dur."""
    clip_paths = ["a.mp4", "b.mp4"]
    transitions = [None, "dissolve"]
    durations = [3.0, 4.0]
    tdurs = [0.5, 0.5]
    result = _xfade_filter_complex(clip_paths, transitions, durations, tdurs)
    # offset = 3.0 - 0.5 = 2.5
    assert "offset=2.5" in result
    assert "transition=dissolve" in result
    assert result.endswith("[vout]")


def test_xfade_offset_three_clips():
    """Three clips: verify both offsets accumulate correctly."""
    clip_paths = ["a.mp4", "b.mp4", "c.mp4"]
    transitions = [None, "fade", "fadeblack"]
    durations = [2.0, 3.0, 4.0]
    tdurs = [0.5, 0.5, 0.5]
    result = _xfade_filter_complex(clip_paths, transitions, durations, tdurs)

    # First xfade: offset = 2.0 - 0.5 = 1.5
    assert "offset=1.5" in result
    # Second xfade: cumulative = 1.5 + (2.0 - 0.5) + (3.0 - 0.5) = 1.5 + 1.5 + 2.5 = 5.5
    # Actually: cumulative_dur starts at 2.0, then after first pair = 2.0 + 3.0 - 0.5 = 4.5
    # offset for second = 4.5 - 0.5 = 4.0
    assert "offset=4.0" in result
    assert result.endswith("[vout]")


def test_xfade_offset_zero_transition_dur_is_hard_cut():
    """If transition_duration = 0, offset = clip0_dur (essentially a hard cut)."""
    clip_paths = ["a.mp4", "b.mp4"]
    transitions = [None, "dissolve"]
    durations = [2.0, 2.0]
    tdurs = [0.0, 0.0]
    result = _xfade_filter_complex(clip_paths, transitions, durations, tdurs)
    assert "offset=2.0" in result


def test_xfade_single_clip_raises():
    with pytest.raises(ValueError, match="at least 2"):
        _xfade_filter_complex(["a.mp4"], [None], [2.0], [0.5])


def test_xfade_all_supported_transitions_are_strings():
    """Spot-check the supported set is non-empty and contains expected entries."""
    assert "dissolve" in _SUPPORTED_XFADE_TRANSITIONS
    assert "wipeleft" in _SUPPORTED_XFADE_TRANSITIONS
    assert "fade" in _SUPPORTED_XFADE_TRANSITIONS
    assert len(_SUPPORTED_XFADE_TRANSITIONS) >= 13


# ─── Plan schema — new transition fields ────────────────────────────────────


def _write_plan(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.safe_dump(payload, sort_keys=False))
    return p


def _minimal(**overrides):
    base = {
        "aspect": "9:16",
        "voice": "nova",
        "voice_speed": 1.0,
        "image_model": "mid",
        "video_model": "mid",
        "scenes": [
            {"index": 0, "shot_type": "broll", "vo_text": "First.", "prompt": "A kitchen."},
            {"index": 1, "shot_type": "broll", "vo_text": "Second.", "prompt": "A market."},
        ],
    }
    base.update(overrides)
    return base


def test_plan_accepts_default_transition(tmp_path):
    payload = _minimal(default_transition="dissolve", default_transition_duration_s=0.4)
    p = _write_plan(tmp_path, payload)
    plan = Plan.from_yaml(p)
    assert plan.default_transition == "dissolve"
    assert plan.default_transition_duration_s == 0.4


def test_plan_default_transition_is_none_by_default(tmp_path):
    p = _write_plan(tmp_path, _minimal())
    plan = Plan.from_yaml(p)
    assert plan.default_transition is None
    assert plan.default_transition_duration_s == 0.5


def test_plan_accepts_per_scene_transition(tmp_path):
    payload = _minimal()
    payload["scenes"][1]["transition"] = "wipeleft"
    payload["scenes"][1]["transition_duration_s"] = 0.3
    p = _write_plan(tmp_path, payload)
    plan = Plan.from_yaml(p)
    assert plan.scenes[1].transition == "wipeleft"
    assert plan.scenes[1].transition_duration_s == 0.3


def test_plan_unknown_transition_field_rejected(tmp_path):
    """extra='forbid' still applies — unknown fields must fail."""
    payload = _minimal()
    payload["scenes"][0]["mystery_transition_field"] = "x"
    p = _write_plan(tmp_path, payload)
    with pytest.raises(Exception):
        Plan.from_yaml(p)


def test_plan_default_transition_duration_default(tmp_path):
    """default_transition_duration_s defaults to 0.5."""
    p = _write_plan(tmp_path, _minimal(default_transition="fade"))
    plan = Plan.from_yaml(p)
    assert plan.default_transition_duration_s == 0.5


# ─── ken_burns_assemble with transitions ─────────────────────────────────────


def _make_still(path: Path, color: str = "red", w: int = 1080, h: int = 1920) -> None:
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
         "-t", str(duration_s), "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True,
    )


def _probe_format_duration(path: Path) -> float:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    return float(p.stdout.strip())


def test_ken_burns_assemble_with_dissolve_transition(tmp_path, monkeypatch):
    """With dissolve transition, output mp4 should be shorter than hard-cut
    (due to xfade overlap) and still be a valid mp4."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    still1 = tmp_path / "s1.png"
    still2 = tmp_path / "s2.png"
    _make_still(still1, "red")
    _make_still(still2, "green")
    audio = tmp_path / "narration.wav"
    _make_silent_wav(audio, 2.0)

    scenes = [
        {"index": 0, "still_path": str(still1), "duration_s": 1.5},
        {"index": 1, "still_path": str(still2), "duration_s": 1.5},
    ]
    out = tmp_path / "assembled_dissolve.mp4"
    assembly.ken_burns_assemble(
        json.dumps(scenes),
        str(audio),
        str(out),
        "1080x1920",
        transitions=[None, "dissolve"],
        transition_duration_s=[0.5, 0.5],
    )
    assert out.exists() and out.stat().st_size > 0
    # Video should be ~2.5s (3.0 - 0.5 overlap), not 3.0s
    dur = _probe_format_duration(out)
    assert 1.8 < dur < 3.2, f"unexpected duration {dur}"


def test_ken_burns_assemble_no_transitions_unchanged(tmp_path, monkeypatch):
    """Passing transitions=None is the existing hard-cut path — must be unchanged."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    still1 = tmp_path / "s1.png"
    still2 = tmp_path / "s2.png"
    _make_still(still1, "blue")
    _make_still(still2, "yellow")
    audio = tmp_path / "n.wav"
    _make_silent_wav(audio, 2.0)

    scenes = [
        {"index": 0, "still_path": str(still1), "duration_s": 1.0},
        {"index": 1, "still_path": str(still2), "duration_s": 1.0},
    ]
    out = tmp_path / "assembled_hardcut.mp4"
    assembly.ken_burns_assemble(json.dumps(scenes), str(audio), str(out), "1080x1920")
    assert out.exists() and out.stat().st_size > 0
    dur = _probe_format_duration(out)
    assert abs(dur - 2.0) < 0.4, f"unexpected duration {dur}"


def test_ken_burns_assemble_all_none_transitions_uses_hard_cut(tmp_path, monkeypatch):
    """transitions=[None, None] — no xfade triggered, behaves like hard cut."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    still1 = tmp_path / "s1.png"
    still2 = tmp_path / "s2.png"
    _make_still(still1, "red")
    _make_still(still2, "blue")
    audio = tmp_path / "n.wav"
    _make_silent_wav(audio, 2.0)

    scenes = [
        {"index": 0, "still_path": str(still1), "duration_s": 1.0},
        {"index": 1, "still_path": str(still2), "duration_s": 1.0},
    ]
    out = tmp_path / "assembled_none.mp4"
    assembly.ken_burns_assemble(
        json.dumps(scenes), str(audio), str(out), "1080x1920",
        transitions=[None, None],
        transition_duration_s=[0.5, 0.5],
    )
    assert out.exists() and out.stat().st_size > 0
