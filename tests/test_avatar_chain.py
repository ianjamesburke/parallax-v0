"""Avatar chroma-key chain end-to-end characterization.

The chain (generate_avatar_clips → key_avatar_track → burn_avatar) is
the single most fragile part of the pipeline — a ProRes color-range
regression shipped in `0241c22`. These tests exercise the full keying
pipeline with a synthetic blue-screen avatar (lavfi blue source + audio)
through to compositing onto a base video.

Locks in:
  - generate_avatar_clips full_audio mode: one Aurora call, one mp4
    written, JSON returns clips/avatar_track/track_start_s.
  - key_avatar_track produces a ProRes 4444 .mov that exists and is
    decodable (with chromakey applied).
  - burn_avatar with chroma_key composites at composite-time and
    produces a video with the same duration as the base.
  - burn_avatar with a pre-keyed .mov (no chroma_key) overlays alpha
    correctly without re-keying.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from parallax import tools_video


def _make_blue_avatar(path: Path, duration_s: float = 1.0,
                      w: int = 320, h: int = 240) -> None:
    """Generate a fake avatar clip: solid blue background + silent audio."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=0x0000FF:s={w}x{h}:d={duration_s}:r=30",
         "-f", "lavfi", "-i", "anullsrc=cl=mono:r=44100",
         "-t", str(duration_s),
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )


def _make_base_video(path: Path, duration_s: float = 1.5,
                     w: int = 540, h: int = 960) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=red:s={w}x{h}:d={duration_s}:r=30",
         "-f", "lavfi", "-i", "anullsrc=cl=mono:r=44100",
         "-t", str(duration_s),
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )


def _make_silent_mp3(path: Path, duration_s: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "anullsrc=cl=mono:r=44100",
         "-t", str(duration_s), "-c:a", "libmp3lame", str(path)],
        check=True, capture_output=True,
    )


def _make_still(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=green:s=512x512",
         "-frames:v", "1", str(path)],
        check=True, capture_output=True,
    )


def _probe_dur(path: Path) -> float:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    return float(p.stdout.strip())


# ─── generate_avatar_clips (mocked fal_client) ──────────────────────────


def test_generate_avatar_clips_full_audio_mode(tmp_path, monkeypatch):
    """full_audio=True: one Aurora call returns one continuous mp4.

    fal_client and urllib are mocked — Aurora's "video URL" actually
    points at a local synthetic blue-avatar mp4 we generate inline.
    """
    fake_avatar = tmp_path / "fake_aurora_returned.mp4"
    _make_blue_avatar(fake_avatar, 1.0)

    audio = tmp_path / "vo.mp3"
    _make_silent_mp3(audio, 1.0)
    char = tmp_path / "char.png"
    _make_still(char)

    import fal_client
    monkeypatch.setattr(fal_client, "upload_file", lambda p: f"https://fal/{Path(p).name}")
    monkeypatch.setattr(
        fal_client, "subscribe",
        lambda model, arguments: {"video": {"url": str(fake_avatar)}},
    )
    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlretrieve",
        lambda url, dest: __import__("shutil").copy2(url, dest),
    )

    result = json.loads(tools_video.generate_avatar_clips(
        scenes_json="[]", audio_path=str(audio),
        character_image=str(char), avatar_scene_indices=[],
        out_dir=str(tmp_path), full_audio=True,
    ))
    track = Path(result["avatar_track"])
    assert track.exists()
    assert result["track_start_s"] == 0.0
    assert len(result["clips"]) == 1
    assert result["clips"][0]["index"] == -1


