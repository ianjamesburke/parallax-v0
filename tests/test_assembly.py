"""Ken Burns assembly characterization.

Real ffmpeg with lavfi-generated stills + silent wav. Asserts on the
output mp4's video and audio durations to lock in:

  - ken_burns_assemble produces a video whose video stream length sums
    the per-scene durations, and whose audio stream covers the full wav
    (regression on the trailing-tail trim from `-shortest`).
  - _zoom_filter emits a stable string per direction (snapshot).
  - _make_kb_clip writes an mp4 of correct duration + resolution.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from parallax import tools_video


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


def _probe_stream_duration(path: Path, stream: str) -> float:
    """stream='v:0' for video, 'a:0' for audio."""
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_entries", "stream=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    raw = p.stdout.strip()
    if not raw or raw == "N/A":
        return _probe_format_duration(path)
    return float(raw)


def _probe_resolution(path: Path) -> tuple[int, int]:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    parts = p.stdout.strip().split(",")
    return int(parts[0]), int(parts[1])


# ─── _zoom_filter ────────────────────────────────────────────────────────


def test_zoom_filter_none_returns_normalize_only():
    f = tools_video._zoom_filter(None, 1.25, 5.0, "1080", "1920")
    assert "scale=1080:1920" in f
    assert "fps=30" in f
    assert "zoom" not in f and "crop" not in f


def test_zoom_filter_in_centered():
    f = tools_video._zoom_filter("in", 1.25, 5.0, "1080", "1920")
    assert "crop=1080:1920" in f
    assert "(iw-1080)/2" in f
    assert "(ih-1920)/2" in f


def test_zoom_filter_directional_anchors():
    """Each direction uses a distinct crop anchor."""
    up = tools_video._zoom_filter("up", 1.25, 5.0, "1080", "1920")
    down = tools_video._zoom_filter("down", 1.25, 5.0, "1080", "1920")
    left = tools_video._zoom_filter("left", 1.25, 5.0, "1080", "1920")
    right = tools_video._zoom_filter("right", 1.25, 5.0, "1080", "1920")
    # Each contains its direction's crop coords (not exhaustive — just distinct)
    assert ":0," in up  # cy=0 for up
    assert "(ih-1920)" in down  # cy=(ih-h) for down
    assert "crop=1080:1920:0:" in left  # cx=0 for left
    assert "(iw-1080)" in right  # cx=(iw-w) for right


def test_zoom_filter_zoom_factor_in_expr():
    f = tools_video._zoom_filter("in", 1.50, 4.0, "1080", "1920")
    # zoom delta should be 0.5
    assert "0.5000" in f or "+0.5*" in f
    # duration appears as denominator
    assert "/4.0" in f


def test_zoom_filter_preserves_source_aspect_ratio():
    """Zoom branch must use force_original_aspect_ratio=increase + crop so
    non-9:16 input clips fill the target frame WITHOUT being stretched."""
    f = tools_video._zoom_filter("in", 1.25, 5.0, "1080", "1920")
    assert "force_original_aspect_ratio=increase" in f, (
        "zoom filter must fit-to-fill source, not stretch it"
    )
    # The fit-to-fill path crops the centered window before the per-frame zoom
    assert f.count("crop=1080:1920") == 2, (
        "expected two crops: one to normalize source aspect, one for zoom anchor"
    )


# ─── _make_kb_clip ───────────────────────────────────────────────────────


def test_make_kb_clip_writes_mp4_with_correct_duration_and_resolution(tmp_path, monkeypatch):
    """In test mode, _make_kb_clip skips the Ken Burns motion path and just
    resizes — but still writes a valid mp4 at the requested resolution."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    still = tmp_path / "in.png"
    _make_still(still, "red", 1080, 1920)
    out = tmp_path / "kb.mp4"
    tools_video._make_kb_clip(str(still), 1.0, str(out), "1080x1920", scene_index=0)
    assert out.exists() and out.stat().st_size > 0
    w, h = _probe_resolution(out)
    assert w == 1080 and h == 1920
    dur = _probe_format_duration(out)
    assert abs(dur - 1.0) < 0.15


def test_make_kb_clip_real_path_with_zoom(tmp_path, monkeypatch):
    monkeypatch.delenv("PARALLAX_TEST_MODE", raising=False)
    still = tmp_path / "in.png"
    _make_still(still, "blue", 1080, 1920)
    out = tmp_path / "kb.mp4"
    tools_video._make_kb_clip(
        str(still), 1.0, str(out), "1080x1920",
        scene_index=0, zoom_direction="in", zoom_amount=1.2,
    )
    assert out.exists()
    w, h = _probe_resolution(out)
    assert w == 1080 and h == 1920


# ─── ken_burns_assemble ──────────────────────────────────────────────────


def test_ken_burns_assemble_video_and_audio_durations(tmp_path, monkeypatch):
    """The produced mp4's audio stream covers the full wav, and the total
    format duration roughly matches the longer of (sum of scenes, audio)."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    still1 = tmp_path / "s1.png"
    still2 = tmp_path / "s2.png"
    _make_still(still1, "red")
    _make_still(still2, "green")

    audio = tmp_path / "narration.wav"
    _make_silent_wav(audio, 2.0)

    scenes = [
        {"index": 0, "still_path": str(still1), "duration_s": 1.0},
        {"index": 1, "still_path": str(still2), "duration_s": 1.0},
    ]
    out = tmp_path / "assembled.mp4"
    result_path = tools_video.ken_burns_assemble(
        json.dumps(scenes), str(audio), str(out), "1080x1920",
    )
    assert Path(result_path) == out
    assert out.exists()

    # Video stream ~ sum of scene durations
    v_dur = _probe_stream_duration(out, "v:0")
    assert abs(v_dur - 2.0) < 0.3, f"video duration {v_dur} != 2.0"
    # Audio stream covers full wav (regression on trailing-tail issue)
    a_dur = _probe_stream_duration(out, "a:0")
    assert abs(a_dur - 2.0) < 0.3, f"audio duration {a_dur} != 2.0"


def test_ken_burns_assemble_empty_scenes_raises(tmp_path):
    audio = tmp_path / "n.wav"
    _make_silent_wav(audio, 0.5)
    with pytest.raises(ValueError, match="No scenes"):
        tools_video.ken_burns_assemble("[]", str(audio), str(tmp_path / "x.mp4"))


def test_ken_burns_assemble_with_clip_path(tmp_path, monkeypatch):
    """Scene with pre-existing clip_path is looped/trimmed (not re-rendered from still)."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    # A pre-rendered 0.5s clip shorter than the requested 1.0s scene → triggers ping-pong
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=red:s=1080x1920:d=0.5",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True, capture_output=True,
    )
    audio = tmp_path / "n.wav"
    _make_silent_wav(audio, 1.0)
    scenes = [{"index": 0, "clip_path": str(clip), "duration_s": 1.0}]
    out = tmp_path / "assembled.mp4"
    tools_video.ken_burns_assemble(
        json.dumps(scenes), str(audio), str(out), "1080x1920",
    )
    assert out.exists() and out.stat().st_size > 0
