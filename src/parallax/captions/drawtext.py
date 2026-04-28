"""ffmpeg drawtext caption backend.

Builds drawtext filter strings from a resolved style dict + chunk list,
then invokes ffmpeg to burn captions onto the source video. This is the
fast path; falls back to `pillow.py` when drawtext isn't available.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .styles import _FONTS_DIR


def _style_drawtext_filter(
    style: dict,
    text: str,
    start: float,
    end: float,
    fontsize: int,
) -> str:
    # Escape order matters: backslashes first, then other special chars.
    # Reversing this order would double-escape the backslashes inserted by later steps.
    escaped = text.replace("\\", "\\\\").replace("'", "’").replace(":", "\\:")
    if style.get("uppercase"):
        escaped = escaped.upper()

    fontfile = style.get("fontfile")
    font_path = str(_FONTS_DIR / fontfile) if fontfile else style.get("system_font", "")

    x_expr = style.get("x_expr", "(w-tw)/2")
    y_expr = style.get("y_expr", "h*0.65-th")

    kv: list[str] = [
        f"fontfile='{font_path}'",
        f"text='{escaped}'",
        f"fontsize={fontsize}",
        f"fontcolor={style['fontcolor']}",
    ]
    if style.get("borderw") and style.get("bordercolor"):
        kv += [f"bordercolor={style['bordercolor']}", f"borderw={style['borderw']}"]
    if style.get("shadowx") or style.get("shadowy"):
        kv += [
            f"shadowx={style.get('shadowx', 0)}",
            f"shadowy={style.get('shadowy', 0)}",
            "shadowcolor=black@0.7",
        ]
    if style.get("box"):
        kv += ["box=1", f"boxcolor={style['boxcolor']}", f"boxborderw={style.get('boxborderw', 10)}"]
    kv += [f"x={x_expr}", f"y={y_expr}", f"enable='gte(t,{start})*lt(t,{end})'"]
    return "drawtext=" + ":".join(kv)


def _burn_captions_drawtext(
    video_path: str,
    chunks: list[dict],
    out: Path,
    fontsize: int,
    style: dict,
) -> None:
    from ..ffmpeg_utils import _get_ffmpeg

    # Each chunk may have its own per-keyframe fontsize set by
    # `_expand_pop_keyframes`; fall back to the global fontsize for
    # chunks without one (animation disabled).
    filters = [
        _style_drawtext_filter(
            style, c["text"], c["start"], c["end"],
            int(c.get("fontsize", fontsize)),
        )
        for c in chunks
    ]
    result = subprocess.run(
        [_get_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", video_path,
         "-vf", ",".join(filters),
         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
         "-c:a", "copy",
         str(out)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"burn_captions (drawtext) failed:\n{result.stderr[:500]}")
