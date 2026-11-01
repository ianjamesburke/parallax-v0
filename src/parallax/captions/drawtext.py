"""ffmpeg drawtext caption backend.

Builds drawtext filter strings from a resolved style dict + chunk list,
then invokes ffmpeg to burn captions onto the source video. This is the
fast path; falls back to `pillow.py` when drawtext isn't available.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .styles import apply_case, resolve_font_path
from .layout import _font, _line_pitch, _measure
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
    escaped = apply_case(style, escaped)

    font_path = str(resolve_font_path(style))

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
            f"shadowcolor={style.get('shadow_color', 'black')}@{style.get('shadow_opacity', 0.7)}",
        ]
    if style.get("box"):
        kv += ["box=1", f"boxcolor={style['boxcolor']}", f"boxborderw={style.get('boxborderw', 10)}"]
    kv += [f"x={x_expr}", f"y={y_expr}", f"enable='gte(t,{start})*lt(t,{end})'"]
    return "drawtext=" + ":".join(kv)


def _highlight_drawtext_filter(style: dict, text: str, start: float, end: float, fontsize: int) -> str:
    """Active-word drawtext: filled highlight box behind plain text (no stroke/shadow)."""
    return _style_drawtext_filter(
        {**style, "borderw": 0, "bordercolor": None, "shadowx": 0, "shadowy": 0,
         "box": True, "boxcolor": style.get("highlight_color", "0xE8457A"),
         "boxborderw": int(style.get("highlight_padding", 10)),
         "fontcolor": style.get("highlight_text_color", style["fontcolor"])},
        text, start, end, fontsize)


def _word_windows(c: dict, n: int) -> list[tuple[float, float]] | None:
    """Highlight window per word: from its start to the next word's start.

    The first word starts at the chunk start and the last runs to the chunk
    end, so the highlight stays on the last-spoken word across gaps. Windows
    are clipped to the chunk (animation keyframes cover sub-ranges).
    """
    words = c.get("words")
    if not words or len(words) != n:
        return None
    starts = [c["start"]] + [float(w["start"]) for w in words[1:]]
    ends = starts[1:] + [c["end"]]
    out = []
    for s0, e0 in zip(starts, ends):
        out.append((round(max(s0, c["start"]), 3), round(min(e0, c["end"]), 3)))
    return out


def _chunk_filters(c: dict, style: dict, fontsize: int) -> tuple[list[dict], list[str]]:
    """Return (base text items, highlight filters) for one chunk.

    Items are {text, x, y, start, end, size}; base items carry the crisp text.
    """
    size = int(c.get("fontsize", fontsize))
    lines = c["text"].split("\n")
    # A physical drawtext instance per line is deliberate: the filtergraph
    # parser consumes newline escapes before drawtext receives them. It also
    # makes this renderer use the exact same wrapped lines as layout_caption.
    font = _font(style, size)
    cased = [apply_case(style, line) for line in lines]
    widths = [_measure(font, line, int(style.get("borderw", 0)))[0] for line in cased]
    widest = max(widths)
    # Never use a glyph-specific bbox for the next origin: with boxed
    # captions that allows the next box background to cover descenders.
    pitch = int(c.get("line_pitch", _line_pitch(font, style)))
    y = c.get("y", style.get("y_expr", "h*.65-th"))
    tokens = [line.split() for line in lines]
    windows = _word_windows(c, sum(len(t) for t in tokens)) if style.get("highlight_color") and "x" in c else None
    items: list[dict] = []
    highlights: list[str] = []
    k = 0
    for i, line in enumerate(lines):
        line_y = y if i == 0 else (int(c["y"]) + i * pitch if "y" in c else y)
        if windows is None:
            x = int(c["x"]) + (widest - widths[i]) // 2 if "x" in c else style.get("x_expr", "(w-tw)/2")
            items.append({"text": line, "x": x, "y": line_y, "start": c["start"], "end": c["end"], "size": size})
            continue
        # Karaoke: one drawtext per word, positioned from font advances and
        # centred as a group on the same centre line the layout chose.
        ftoks = [apply_case(style, t) for t in tokens[i]]
        gap = font.getlength(" ")
        adv = [font.getlength(t) for t in ftoks]
        x = int(c["x"]) + widest / 2 - (sum(adv) + gap * (len(adv) - 1)) / 2
        for tok, a in zip(tokens[i], adv):
            items.append({"text": tok, "x": round(x), "y": line_y, "start": c["start"], "end": c["end"], "size": size})
            ws, we = windows[k]
            if we > ws:
                highlights.append(_highlight_drawtext_filter({**style, "x": round(x), "y": line_y}, tok, ws, we, size))
            k += 1
            x += a + gap
    return items, highlights


def _write_if_long(args: list[str], flag: str, graph: str, script_flag: str, tmp: list[str]) -> None:
    """Pass a long filtergraph via a script file (argv strings cap at ~128KB)."""
    if len(graph) < 60000:
        args += [flag, graph]
        return
    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    f.write(graph); f.close()
    tmp.append(f.name)
    args += [script_flag, f.name]


def _burn_captions_drawtext(
    video_path: str,
    chunks: list[dict],
    out: Path,
    fontsize: int,
    style: dict,
) -> None:
    import os
    from ..ffmpeg_utils import _get_drawtext_ffmpeg, _probe_fps, probe_resolution

    blur = float(style.get("shadow_blur", 0) or 0)
    soft = blur > 0 and bool(style.get("shadowx") or style.get("shadowy"))
    # Each chunk may have its own per-keyframe fontsize set by
    # `_expand_pop_keyframes`; fall back to the global fontsize for
    # chunks without one (animation disabled).
    crisp_style = {**style, "shadowx": 0, "shadowy": 0} if soft else style
    crisp: list[str] = []
    shadow: list[str] = []
    top: list[str] = []
    for c in chunks:
        items, highlights = _chunk_filters(c, style, fontsize)
        for it in items:
            crisp.append(_style_drawtext_filter({**crisp_style, "x": it["x"], "y": it["y"]},
                                                it["text"], it["start"], it["end"], it["size"]))
            if soft:
                color = f"{style.get('shadow_color', 'black')}@{style.get('shadow_opacity', 0.9)}"
                shadow.append(_style_drawtext_filter(
                    {**style, "fontcolor": color, "bordercolor": color, "box": False,
                     "shadowx": 0, "shadowy": 0,
                     "x": int(it["x"]) + int(style.get("shadowx", 0)) if isinstance(it["x"], int) else
                          f"({it['x']})+{int(style.get('shadowx', 0))}",
                     "y": int(it["y"]) + int(style.get("shadowy", 0)) if isinstance(it["y"], int) else
                          f"({it['y']})+{int(style.get('shadowy', 0))}"},
                    it["text"], it["start"], it["end"], it["size"]))
        top += highlights
    args = [_get_drawtext_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", video_path]
    tmp: list[str] = []
    if soft:
        # Shadow text goes on a transparent layer, is blurred, then overlaid under
        # the crisp text. The layer is a lavfi colour source of the video's size.
        w, h = probe_resolution(video_path) or (720, 1280)
        dur = _probe_duration(video_path) + 1.0
        fps = _probe_fps(video_path)
        graph = (f"color=c=black@0.0:s={w}x{h}:r={fps}:d={dur:.3f},format=rgba,"
                 + ",".join(shadow) + f",gblur=sigma={blur}[sh];"
                 "[0:v][sh]overlay=0:0:shortest=1," + ",".join(crisp + top))
        _write_if_long(args, "-filter_complex", graph, "-filter_complex_script", tmp)
    else:
        _write_if_long(args, "-vf", ",".join(crisp + top), "-filter_script:v", tmp)
    args += ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "copy", str(out)]
    try:
        result = run_ffmpeg(args, capture_output=True, text=True)
    finally:
        for f in tmp:
            os.unlink(f)
    if result.returncode != 0:
        raise RuntimeError(f"burn_captions (drawtext) failed:\n{result.stderr[:500]}")


def _probe_duration(path: str) -> float:
    r = run_ffmpeg(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 3600.0
