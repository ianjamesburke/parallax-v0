"""Caption style presets and resolver.

`CAPTION_STYLES` holds the shipped presets (bangers, impact, bebas, anton,
clean, karaoke, soft). `resolve_caption_style` is the inline-style
entry point: an agent or plan can pass a string preset name, an inline
dict (with optional `base: <preset>` for partial override), or a fully
custom dict and get back a fully-merged style ready to feed into the
drawtext / Pillow caption backends.
"""

from __future__ import annotations

from pathlib import Path

from ..log import get_logger

log = get_logger(__name__)

_FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"

# TikTok-native caption styles. Each is applied by both the drawtext and
# Pillow code paths, so they stay visually consistent regardless of backend.
# Each preset is a self-contained recipe — visual properties + animation profile.
# Animations: {"type": "none"} or {"type": "pop", "duration_s": float, "scale_keys": [floats]}.
# `pop` stamps the chunk at each scale_key in order, splitting `duration_s` evenly across
# the keys (final key = settle size, usually 1.0). Keep `duration_s` ≤ 0.08 for snappy.
# Owner rule (2026-11-01): captions are ALWAYS one line on screen, usually 1-2
# words, 3 at most. Every preset shares these defaults; inline styles may still
# override them (e.g. `{"base": "karaoke", "max_words": 4, "max_lines": 2}`).
#   max_lines              — physical lines per caption; 1 = never wrap. A chunk
#                            too wide for one line is split into more chunks.
#   max_words              — hard cap on words per on-screen chunk; a larger
#                            integer `words_per_chunk` is clamped to it.
#   three_word_max_letters — a 3rd (or later) word joins a chunk only if the
#                            chunk's letters (alphanumerics, punctuation
#                            ignored) stay within this budget, so 3-word cards
#                            are reserved for short words ("is a gel").
DEFAULT_MAX_LINES = 1
DEFAULT_MAX_WORDS = 3
DEFAULT_THREE_WORD_MAX_LETTERS = 12
_SINGLE_LINE = {
    "max_lines": DEFAULT_MAX_LINES,
    "max_words": DEFAULT_MAX_WORDS,
    "three_word_max_letters": DEFAULT_THREE_WORD_MAX_LETTERS,
}

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
        "safe_left": 0.08, "safe_right": 0.08, "safe_top": 0.12, "safe_bottom": 0.20,
        "max_line_width": 0.84, **_SINGLE_LINE,
        "animation": {"type": "pop", "duration_s": 0.05, "scale_keys": [1.10, 1.0]},
    },
    "impact": {
        # Classic meme — system Impact, thin outline. Static (no animation).
        "fontfile": None,
        "system_font": "/Library/Fonts/Impact.ttf",
        # Used (with a warning) when the Mac-only system font is missing.
        "fallback_fontfile": "Anton-Regular.ttf",
        "fontcolor": "white",
        "bordercolor": "black",
        "borderw": 3,
        "shadowx": 0,
        "shadowy": 0,
        "box": False,
        "uppercase": True,
        "x_expr": "(w-tw)/2",
        "y_expr": "h*65/100-th",
        "safe_left": 0.08, "safe_right": 0.08, "safe_top": 0.12, "safe_bottom": 0.20,
        "max_line_width": 0.84, **_SINGLE_LINE,
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
        "safe_left": 0.08, "safe_right": 0.08, "safe_top": 0.12, "safe_bottom": 0.20,
        "max_line_width": 0.84, **_SINGLE_LINE,
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
        "safe_left": 0.08, "safe_right": 0.08, "safe_top": 0.12, "safe_bottom": 0.20,
        "max_line_width": 0.84, **_SINGLE_LINE,
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
        "safe_left": 0.08, "safe_right": 0.08, "safe_top": 0.12, "safe_bottom": 0.20,
        "max_line_width": 0.84, **_SINGLE_LINE,
        "animation": {"type": "none"},
    },
    "karaoke": {
        # Karaoke — Montserrat ExtraBold caps, white with black stroke. Words are
        # drawn one by one; the word being spoken sits on a filled highlight box
        # (highlight_color / highlight_padding / highlight_text_color). Static.
        "fontfile": "Montserrat-ExtraBold.ttf",
        "fontcolor": "white",
        "bordercolor": "black",
        "borderw": 4,
        "shadowx": 0,
        "shadowy": 0,
        "box": False,
        "uppercase": True,
        "highlight_color": "0xE8457A",
        "highlight_padding": 10,
        "x_expr": "(w-tw)/2",
        "y_expr": "h*65/100-th",
        "safe_left": 0.08, "safe_right": 0.08, "safe_top": 0.12, "safe_bottom": 0.20,
        "max_line_width": 0.84, **_SINGLE_LINE,
        "animation": {"type": "none"},
    },
    "soft": {
        # Soft — Montserrat SemiBold lowercase, no box/stroke, one narrow line and
        # a blurred drop shadow (shadow_blur is a gblur sigma; 0 = hard shadow). Static.
        # max_line_width 0.72 (was 0.62 when soft wrapped to two lines): still
        # narrower than the 0.84 presets, but wide enough that ordinary two-word
        # chunks stay together instead of being width-split into one-word cards.
        "fontfile": "Montserrat-SemiBold.ttf",
        "fontcolor": "white",
        "bordercolor": None,
        "borderw": 0,
        "shadowx": 0,
        "shadowy": 4,
        "shadow_blur": 7,
        "shadow_color": "black",
        "shadow_opacity": 0.9,
        "box": False,
        "uppercase": False,
        "lowercase": True,
        "x_expr": "(w-tw)/2",
        "y_expr": "h*65/100-th",
        "safe_left": 0.08, "safe_right": 0.08, "safe_top": 0.12, "safe_bottom": 0.20,
        "max_line_width": 0.72, **_SINGLE_LINE,
        "animation": {"type": "none"},
    },
}


