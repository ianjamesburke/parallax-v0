"""ffmpeg/ffprobe utility helpers.

Pure helpers: locating the ffmpeg binary, capability checks, frame-rate
probing, and color string parsing for the Pillow caption fallback.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# Known locations to search when looking for a drawtext-capable ffmpeg.
# Checked in order; first working binary with drawtext wins.
_DRAWTEXT_SEARCH_PATHS = [
    None,  # sentinel: system PATH ffmpeg (filled in at call time)
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
]


def run_ffmpeg(cmd: list[str], **kwargs):
    """`subprocess.run` wrapper that emits a DEBUG `ffmpeg.invoke` event.

    Captures the actual argv that ran (after any caller-side composition)
    so the run.log carries a faithful trace. Use this at every direct
    ffmpeg/ffprobe `subprocess.run` callsite — emit at the callsite, not
    inside the helper, so the recorded argv is the real one.
    """
    import time as _time
    from . import runlog
    t0 = _time.monotonic()
    result = subprocess.run(cmd, **kwargs)
    duration_ms = int((_time.monotonic() - t0) * 1000)
    runlog.event(
        "ffmpeg.invoke",
        level="DEBUG",
        argv=cmd,
        returncode=result.returncode,
        duration_ms=duration_ms,
    )
    return result


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
    """Return the ffmpeg binary on PATH."""
    return shutil.which("ffmpeg") or "ffmpeg"


def _supports_drawtext(binary: str) -> bool:
    """Return True if the given ffmpeg binary supports the drawtext filter."""
    try:
        result = subprocess.run(
            [binary, "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=5,
        )
        return "drawtext" in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def _get_drawtext_ffmpeg() -> str:
    """Return the best available ffmpeg that supports drawtext.

    Tries the system ffmpeg first. If it lacks drawtext (e.g. standard Homebrew
    build without libfreetype), walks _DRAWTEXT_SEARCH_PATHS for a working
    alternative (e.g. ffmpeg-full). Falls back to the system ffmpeg if nothing
    better is found — callers should check _ffmpeg_has_drawtext() and fall back
    to the Pillow path if needed.
    """
    system = _get_ffmpeg()
    if _supports_drawtext(system):
        return system
    for path in _DRAWTEXT_SEARCH_PATHS:
        candidate = path or system
        if candidate == system:
            continue
        if Path(candidate).exists() and _supports_drawtext(candidate):
            return candidate
    return system


def _ffmpeg_has_drawtext() -> bool:
    """Return True if any available ffmpeg supports the drawtext filter."""
    return _supports_drawtext(_get_drawtext_ffmpeg())


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


