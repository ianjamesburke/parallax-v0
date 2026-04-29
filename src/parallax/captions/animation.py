"""Caption animation keyframe expansion.

`_expand_animation_keyframes` turns chunks + an animation profile into a
flat list of drawtext keyframes. Today the only animation type beyond
`none` is `pop` — multi-scale stamps within a short duration window.
"""

from __future__ import annotations

from ..log import get_logger

log = get_logger(__name__)


def _expand_animation_keyframes(
    chunks: list[dict],
    fontsize: int,
    animation: dict | None,
) -> list[dict]:
    """Expand chunks into drawtext keyframes per the style's animation profile.

    `animation` is the dict from `style["animation"]` (or a plan-level
    override). Supported types:

      - {"type": "none"} — passthrough; one drawtext per chunk at base size.
      - {"type": "pop", "duration_s": float, "scale_keys": [floats]} —
        emit one drawtext per scale_key, each holding for `duration_s / N`,
        with the final key extending to the chunk's natural end. drawtext
        cannot interpolate fontsize, so we step. Keep `duration_s` short
        (~50 ms) and `scale_keys` to 2 entries for snappy stamps; longer
        durations or 3+ keys read as deliberate growth.

    Chunks shorter than `duration_s` skip the animation and render at base
    size — there's no time for a meaningful pop.
    """
    if not animation or animation.get("type", "none") == "none":
        return [{**c, "fontsize": fontsize} for c in chunks]

    if animation["type"] != "pop":
        log.warning("Unknown caption animation type %r — falling back to static",
                    animation.get("type"))
        return [{**c, "fontsize": fontsize} for c in chunks]

    duration_s = float(animation.get("duration_s", 0.05))
    scale_keys = list(animation.get("scale_keys", [1.10, 1.0]))
    if not scale_keys or duration_s <= 0:
        return [{**c, "fontsize": fontsize} for c in chunks]

    step_s = duration_s / len(scale_keys)
    out: list[dict] = []
    for c in chunks:
        s, e = c["start"], c["end"]
        if (e - s) <= duration_s:
            out.append({**c, "fontsize": fontsize})
            continue
        # Each scale key gets a fixed slice of the animation window.
        # The LAST key extends to the chunk's natural end (the settle size).
        for i, scale in enumerate(scale_keys):
            seg_start = s + i * step_s
            seg_end = e if i == len(scale_keys) - 1 else s + (i + 1) * step_s
            out.append({
                "text": c["text"],
                "start": round(seg_start, 4),
                "end": round(seg_end, 4),
                "fontsize": max(1, int(round(fontsize * scale))),
            })
    return out
