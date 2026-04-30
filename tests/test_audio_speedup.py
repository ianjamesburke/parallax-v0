"""audio.speedup characterization — atempo wrapper.

Locks in:
  - rate=1.0 is identity (output duration ≈ input duration)
  - rate=1.5 shortens by ~33% (1/1.5 of original)
  - ffmpeg failure raises a clear error naming `audio.speedup`
  - parse_by_pct translates '+30%' → 1.3 and '-20%' → 0.8
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from parallax import audio


def _make_silent_wav(path: Path, duration_s: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "anullsrc=cl=mono:r=44100",
         "-t", str(duration_s), "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True,
    )


def _probe_duration(path: Path) -> float:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    return float(p.stdout.strip())


def test_speedup_rate_1_is_identity(tmp_path):
    src = tmp_path / "src.wav"
    dst = tmp_path / "dst.wav"
    _make_silent_wav(src, 2.0)
    out = audio.speedup(src, dst, 1.0)
    assert out == dst
    assert dst.exists()
    assert abs(_probe_duration(dst) - 2.0) < 0.1


def test_speedup_rate_1_5_shortens_by_33pct(tmp_path):
    src = tmp_path / "src.wav"
    dst = tmp_path / "dst.wav"
    _make_silent_wav(src, 3.0)
    audio.speedup(src, dst, 1.5)
    expected = 3.0 / 1.5  # 2.0s
    assert abs(_probe_duration(dst) - expected) < 0.15


def test_speedup_rate_0_8_lengthens(tmp_path):
    src = tmp_path / "src.wav"
    dst = tmp_path / "dst.wav"
    _make_silent_wav(src, 1.6)
    audio.speedup(src, dst, 0.8)
    expected = 1.6 / 0.8  # 2.0s
    assert abs(_probe_duration(dst) - expected) < 0.15


def test_speedup_ffmpeg_failure_raises(tmp_path):
    src = tmp_path / "garbage.wav"
    src.write_bytes(b"not-a-wav")
    dst = tmp_path / "dst.wav"
    with pytest.raises(RuntimeError, match="audio.speedup"):
        audio.speedup(src, dst, 1.5)


def test_speedup_missing_input_raises(tmp_path):
    src = tmp_path / "missing.wav"
    dst = tmp_path / "dst.wav"
    with pytest.raises(FileNotFoundError):
        audio.speedup(src, dst, 1.5)


def test_speedup_invalid_rate_raises(tmp_path):
    src = tmp_path / "src.wav"
    dst = tmp_path / "dst.wav"
    _make_silent_wav(src, 0.5)
    with pytest.raises(ValueError):
        audio.speedup(src, dst, 0.0)
    with pytest.raises(ValueError):
        audio.speedup(src, dst, -1.0)
    with pytest.raises(ValueError):
        audio.speedup(src, dst, 0.49)


def test_parse_by_pct_positive():
    assert abs(audio.parse_by_pct("30%") - 1.3) < 1e-9


def test_parse_by_pct_negative():
    assert abs(audio.parse_by_pct("-20%") - 0.8) < 1e-9


def test_parse_by_pct_zero():
    assert audio.parse_by_pct("0%") == 1.0


def test_parse_by_pct_requires_percent_sign():
    with pytest.raises(ValueError):
        audio.parse_by_pct("30")


def test_parse_by_pct_rejects_garbage():
    with pytest.raises(ValueError):
        audio.parse_by_pct("fast%")
