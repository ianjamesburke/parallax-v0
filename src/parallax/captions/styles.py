"""Caption style presets and resolver.

`CAPTION_STYLES` holds the five shipped TikTok-native presets (bangers,
impact, bebas, anton, clean). `resolve_caption_style` is the inline-style
entry point: an agent or plan can pass a string preset name, an inline
dict (with optional `base: <preset>` for partial override), or a fully
custom dict and get back a fully-merged style ready to feed into the
drawtext / Pillow caption backends.
"""

from __future__ import annotations

from pathlib import Path

_FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"

# Five TikTok-native caption styles. Each is applied by both the drawtext and
# Pillow code paths, so they stay visually consistent regardless of backend.
# Each preset is a self-contained recipe — visual properties + animation profile.
# Animations: {"type": "none"} or {"type": "pop", "duration_s": float, "scale_keys": [floats]}.
# `pop` stamps the chunk at each scale_key in order, splitting `duration_s` evenly across
# the keys (final key = settle size, usually 1.0). Keep `duration_s` ≤ 0.08 for snappy.
CAPTION_STYLES: dict[str, dict] = {
    "bangers": {
        # Kill Tony style — chunky, heavy stroke, all-caps. Pops on each chunk.
        # x_expr uses centered text-width; Bangers has a slight rightward italic slant.
        "fontfile": "Bangers-Regular.ttf",
        "fontcolor": "white",
        "bordercolor": "black",
        "borderw": 6,
        "shadowx": 0,
        "shadowy": 0,
        "box": False,
        "uppercase": True,
        "x_expr": "(w-tw)/2",
        "y_expr": "h*65/100-th",
        "animation": {"type": "pop", "duration_s": 0.05, "scale_keys": [1.10, 1.0]},
    },
    "impact": {
        # Classic meme — system Impact, thin outline. Static (no animation).
        "fontfile": None,
        "system_font": "/Library/Fonts/Impact.ttf",
        "fontcolor": "white",
        "bordercolor": "black",
        "borderw": 3,
        "shadowx": 0,
        "shadowy": 0,
        "box": False,
        "uppercase": True,
        "x_expr": "(w-tw)/2",
        "y_expr": "h*65/100-th",
        "animation": {"type": "none"},
    },
    "bebas": {
        # Viral TikTok — Bebas Neue, electric yellow, thick stroke. Quick pop.
        "fontfile": "BebasNeue-Regular.ttf",
        "fontcolor": "#FFE600",
        "bordercolor": "black",
        "borderw": 5,
        "shadowx": 0,
        "shadowy": 0,
        "box": False,
        "uppercase": True,
        "x_expr": "(w-tw)/2",
        "y_expr": "h*65/100-th",
        "animation": {"type": "pop", "duration_s": 0.05, "scale_keys": [1.12, 1.0]},
    },
    "anton": {
        # Bold podcast — Anton, white with soft drop shadow. Static.
        "fontfile": "Anton-Regular.ttf",
        "fontcolor": "white",
        "bordercolor": "black",
        "borderw": 2,
        "shadowx": 4,
        "shadowy": 4,
        "box": False,
        "uppercase": True,
        "x_expr": "(w-tw)/2",
        "y_expr": "h*65/100-th",
        "animation": {"type": "none"},
    },
    "clean": {
        # Modern/clean — Montserrat Black, white on semi-transparent dark pill. Static.
        "fontfile": "Montserrat-Black.ttf",
        "fontcolor": "white",
        "bordercolor": None,
        "borderw": 0,
        "shadowx": 0,
        "shadowy": 0,
        "box": True,
        "boxcolor": "black@0.55",
        "boxborderw": 20,
        "uppercase": False,
        "x_expr": "(w-tw)/2",
        "y_expr": "h*65/100-th",
        "animation": {"type": "none"},
    },
}


def resolve_caption_style(spec: str | dict | None) -> dict:
    """Resolve a `caption_style` plan value to a fully-merged style dict.

    Accepts:
      - str: looks up preset by name (falls back to `bangers` if unknown)
      - dict with `base: <preset>`: merges the preset under the supplied keys
      - dict without `base`: treated as a fully custom style (must include
        all required visual fields — fontfile or system_font, fontcolor, etc.)
      - None: returns the `bangers` preset

    This is the inline-style entry point: an agent (or a one-off plan) can
    synthesize a custom style dict and pass it directly without registering
    a new preset in CAPTION_STYLES.
    """
    if spec is None:
        return dict(CAPTION_STYLES["bangers"])
    if isinstance(spec, str):
        return dict(CAPTION_STYLES.get(spec, CAPTION_STYLES["bangers"]))
    if isinstance(spec, dict):
        base_name = spec.get("base")
        if base_name:
            base = dict(CAPTION_STYLES.get(base_name, CAPTION_STYLES["bangers"]))
        else:
            base = {}
        merged = {**base, **{k: v for k, v in spec.items() if k != "base"}}
        return merged
    raise TypeError(f"caption_style must be str, dict, or None — got {type(spec).__name__}")
