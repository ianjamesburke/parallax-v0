"""Headline and title overlays.

`burn_titles` paints a list of timed section titles roughly 20% from the
top with a translucent box. `burn_headline` paints a single multi-line
headline near the top with an opaque block background (Instagram /
TikTok style). Both reuse the caption style presets for fonts.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .captions.styles import CAPTION_STYLES, _FONTS_DIR
from .ffmpeg_utils import _get_ffmpeg, run_ffmpeg
from .log import get_logger

log = get_logger(__name__)


def burn_titles(
    video_path: str,
    titles: list[dict],
    output_path: str | None = None,
    fontsize: int = 72,
    style: str = "bebas",
) -> str:
    """Burn timed section-title overlays onto a video.

    Each entry in titles: {text, start_s, end_s}
    Titles appear at ~20% from the top, centred, with a semi-transparent background.
    Returns the output video path.
    """
    if not titles:
        return video_path

    out = Path(output_path or str(Path(video_path).with_stem(Path(video_path).stem + "_titled")))
    out.parent.mkdir(parents=True, exist_ok=True)

    s = CAPTION_STYLES.get(style, CAPTION_STYLES["bebas"])
    fontfile = s.get("fontfile")
    font_path = str(_FONTS_DIR / fontfile) if fontfile else s.get("system_font", "")

    filters = []
    for t in titles:
        text = t["text"]
        start_s = float(t["start_s"])
        end_s = float(t["end_s"])
        escaped = text.replace("\\", "\\\\").replace("'", "’").replace(":", "\\:")
        if s.get("uppercase"):
            escaped = escaped.upper()
        filters.append(
            f"drawtext=fontfile='{font_path}'"
            f":text='{escaped}'"
            f":fontsize={fontsize}"
            f":fontcolor=white"
            f":bordercolor=black:borderw=5"
            f":box=1:boxcolor=black@0.55:boxborderw=22"
            f":x=(w-tw)/2"
            f":y=h*20/100"
            f":enable='gte(t,{start_s})*lt(t,{end_s})'"
        )

    result = run_ffmpeg(
        [_get_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", video_path,
         "-vf", ",".join(filters),
         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
         "-c:a", "copy",
         str(out)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"burn_titles failed:\n{result.stderr[:500]}")
    log.info("burn_titles: wrote %s", out)
    return str(out)


def burn_headline(
    video_path: str,
    text: str,
    output_path: str | None = None,
    fontsize: int = 64,
    bg_color: str = "white",
    text_color: str = "black",
    font_name: str = "bangers",
    y_position: str = "h*12/100",
    end_time_s: float | None = None,
) -> str:
    """Overlay a headline with a solid block background (Instagram/TikTok style).

    end_time_s: if set, headline fades out at this timestamp (use first scene's end_s
                so the headline is only visible during the intro).
    bg_color / text_color accept any ffmpeg color string (e.g. 'white', 'black', '#FF0000').
    y_position is an ffmpeg expression for the TOP of the text block (default: 12% from top).
    font_name: one of bangers, impact, bebas, anton, clean (uses bundled fonts).
    Returns the output video path.
    """
    out = Path(output_path or str(Path(video_path).with_stem(Path(video_path).stem + "_headline")))
    out.parent.mkdir(parents=True, exist_ok=True)

    style = CAPTION_STYLES.get(font_name, CAPTION_STYLES["bangers"])
    fontfile = style.get("fontfile")
    font_path = str(_FONTS_DIR / fontfile) if fontfile else style.get("system_font", "")

    enable_clause = f":enable='lt(t,{end_time_s})'" if end_time_s is not None else ""
    pad = max(8, fontsize // 5)
    line_height = int(fontsize * 1.30)

    # One drawtext per line — each gets its own tight box (TikTok per-line style)
    lines = text.split("\n")
    filters = []
    for i, line in enumerate(lines):
        esc = line.replace("\\", "\\\\").replace("’", "’").replace("'", "’").replace(":", "\\:")
        if style.get("uppercase"):
            esc = esc.upper()
        y_expr = f"({y_position})+{i}*{line_height}"
        filters.append(
            f"drawtext=fontfile='{font_path}'"
            f":text='{esc}'"
            f":fontsize={fontsize}"
            f":fontcolor={text_color}"
            f":box=1:boxcolor={bg_color}:boxborderw={pad}"
            f":x=(w-tw)/2"
            f":y={y_expr}"
            f"{enable_clause}"
        )
    filter_str = ",".join(filters)

    result = run_ffmpeg(
        [_get_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", video_path,
         "-vf", filter_str,
         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
         "-c:a", "copy",
         str(out)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"burn_headline failed:\n{result.stderr[:500]}")
    log.info("burn_headline: wrote %s", out)
    return str(out)
