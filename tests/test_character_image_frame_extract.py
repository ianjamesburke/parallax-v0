"""character_image frame extraction for video files.

When character_image points to a video file, _extract_character_image_frame
must extract a still frame and return the PNG path. Image files pass through
unchanged. Extracted frames are cached so ffmpeg is not called twice.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from parallax.stages import _extract_character_image_frame


# ---------------------------------------------------------------------------
# image passthrough
# ---------------------------------------------------------------------------

def test_image_passthrough(tmp_path):
    """PNG/JPG paths are returned as-is — no ffmpeg call."""
    img = tmp_path / "hero.png"
    img.write_bytes(b"\x89PNG")
    with patch("parallax.stages.run_ffmpeg") as mock_ff:
        result = _extract_character_image_frame(str(img), tmp_path)
    assert result == str(img)
    mock_ff.assert_not_called()


def test_jpg_passthrough(tmp_path):
    img = tmp_path / "hero.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    with patch("parallax.stages.run_ffmpeg") as mock_ff:
        result = _extract_character_image_frame(str(img), tmp_path)
    assert result == str(img)
    mock_ff.assert_not_called()


# ---------------------------------------------------------------------------
# video → frame extraction
# ---------------------------------------------------------------------------

def _fake_ffmpeg_writes(cmd, **_):
    """Side effect: writes the last path arg as a PNG."""
    out = Path(cmd[-1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"\x89PNG")
    return MagicMock(returncode=0, stderr="")


def test_mp4_extracts_frame(tmp_path):
    """MP4 character_image triggers ffmpeg frame extraction."""
    video = tmp_path / "hero.mp4"
    video.write_bytes(b"fake")
    cache_png = tmp_path / "__parallax_cache__" / "character_frame.png"

    with patch("parallax.stages.run_ffmpeg", side_effect=_fake_ffmpeg_writes) as mock_ff:
        result = _extract_character_image_frame(str(video), tmp_path)

    assert result == str(cache_png)
    assert cache_png.exists()
    assert mock_ff.call_count == 1
    cmd = mock_ff.call_args[0][0]
    assert str(video) in cmd
    assert str(cache_png) in cmd


def test_mov_extension_triggers_extract(tmp_path):
    video = tmp_path / "hero.mov"
    video.write_bytes(b"fake")

    with patch("parallax.stages.run_ffmpeg", side_effect=_fake_ffmpeg_writes):
        result = _extract_character_image_frame(str(video), tmp_path)

    assert result == str(tmp_path / "__parallax_cache__" / "character_frame.png")


# ---------------------------------------------------------------------------
# cache hit
# ---------------------------------------------------------------------------

def test_cache_hit_skips_ffmpeg(tmp_path):
    """If the cache PNG already exists, ffmpeg is not called again."""
    video = tmp_path / "hero.mp4"
    video.write_bytes(b"fake")
    cache_png = tmp_path / "__parallax_cache__" / "character_frame.png"
    cache_png.parent.mkdir(parents=True)
    cache_png.write_bytes(b"\x89PNG")

    with patch("parallax.stages.run_ffmpeg") as mock_ff:
        result = _extract_character_image_frame(str(video), tmp_path)

    assert result == str(cache_png)
    mock_ff.assert_not_called()


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------

def test_ffmpeg_failure_raises(tmp_path):
    """ffmpeg non-zero exit raises RuntimeError naming the video path."""
    video = tmp_path / "hero.mp4"
    video.write_bytes(b"fake")

    with patch("parallax.stages.run_ffmpeg", return_value=MagicMock(returncode=1, stderr="error detail")):
        with pytest.raises(RuntimeError, match="hero.mp4"):
            _extract_character_image_frame(str(video), tmp_path)
