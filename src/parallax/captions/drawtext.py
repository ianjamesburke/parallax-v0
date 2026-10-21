"""ffmpeg drawtext caption backend.

Builds drawtext filter strings from a resolved style dict + chunk list,
then invokes ffmpeg to burn captions onto the source video. This is the
fast path; falls back to `pillow.py` when drawtext isn't available.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .styles import _FONTS_DIR
from .layout import _font, _measure
from ..ffmpeg_utils import run_ffmpeg


def _style_drawtext_filter(
    style: dict,
    text: str,
    start: float,
    end: float,
    fontsize: int,
) -> str:
    # Escape order matters: backslashes first, then other special chars.
    # Reversing this order would double-escape the backslashes inserted by later steps.
    # `%` starts a drawtext expansion (`%{...}`), so a literal one must reach drawtext as `\%`.
    # The filtergraph parser strips one backslash level first, hence two here.
    escaped = (
        text.replace("\\", "\\\\").replace("'", "’").replace(":", "\\:").replace("%", "\\\\%").replace("\n", " ")
    )
    if style.get("uppercase"):
        escaped = escaped.upper()

    fontfile = style.get("fontfile")
    font_path = str(_FONTS_DIR / fontfile) if fontfile else style.get("system_font", "")

    x_expr = str(style.get("x", style.get("x_expr", "(w-tw)/2")))
    y_expr = str(style.get("y", style.get("y_expr", "h*0.65-th")))

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
    from ..ffmpeg_utils import _get_drawtext_ffmpeg

    # Each chunk may have its own per-keyframe fontsize set by
    # `_expand_pop_keyframes`; fall back to the global fontsize for
    # chunks without one (animation disabled).
    filters: list[str] = []
    for c in chunks:
        size = int(c.get("fontsize", fontsize))
        lines = c["text"].split("\n")
        # A physical drawtext instance per line is deliberate: the filtergraph
        # parser consumes newline escapes before drawtext receives them. It also
        # makes this renderer use the exact same wrapped lines as layout_caption.
        font = _font(style, size)
        widths = [_measure(font, line, int(style.get("borderw", 0)))[0] for line in lines]
        line_heights = [_measure(font, line, int(style.get("borderw", 0)))[1] for line in lines]
        widest = max(widths)
        y = c.get("y", style.get("y_expr", "h*.65-th"))
        for i, line in enumerate(lines):
            if "x" in c:
                x = int(c["x"]) + (widest - widths[i]) // 2
            else:
                x = style.get("x_expr", "(w-tw)/2")
            line_y = y if i == 0 else (int(c["y"]) + sum(line_heights[:i]) if "y" in c else y)
            filters.append(_style_drawtext_filter({**style, "x": x, "y": line_y}, line, c["start"], c["end"], size))
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
        raise RuntimeError(f"burn_captions (drawtext) failed:\n{result.stderr[:500]}")
