"""Phase 1.3 — test-mode mocks honor requested resolution.

These guard the resolution-adaptation cases in Phase 2.1: without them,
mocks silently emit 1080x1920 regardless of requested size, which lets a
broken adapter pass against placeholder output.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from parallax.shim import render_mock_image, render_mock_video
from parallax.voiceover import _mock_voiceover


_RESOLUTIONS = ["480x854", "720x1280", "1080x1920"]


@pytest.fixture(autouse=True)
def _isolate_output(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path / "output"))
    yield


@pytest.mark.parametrize("resolution", _RESOLUTIONS)
def test_render_mock_image_matches_requested_aspect(tmp_path, resolution):
    target_w, target_h = (int(x) for x in resolution.split("x"))
    target_aspect = target_w / target_h

    path = render_mock_image(
        prompt="a watercolor cat", model="flux-pro",
        out_dir=tmp_path, resolution=resolution,
    )
    assert path.exists()
    assert path.stat().st_size > 0  # legible content placeholder, not 0-byte
    with Image.open(path) as img:
        assert img.format == "PNG"
        w, h = img.size
        actual_aspect = w / h
        # Within 0.5% of target aspect
        assert abs(actual_aspect - target_aspect) / target_aspect < 0.005, (
            f"aspect drift at {resolution}: got {w}x{h} ({actual_aspect:.4f}) "
            f"vs target {target_aspect:.4f}"
        )
        # Exact dimension match (no rounding leakage)
        assert (w, h) == (target_w, target_h)


@pytest.mark.parametrize("resolution", _RESOLUTIONS)
def test_render_mock_video_matches_requested_resolution(tmp_path, resolution):
    target_w, target_h = (int(x) for x in resolution.split("x"))
    duration = 2.0

    path = render_mock_video(
        prompt="apple rolling", model="seedance",
        duration_s=duration, out_dir=tmp_path, resolution=resolution,
    )
    assert path.exists()

    # Probe dimensions
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration",
         "-of", "default=noprint_wrappers=1:nokey=0", str(path)],
        capture_output=True, text=True,
    )
    assert probe.returncode == 0, probe.stderr
    fields = dict(line.split("=", 1) for line in probe.stdout.strip().splitlines() if "=" in line)
    assert int(fields["width"]) == target_w
    assert int(fields["height"]) == target_h

    # Duration probe (format-level for a clean total)
    fmt_probe = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    actual_duration = float(fmt_probe.stdout.strip())
    assert abs(actual_duration - duration) < 0.1, (
        f"duration drift at {resolution}: got {actual_duration:.3f}s vs target {duration:.3f}s"
    )


def test_mock_voiceover_silence_matches_word_timestamps(tmp_path):
    text = "the quick brown fox jumps over the lazy dog"
    out = _mock_voiceover(text, tmp_path)
    parsed = json.loads(out)
    audio_path = Path(parsed["audio_path"])
    total = float(parsed["total_duration_s"])
    words = parsed["words"]

    assert audio_path.exists()
    assert audio_path.stat().st_size > 0
    assert words, "expected non-empty word list"

    # Word timestamps span exactly the declared duration
    last_end = words[-1]["end"]
    assert abs(last_end - total) < 0.05, (
        f"last word.end {last_end} drifts from total {total}"
    )

    # Probed audio duration matches the declared duration ±0.05s
    probe = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True,
    )
    actual_duration = float(probe.stdout.strip())
    assert abs(actual_duration - total) < 0.05, (
        f"silence wav duration {actual_duration:.3f}s drifts from declared {total:.3f}s"
    )
