"""ffmpeg/ffprobe utility helpers.

Pure helpers: locating the ffmpeg binary, capability checks, frame-rate
probing, and color string parsing for the Pillow caption fallback.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_FFMPEG_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"


def parse_resolution(s: str) -> tuple[int, int]:
    """Parse a "WxH" resolution string into an (int, int) tuple.

    Replaces the duplicated `resolution.split("x")` pattern across the
    codebase. Raises ValueError on malformed input rather than silently
    returning truncated/bad values.
    """
    try:
        w_str, h_str = s.split("x")
        return int(w_str), int(h_str)
    except (ValueError, AttributeError) as e:
        raise ValueError(
            f"parse_resolution: expected 'WxH' (e.g. '1080x1920'), got {s!r}"
        ) from e


def _get_ffmpeg() -> str:
    """Return the best available ffmpeg binary — ffmpeg-full (has drawtext) first."""
    import shutil
    if Path(_FFMPEG_FULL).exists():
        return _FFMPEG_FULL
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffmpeg_has_drawtext() -> bool:
    """Return True if the resolved ffmpeg binary supports the drawtext filter."""
    result = subprocess.run(
        [_get_ffmpeg(), "-hide_banner", "-filters"],
        capture_output=True, text=True,
    )
    return "drawtext" in result.stdout


def _probe_fps(video_path: str) -> float:
    """Return video FPS; falls back to 30.0 on any error."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True,
    )
    raw = result.stdout.strip()
    try:
        num, den = raw.split("/")
        return float(num) / float(den)
    except Exception:
        return 30.0


def _parse_color(color: str | None) -> tuple[int, int, int]:
    if not color:
        return (255, 255, 255)
    color = color.split("@")[0].strip()
    if color.lower() == "white":
        return (255, 255, 255)
    if color.lower() == "black":
        return (0, 0, 0)
    if color.startswith("#") and len(color) == 7:
        return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))
    return (255, 255, 255)