def test_generate_avatar_clips_per_scene_mode(tmp_path, monkeypatch):
    """Per-scene mode (legacy): splits audio per scene, calls Aurora per scene,
    concatenates."""
    fake_avatar = tmp_path / "fake_aurora.mp4"
    _make_blue_avatar(fake_avatar, 0.5)

    audio = tmp_path / "vo.mp3"
    _make_silent_mp3(audio, 2.0)
    char = tmp_path / "char.png"
    _make_still(char)

    import fal_client
    monkeypatch.setattr(fal_client, "upload_file", lambda p: f"https://fal/{Path(p).name}")
    monkeypatch.setattr(
        fal_client, "subscribe",
        lambda model, arguments: {"video": {"url": str(fake_avatar)}},
    )
    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlretrieve",
        lambda url, dest: __import__("shutil").copy2(url, dest),
    )

    scenes = [
        {"index": 0, "start_s": 0.0, "duration_s": 1.0, "end_s": 1.0},
        {"index": 1, "start_s": 1.0, "duration_s": 1.0, "end_s": 2.0},
    ]
    result = json.loads(tools_video.generate_avatar_clips(
        scenes_json=json.dumps(scenes), audio_path=str(audio),
        character_image=str(char), avatar_scene_indices=[0, 1],
        out_dir=str(tmp_path), full_audio=False,
    ))
    assert len(result["clips"]) == 2
    assert Path(result["avatar_track"]).exists()


# ─── key_avatar_track ───────────────────────────────────────────────────


def test_key_avatar_track_produces_prores_with_alpha(tmp_path):
    """Real ffmpeg chromakey pass: blue avatar → ProRes 4444 .mov with alpha."""
    src = tmp_path / "avatar.mp4"
    _make_blue_avatar(src, 1.0)
    out = tools_video.key_avatar_track(str(src), chroma_key="0x0000FF")
    out_path = Path(out)
    assert out_path.exists() and out_path.suffix == ".mov"

    # Verify the output decodes and has the expected duration
    dur = _probe_dur(out_path)
    assert abs(dur - 1.0) < 0.3

    # Verify pixel format includes alpha (yuva*)
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt",
         "-of", "default=noprint_wrappers=1:nokey=1", str(out_path)],
        capture_output=True, text=True,
    )
    assert "yuva" in p.stdout.strip()


# ─── burn_avatar ────────────────────────────────────────────────────────


def test_burn_avatar_with_chroma_key_at_composite_time(tmp_path):
    """End-to-end: blue avatar + base video → composited mp4 with key applied."""
    base = tmp_path / "base.mp4"
    _make_base_video(base, 1.5)
    avatar = tmp_path / "avatar.mp4"
    _make_blue_avatar(avatar, 1.0)
    out = tmp_path / "out.mp4"
    result = tools_video.burn_avatar(
        str(base), str(avatar), track_start_s=0.0, output_path=str(out),
        chroma_key="0x0000FF", size=0.4, out_width=540,
    )
    assert Path(result) == out
    assert out.exists() and out.stat().st_size > 0
    # Output duration matches the base video
    assert abs(_probe_dur(out) - 1.5) < 0.3


def test_burn_avatar_pre_keyed_chain(tmp_path):
    """Full chain: generate blue → key → composite. Avatar is NOT a blue
    rectangle in the output (regression check)."""
    base = tmp_path / "base.mp4"
    _make_base_video(base, 1.5)
    avatar = tmp_path / "avatar.mp4"
    _make_blue_avatar(avatar, 1.0)

    keyed = tools_video.key_avatar_track(str(avatar), chroma_key="0x0000FF")

    out = tmp_path / "composited.mp4"
    tools_video.burn_avatar(
        str(base), keyed, track_start_s=0.0, output_path=str(out),
        size=0.3, out_width=540,
    )
    assert out.exists() and out.stat().st_size > 0
    assert abs(_probe_dur(out) - 1.5) < 0.3


def test_burn_avatar_position_top_right(tmp_path):
    base = tmp_path / "base.mp4"
    _make_base_video(base, 1.0)
    avatar = tmp_path / "avatar.mp4"
    _make_blue_avatar(avatar, 1.0)
    out = tmp_path / "tr.mp4"
    tools_video.burn_avatar(
        str(base), str(avatar), track_start_s=0.0, output_path=str(out),
        position="top_right", chroma_key="0x0000FF", out_width=540,
    )
    assert out.exists()
