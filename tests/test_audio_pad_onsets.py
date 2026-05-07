"""audio.pad_onsets — onset padding characterization tests.

Locks in:
  - words with sufficient lead-in are untouched (no-op path)
  - words with insufficient lead-in get silence inserted; output is longer
  - first word with no lead-in gets silence prepended
  - multiple insertions all apply correctly
  - missing input raises FileNotFoundError naming "pad_onsets"
  - empty words list raises ValueError
"""

from __future__ import annotations

import json
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


def test_no_op_when_all_lead_ins_sufficient(tmp_path):
    src = tmp_path / "src.wav"
    dst = tmp_path / "dst.wav"
    _make_silent_wav(src, 2.0)

    # All gaps >= 0.05s pad — no insertions needed
    words = [
        {"word": "one", "start": 0.1, "end": 0.5},
        {"word": "two", "start": 0.7, "end": 1.1},
    ]
    result = audio.pad_onsets(str(src), str(dst), words, pad_s=0.05)

    assert result["onsets_padded"] == 0
    assert result["seconds_added"] == 0.0
    assert dst.exists()
    assert abs(_probe_duration(dst) - result["original_duration_s"]) < 0.05


def test_pads_single_word_with_short_lead_in(tmp_path):
    src = tmp_path / "src.wav"
    dst = tmp_path / "dst.wav"
    _make_silent_wav(src, 2.0)

    # word "two" starts 0.01s after "one" ends — well below 0.05s pad
    words = [
        {"word": "one", "start": 0.1, "end": 0.5},
        {"word": "two", "start": 0.51, "end": 1.0},
    ]
    result = audio.pad_onsets(str(src), str(dst), words, pad_s=0.05)

    assert result["onsets_padded"] == 1
    assert abs(result["seconds_added"] - 0.04) < 0.005
    assert dst.exists()
    new_dur = _probe_duration(dst)
    assert new_dur > result["original_duration_s"] - 0.05  # got longer (or close)


def test_pads_first_word_when_no_lead_in(tmp_path):
    """First word starts at 0 — no lead-in at all, should prepend silence."""
    src = tmp_path / "src.wav"
    dst = tmp_path / "dst.wav"
    _make_silent_wav(src, 1.5)

    words = [{"word": "go", "start": 0.0, "end": 0.4}]
    result = audio.pad_onsets(str(src), str(dst), words, pad_s=0.05)

    assert result["onsets_padded"] == 1
    assert abs(result["seconds_added"] - 0.05) < 0.005
    assert dst.exists()


def test_multiple_insertions(tmp_path):
    src = tmp_path / "src.wav"
    dst = tmp_path / "dst.wav"
    _make_silent_wav(src, 3.0)

    # Gaps between words are all 0.01s — all below 0.05s
    words = [
        {"word": "a", "start": 0.1, "end": 0.5},
        {"word": "b", "start": 0.51, "end": 0.9},
        {"word": "c", "start": 0.91, "end": 1.3},
    ]
    result = audio.pad_onsets(str(src), str(dst), words, pad_s=0.05)

    assert result["onsets_padded"] == 2
    assert abs(result["seconds_added"] - 0.08) < 0.005
    assert dst.exists()
    new_dur = _probe_duration(dst)
    assert new_dur > 3.0 - 0.1  # output is longer


def test_missing_input_raises(tmp_path):
    dst = tmp_path / "dst.wav"
    words = [{"word": "x", "start": 0.1, "end": 0.5}]
    with pytest.raises(FileNotFoundError, match="pad_onsets"):
        audio.pad_onsets(str(tmp_path / "missing.wav"), str(dst), words)


def test_empty_words_raises(tmp_path):
    src = tmp_path / "src.wav"
    dst = tmp_path / "dst.wav"
    _make_silent_wav(src, 1.0)
    with pytest.raises(ValueError, match="pad_onsets"):
        audio.pad_onsets(str(src), str(dst), [], pad_s=0.05)