def _unknown_preset(name: object) -> ValueError:
    return ValueError(
        f"Unknown caption style {name!r}. Valid presets: {', '.join(CAPTION_STYLES)} "
        "(or pass an inline style dict, optionally with `base: <preset>`)."
    )


def resolve_caption_style(spec: str | dict | None) -> dict:
    """Resolve a `caption_style` plan value to a fully-merged style dict.

    Accepts:
      - str: looks up preset by name; unknown names raise ValueError listing
        the valid presets
      - dict with `base: <preset>`: merges the preset under the supplied keys
        (unknown `base` raises ValueError)
      - dict without `base`: treated as a fully custom style (must include
        all required visual fields — fontfile or system_font, fontcolor, etc.)
      - None: returns the `bangers` preset

    Case options: `uppercase` and `lowercase` are both honoured by the
    renderer; if both are true, `lowercase` wins.

    This is the inline-style entry point: an agent (or a one-off plan) can
    synthesize a custom style dict and pass it directly without registering
    a new preset in CAPTION_STYLES.
    """
    if spec is None:
        return dict(CAPTION_STYLES["bangers"])
    if isinstance(spec, str):
        if spec not in CAPTION_STYLES:
            raise _unknown_preset(spec)
        return dict(CAPTION_STYLES[spec])
    if isinstance(spec, dict):
        base_name = spec.get("base")
        if base_name:
            if base_name not in CAPTION_STYLES:
                raise _unknown_preset(base_name)
            base = dict(CAPTION_STYLES[base_name])
        else:
            base = {}
        merged = {**base, **{k: v for k, v in spec.items() if k != "base"}}
        return merged
    raise TypeError(f"caption_style must be str, dict, or None — got {type(spec).__name__}")


def apply_case(style: dict, text: str) -> str:
    """Apply the style's case option to `text` (`lowercase` wins over `uppercase`)."""
    if style.get("lowercase"):
        return text.lower()
    if style.get("uppercase"):
        return text.upper()
    return text


_warned_fonts: set[str] = set()


def resolve_font_path(style: dict) -> Path:
    """Return an existing font file for `style`, falling back to a bundled bold font.

    Order: `fontfile` (bundled name or absolute path), then `system_font`,
    then the style's `fallback_fontfile` (default Montserrat-Black). Falls back
    with a one-time warning rather than letting ffmpeg use its thin default font.
    """
    name = style.get("fontfile")
    path = _FONTS_DIR / name if name else Path(style.get("system_font") or "")
    if path.is_file():
        return path
    fallback = _FONTS_DIR / (style.get("fallback_fontfile") or "Montserrat-Black.ttf")
    if str(path) not in _warned_fonts:
        _warned_fonts.add(str(path))
        log.warning("caption font %s not found — falling back to bundled %s", path or "(none)", fallback.name)
    return fallback
