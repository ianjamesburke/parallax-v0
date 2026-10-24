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
from .ffmpeg_utils import _ffmpeg_has_drawtext, _get_drawtext_ffmpeg, run_ffmpeg
from .log import get_logger

log = get_logger(__name__)

_DRAWTEXT_REQUIRED_MSG = (
    "ffmpeg on PATH lacks drawtext support (libfreetype). "
    "On macOS run: brew install ffmpeg-full. "
    "On Linux run: sudo apt install ffmpeg (Ubuntu/Debian ships with freetype enabled)."
)


def _layout_headline(text: str, width: int, height: int, fontsize: int, font_name: str) -> dict:
    """Fit headline glyphs and their background padding into the common safe area."""
    from .captions.layout import _font, _measure, _wrap, safe_rectangle

    style = CAPTION_STYLES.get(font_name, CAPTION_STYLES["bangers"])
    safe = safe_rectangle(width, height, style)
    pad = max(8, fontsize // 5)
    limit = safe["right"] - safe["left"] - 2 * pad
    for size in range(int(fontsize), 7, -1):
        font = _font(style, size)
        # Preserve authored newlines while safely wrapping each authored line.
        lines = [line for part in text.split("\n") for line in _wrap(part, font, limit, 4, 5).split("\n")]
        widths = [_measure(font, line, 5)[0] for line in lines]
        heights = [_measure(font, line, 5)[1] for line in lines]
        total_h = sum(heights) + 2 * pad * len(lines)
        if max(widths, default=0) + 2 * pad <= safe["right"] - safe["left"] and total_h <= safe["bottom"] - safe["top"]:
            left = (width - max(widths, default=0)) // 2
            return {"lines": lines, "fontsize": size, "widths": widths, "heights": heights,
                    "left": left - pad, "right": left + max(widths, default=0) + pad, "safe": safe, "pad": pad}
    return {"lines": [text[:1] or " "], "fontsize": 8, "widths": [1], "heights": [8],
            "left": safe["left"], "right": safe["left"] + 1, "safe": safe, "pad": 8}


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
    if not _ffmpeg_has_drawtext():
        raise RuntimeError(f"burn_titles: {_DRAWTEXT_REQUIRED_MSG}")

    out = Path(output_path or str(Path(video_path).with_stem(Path(video_path).stem + "_titled")))
    out.parent.mkdir(parents=True, exist_ok=True)

    from .ffmpeg_utils import probe_resolution
    w, h = probe_resolution(video_path) or (1080, 1920)
    s = CAPTION_STYLES.get(style, CAPTION_STYLES["bebas"])
    fontfile = s.get("fontfile")
    font_path = str(_FONTS_DIR / fontfile) if fontfile else s.get("system_font", "")

    filters = []
    for t in titles:
        laid = _layout_headline(t["text"], w, h, fontsize, style)
        start_s = float(t["start_s"])
        end_s = float(t["end_s"])
        y = max(laid["safe"]["top"], round(h * .20))
        for line, line_h in zip(laid["lines"], laid["heights"]):
            escaped = line.replace("\\", "\\\\").replace("'", "’").replace(":", "\\:")
            if s.get("uppercase"): escaped = escaped.upper()
            filters.append(f"drawtext=fontfile='{font_path}':text='{escaped}':fontsize={laid['fontsize']}:fontcolor=white:bordercolor=black:borderw=5:box=1:boxcolor=black@0.55:boxborderw={laid['pad']}:x=(w-tw)/2:y={y}:enable='gte(t,{start_s})*lt(t,{end_s})'")
            y += line_h + 2 * laid["pad"]

    result = run_ffmpeg(
        [_get_drawtext_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
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
    fade_in_s: float = 0.0,
) -> str:
    """Overlay a headline with a solid block background (Instagram/TikTok style).

    end_time_s: if set, hide the headline at this timestamp (use first scene's end_s
                so the headline is only visible during the intro).
    fade_in_s: optional explicit fade-in duration. Defaults to 0 so the headline is
               fully opaque on frame 0.
    bg_color / text_color accept any ffmpeg color string (e.g. 'white', 'black', '#FF0000').
    y_position is an ffmpeg expression for the TOP of the text block (default: 12% from top).
    font_name: one of bangers, impact, bebas, anton, clean (uses bundled fonts).
    Returns the output video path.
    """
    if not _ffmpeg_has_drawtext():
        raise RuntimeError(f"burn_headline: {_DRAWTEXT_REQUIRED_MSG}")

    out = Path(output_path or str(Path(video_path).with_stem(Path(video_path).stem + "_headline")))
    out.parent.mkdir(parents=True, exist_ok=True)

    from .ffmpeg_utils import probe_resolution
    w, h = probe_resolution(video_path) or (1080, 1920)
    style = CAPTION_STYLES.get(font_name, CAPTION_STYLES["bangers"])
    fontfile = style.get("fontfile")
    font_path = str(_FONTS_DIR / fontfile) if fontfile else style.get("system_font", "")

    enable_clause = f":enable='lt(t,{end_time_s})'" if end_time_s is not None else ""
    fade_in_s = max(0.0, float(fade_in_s))
    # Do not emit alpha at all for the default: this makes the frame-zero
    # opacity contract easy to audit and avoids an accidental alpha ramp.
    alpha_clause = f":alpha='min(1,t/{fade_in_s})'" if fade_in_s else ""
    laid = _layout_headline(text, w, h, fontsize, font_name)

    # One drawtext per line — each gets its own tight box (TikTok per-line style)
    filters = []
    # The default top placement is clamped to the measured safe rectangle.
    y = max(laid["safe"]["top"], min(laid["safe"]["bottom"] - sum(laid["heights"]) - 2 * laid["pad"] * len(laid["lines"]), round(h * .12)))
    for line, line_h in zip(laid["lines"], laid["heights"]):
        esc = line.replace("\\", "\\\\").replace("’", "’").replace("'", "’").replace(":", "\\:")
        if style.get("uppercase"):
            esc = esc.upper()
        filters.append(
            f"drawtext=fontfile='{font_path}'"
            f":text='{esc}'"
            f":fontsize={laid['fontsize']}"
            f":fontcolor={text_color}"
            f":box=1:boxcolor={bg_color}:boxborderw={laid['pad']}"
            f":x=(w-tw)/2"
            f":y={y}"
            f"{alpha_clause}"
            f"{enable_clause}"
        )
        y += line_h + 2 * laid["pad"]
    filter_str = ",".join(filters)

    result = run_ffmpeg(
        [_get_drawtext_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
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
