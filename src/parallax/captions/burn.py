"""High-level caption burn orchestration.

`burn_captions` is the entry point: it loads words, builds chunks via the
chunker, applies gap-hold + global timing shift, expands the style's
animation profile into keyframes, then dispatches to the drawtext backend.

Requires a drawtext-capable ffmpeg (one built with libfreetype). The
installer provides this via ffmpeg-full on macOS. Raises RuntimeError if
the available ffmpeg lacks drawtext support.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..log import get_logger
from .animation import _expand_animation_keyframes
from .chunker import chunk_words, split_chunks_to_fit
from .drawtext import _burn_captions_drawtext
from .styles import resolve_caption_style

log = get_logger(__name__)


def burn_captions(
    video_path: str,
    words_json: str,
    output_path: str | None = None,
    words_per_chunk: int | str = "smart",
    fontsize: int = 55,
    caption_style: str | dict = "bangers",
    gap_hold_frames: int = 15,
    animation_override: str | dict | None = None,
    shift_s: float = 0.0,
    report_path: str | None = None,
    caption_y: float = .65,
) -> str:
    """Burn word-by-word captions onto a video.

    Tries ffmpeg drawtext first. Falls back to Pillow frame-by-frame rendering
    when ffmpeg lacks libfreetype (e.g. minimal Homebrew builds).

    words_json: JSON string of [{word, start, end}] or path to vo_words.json
    words_per_chunk: "smart" (default) or an int N. Captions are always ONE
        line of 1-2 words (3 max): a 3rd word joins only when at most one word
        of the chunk is a content word (the rest are small function words such
        as a/is/of/what/here's; "Here's what a", "Filler is a") and the chunk
        has ≤ `three_word_max_letters` (default 14) letters. 1 or 2 cap lower;
        3 behaves like "smart"; larger N clamps to the style's `max_words`
        (default 3). Style `three_word_rule` "letters" / "any" swaps the
        function-word test. Chunks end at sentence/phrase punctuation, which is
        retained. Any
        multi-word chunk too wide for one line at `fontsize` is split into
        more chunks, each timed from its own words. Only a lone over-wide word
        shrinks. Inline styles may override `max_words` / `max_lines` / `three_word_rule`.
    caption_style: preset name (bangers/impact/bebas/anton/clean/karaoke/soft;
        unknown names raise ValueError listing the presets), an inline
        dict with optional `base: <preset>` for partial override, or a fully
        custom dict. Style carries its own animation profile — see
        `resolve_caption_style` and `CAPTION_STYLES`.
    gap_hold_frames: if the gap to the next caption is smaller than this many
        frames, extend the current caption to butt up against the next one.
    animation_override: explicitly disable or replace the style's animation.
        `"none"` disables; a dict (e.g. `{"type": "pop", "duration_s": 0.04,
        "scale_keys": [1.15, 1.0]}`) replaces it wholesale.
    shift_s: seconds to shift every caption start/end. Negative makes captions
        appear earlier (use to compensate for residual detector lag); positive
        delays them. Applied AFTER smart-chunking and gap-hold.
    Returns captioned video path.
    """
    from ..ffmpeg_utils import _ffmpeg_has_drawtext, _probe_fps, probe_resolution
    from .layout import fits_one_line, layout_caption
    if not _ffmpeg_has_drawtext():
        raise RuntimeError(
            "burn_captions: ffmpeg on PATH lacks drawtext support (libfreetype). "
            "On macOS run: brew install ffmpeg-full. "
            "On Linux run: sudo apt install ffmpeg (Ubuntu/Debian ships with freetype enabled)."
        )

    wjson_path = Path(words_json)
    if wjson_path.exists():
        payload = json.loads(wjson_path.read_text())
        words: list[dict] = payload if isinstance(payload, list) else payload.get("words", [])
    else:
        payload = json.loads(words_json)
        words = payload if isinstance(payload, list) else payload.get("words", [])

    if not words:
        log.warning("burn_captions: no words, returning original video")
        return video_path

    out = Path(output_path or str(Path(video_path).with_stem(Path(video_path).stem + "_captioned")))
    out.parent.mkdir(parents=True, exist_ok=True)

    style = resolve_caption_style(caption_style)
    # Animation comes from the style preset by default. Plan-level
    # `animation_override` can disable it (`"none"`) or replace it (dict).
    if animation_override is not None:
        if isinstance(animation_override, str) and animation_override.lower() == "none":
            anim = {"type": "none"}
        elif isinstance(animation_override, dict):
            anim = animation_override
        else:
            raise ValueError(
                f"animation_override must be 'none' or a dict — got {animation_override!r}"
            )
    else:
        anim = style.get("animation", {"type": "none"})
    resolution = probe_resolution(video_path) or (720, 1280)

    # Group into chunks, then split any multi-word chunk that would not fit on
    # one line. Measure at the largest pop keyframe so the stamp never shrinks.
    chunks: list[dict] = chunk_words(words, words_per_chunk, style)
    if int(style.get("max_lines", 1)) == 1:
        scale = max([1.0] + [float(k) for k in anim.get("scale_keys", [])]) if anim.get("type") == "pop" else 1.0
        fit_size = max(1, int(round(fontsize * scale)))
        chunks = split_chunks_to_fit(chunks, lambda text: fits_one_line(text, *resolution, fit_size, style))

    # Extend chunk end to the next chunk's start when the gap is smaller than
    # gap_hold_frames, so there's no blank flash between adjacent captions.
    if gap_hold_frames > 0 and len(chunks) > 1:
        fps = _probe_fps(video_path)
        threshold_s = gap_hold_frames / fps
        for i in range(len(chunks) - 1):
            gap = chunks[i + 1]["start"] - chunks[i]["end"]
            if gap < threshold_s:
                chunks[i]["end"] = chunks[i + 1]["start"]

    # Apply global timing shift (compensation for detector lag, etc.).
    # `max(0, ...)` clamps any pre-zero starts that would otherwise crash drawtext.
    if shift_s:
        for c in chunks:
            c["start"] = max(0.0, round(c["start"] + shift_s, 3))
            c["end"] = max(c["start"] + 0.05, round(c["end"] + shift_s, 3))
            if c.get("words"):
                c["words"] = [{**w, "start": max(0.0, round(w["start"] + shift_s, 3)),
                               "end": max(0.0, round(w["end"] + shift_s, 3))} for w in c["words"]]

    keyed = _expand_animation_keyframes(chunks, fontsize, anim)
    boxes = []
    for c in keyed:
        layout = layout_caption(c["text"], *resolution, int(c.get("fontsize", fontsize)), style, center_y=caption_y)
        c.update(layout)
        box, safe = layout["box"], layout["safe"]
        boxes.append({"start": c["start"], "end": c["end"], "text": layout["text"], "fontsize": c.get("fontsize", fontsize),
                      "box": box, "safe": safe,
                      "inside_safe": box["left"] >= safe["left"] and box["right"] <= safe["right"] and box["top"] >= safe["top"] and box["bottom"] <= safe["bottom"],
                      "inside_frame": box["left"] >= 0 and box["top"] >= 0 and box["right"] <= resolution[0] and box["bottom"] <= resolution[1]})
    if report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(json.dumps({"resolution": list(resolution), "boxes": boxes}, indent=2))

    _burn_captions_drawtext(video_path, keyed, out, fontsize, style)

    log.info("burn_captions: wrote %s", out)
    return str(out)
