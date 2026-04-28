"""parallax.video — video utilities: frame extraction, color sampling."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def extract_frame(video_path: str, time_s: float, out_path: str | None = None) -> str:
    """Extract a single frame from a video at the given timestamp.

    Returns the output path. Defaults to a temp file if out_path is not given.
    """
    if out_path is None:
        out_path = str(Path(tempfile.mktemp(suffix=".jpg")))

    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(time_s), "-i", video_path, "-vframes", "1", out_path],
        check=True,
        capture_output=True,
    )
    return out_path


def sample_color(
    input_path: str,
    x: int = 10,
    y: int = 10,
    time_s: float = 2.0,
) -> str:
    """Sample a pixel color from a video frame or image.

    Returns the color as '0xRRGGBB'. For videos, samples the frame at time_s.
    """
    from PIL import Image

    if Path(input_path).suffix.lower() in _VIDEO_EXTS:
        frame_path = extract_frame(input_path, time_s, tempfile.mktemp(suffix=".png"))
        img_path = frame_path
        cleanup = True
    else:
        img_path = input_path
        cleanup = False

    try:
        img = Image.open(img_path).convert("RGB")
        pixel = img.getpixel((x, y))
        r, g, b = int(pixel[0]), int(pixel[1]), int(pixel[2])  # type: ignore[index]
    finally:
        if cleanup:
            Path(img_path).unlink(missing_ok=True)

    return f"0x{r:02X}{g:02X}{b:02X}"
