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
from .chunker import _fixed_chunk_words, _smart_chunk_words
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
) -> str:
    """Burn word-by-word captions onto a video.

    Tries ffmpeg drawtext first. Falls back to Pillow frame-by-frame rendering
    when ffmpeg lacks libfreetype (e.g. minimal Homebrew builds).

    words_json: JSON string of [{word, start, end}] or path to vo_words.json
    words_per_chunk: int N for at most N-word chunks, or "smart" (default) to
        group adjacent words while their combined letter count stays ≤4.
        Both modes end chunks at sentence/phrase punctuation; punctuation is retained.
    caption_style: preset name (bangers/impact/bebas/anton/clean), an inline
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
    from .layout import layout_caption
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

    # Group into chunks
    if isinstance(words_per_chunk, str) and words_per_chunk.lower() == "smart":
        chunks: list[dict] = _smart_chunk_words(words, max_letters=4)
    else:
        chunks = _fixed_chunk_words(words, max_words=int(words_per_chunk))

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

    keyed = _expand_animation_keyframes(chunks, fontsize, anim)
    resolution = probe_resolution(video_path) or (720, 1280)
    boxes = []
    for c in keyed:
        layout = layout_caption(c["text"], *resolution, int(c.get("fontsize", fontsize)), style)
        c.update(layout)
        boxes.append({"start": c["start"], "end": c["end"], "text": layout["text"],
                      "box": layout["box"], "safe": layout["safe"]})
    if report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(json.dumps({"resolution": list(resolution), "boxes": boxes}, indent=2))

    _burn_captions_drawtext(video_path, keyed, out, fontsize, style)

    log.info("burn_captions: wrote %s", out)
    return str(out)
