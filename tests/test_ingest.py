"""Tests for parallax.ingest — clip discovery, estimate path, real path, output shape.

Uses tiny ffmpeg-generated silent .wav fixtures so tests don't need real video
files or actual WhisperX runs. transcribe_words is monkeypatched out to keep
the suite fast and offline.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from parallax import ingest as ingest_mod
from parallax.ingest import ClipIndex, IngestResult, ingest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_silent_wav(path: Path, duration_s: float = 1.0) -> None:
    """Generate a silent mono wav at the given path. ffmpeg must be on PATH."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono",
            "-t", f"{duration_s}",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def clip_dir(tmp_path: Path) -> Path:
    """Directory with three 1-second silent .wav clips."""
    d = tmp_path / "clips"
    d.mkdir()
    for name in ("a.wav", "b.wav", "c.wav"):
        _make_silent_wav(d / name, duration_s=1.0)
    return d


@pytest.fixture
def single_clip(tmp_path: Path) -> Path:
    """A single 1-second silent .wav clip."""
    p = tmp_path / "solo.wav"
    _make_silent_wav(p, duration_s=1.0)
    return p


@pytest.fixture
def fake_transcribe(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Replace transcribe_words with a deterministic stub that writes a fixed words list."""
    fake_words = [
        {"word": "hello", "start": 0.0, "end": 0.4},
        {"word": "world", "start": 0.5, "end": 0.9},
    ]

    def _stub(input_path: str, out_path: str) -> list[dict]:
        Path(out_path).write_text(json.dumps({"words": fake_words, "total_duration_s": 0.9}))
        return list(fake_words)

    monkeypatch.setattr(ingest_mod, "transcribe_words", _stub)
    return fake_words


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_estimate_mode_directory(clip_dir: Path, fake_transcribe: list[dict]) -> None:
    """--estimate returns durations only, writes nothing, sums correctly."""
    result = ingest(clip_dir, estimate=True)

    assert isinstance(result, IngestResult)
    assert result.index_path is None
    assert len(result.clips) == 3
    # Each silent wav is ~1.0s; sum should be roughly 3.0s. Allow slack for ffmpeg rounding.
    assert 2.5 <= result.total_duration_s <= 3.5
    assert result.estimated_cost_usd == 0.0
    # Estimate path must NOT call transcribe_words — every clip's words list is empty.
    assert all(c.words == [] for c in result.clips)
    # No index.json should have been written anywhere under the dir.
    assert not (clip_dir / "index.json").exists()


def test_single_file_writes_sibling_index(single_clip: Path, fake_transcribe: list[dict]) -> None:
    """Single-file mode writes <file>.index.json next to the source clip."""
    result = ingest(single_clip)

    expected = single_clip.with_suffix(single_clip.suffix + ".index.json")
    assert result.index_path == expected
    assert expected.exists()

    payload = json.loads(expected.read_text())
    assert payload["version"] == 1
    assert "generated_at" in payload
    assert "total_duration_s" in payload
    assert "clips" in payload
    assert len(payload["clips"]) == 1
    assert payload["clips"][0]["path"] == str(single_clip)


def test_directory_mode_writes_aggregated_index(
    clip_dir: Path,
    fake_transcribe: list[dict],
) -> None:
    """Directory mode writes one aggregated index.json at the dir root."""
    result = ingest(clip_dir)

    expected = clip_dir / "index.json"
    assert result.index_path == expected
    assert expected.exists()

    payload = json.loads(expected.read_text())
    assert payload["version"] == 1
    assert len(payload["clips"]) == 3
    # Words from the stub flow through into each ClipIndex.
    for clip in result.clips:
        assert clip.words == fake_transcribe
    # And into the serialized payload.
    for clip_payload in payload["clips"]:
        assert clip_payload["words"] == fake_transcribe

    # No per-clip JSON files leaked into the directory.
    stray = [p for p in clip_dir.iterdir() if p.suffix == ".json" and p.name != "index.json"]
    assert stray == [], f"unexpected per-clip JSON in dir: {stray}"


def test_visual_flag_raises(clip_dir: Path) -> None:
    """visual=True is intentionally stubbed until the Gemini path lands."""
    with pytest.raises(NotImplementedError, match="visual indexing"):
        ingest(clip_dir, visual=True)


def test_empty_directory_raises(tmp_path: Path) -> None:
    """A directory with no recognized clips fails fast with a clear message."""
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no recognized clip extensions"):
        ingest(empty)


def test_words_flow_through_clip_index(single_clip: Path, fake_transcribe: list[dict]) -> None:
    """Mocked transcribe_words output appears in ClipIndex.words verbatim."""
    result = ingest(single_clip)
    assert len(result.clips) == 1
    assert isinstance(result.clips[0], ClipIndex)
    assert result.clips[0].words == fake_transcribe
    assert result.clips[0].visual_tags is None
