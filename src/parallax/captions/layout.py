"""Measured, platform-safe caption layout shared by burn and text overlays."""
from __future__ import annotations

from pathlib import Path
from PIL import ImageFont

from .styles import _FONTS_DIR


def safe_rectangle(width: int, height: int, style: dict) -> dict[str, int]:
    """Return the inclusive text-safe rectangle in output pixels."""
    return {
        "left": round(width * float(style.get("safe_left", .08))),
        "right": round(width * (1 - float(style.get("safe_right", .08)))),
        "top": round(height * float(style.get("safe_top", .12))),
        "bottom": round(height * (1 - float(style.get("safe_bottom", .20)))),
    }


def _font(style: dict, size: int):
    name = style.get("fontfile")
    path = _FONTS_DIR / name if name else Path(style.get("system_font") or "")
    # System Impact is absent on Linux CI; bundled Montserrat is a faithful safe fallback.
    if not path.is_file(): path = _FONTS_DIR / "Montserrat-Black.ttf"
    return ImageFont.truetype(str(path), size)


def _measure(font, text: str, stroke: int) -> tuple[int, int]:
    # getbbox with stroke_width measures exactly the outline that ffmpeg draws.
    boxes = [font.getbbox(line or " ", stroke_width=stroke) for line in text.split("\n")]
    return max(b[2] - b[0] for b in boxes), sum(b[3] - b[1] for b in boxes)


def _visual_padding(style: dict) -> tuple[int, int]:
    """Extra pixels painted outside glyph bounds by the selected renderer."""
    pad = int(style.get("boxborderw", 0)) if style.get("box") else 0
    # drawtext's shadow is offset outwards from the glyph/outline.
    return pad + abs(int(style.get("shadowx", 0))), pad + abs(int(style.get("shadowy", 0)))


def _line_pitch(font, style: dict) -> int:
    """Return a collision-safe origin-to-origin distance for boxed lines.

    A glyph bbox is content-dependent: a line without descenders can be much
    shorter than the next line's box border.  Advancing by that bbox lets the
    next drawtext background paint over a preceding descender.  Font metrics
    describe the full line instead, and each independently drawn box needs its
    border/shadow clearance too.
    """
    ascent, descent = font.getmetrics()
    _, pad_y = _visual_padding(style)
    return ascent + descent + 2 * pad_y


def _wrap(text: str, font, limit: int, max_lines: int, stroke: int) -> str:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if _measure(font, candidate, stroke)[0] <= limit:
            current = candidate
        elif current and len(lines) < max_lines - 1:
            lines.append(current); current = word
        else:
            # A single very long token is split rather than allowed to bleed.
            if current: lines.append(current)
            current = word
    if current: lines.append(current)
    return "\n".join(lines[:max_lines])


def layout_caption(text: str, width: int, height: int, fontsize: int, style: dict, center_y: float | None = None) -> dict:
    """Wrap and shrink text until its outlined box is within the safe rectangle."""
    rect = safe_rectangle(width, height, style)
    avail_w = min(rect["right"] - rect["left"], round(width * float(style.get("max_line_width", .84))))
    avail_h = rect["bottom"] - rect["top"]
    stroke = int(style.get("borderw", 0))
    pad_x, pad_y = _visual_padding(style)
    # The safe-area promise includes drawtext's box border and shadow, not just
    # the glyphs Pillow happens to measure.
    avail_w -= 2 * pad_x
    # Preserve caller text; drawtext performs the preset's uppercase transform.
    # Fonts here are bold uppercase-friendly, and shrinking below adds margin.
    source = text
    for size in range(int(fontsize), 7, -1):
        font = _font(style, size)
        wrapped = _wrap(source, font, avail_w, int(style.get("max_lines", 2)), stroke)
        # Still too wide? Character-wrap the final long token before shrinking.
        if _measure(font, wrapped, stroke)[0] > avail_w:
            chars = []
            line = ""
            for char in source:
                if _measure(font, line + char, stroke)[0] > avail_w and line:
                    chars.append(line); line = char
                else: line += char
            if line: chars.append(line)
            wrapped = "\n".join(chars[:int(style.get("max_lines", 2))])
        lines = wrapped.split("\n")
        line_heights = [_measure(font, line, stroke)[1] for line in lines]
        bw = max(_measure(font, line, stroke)[0] for line in lines)
        # This is the physical union of the per-line drawtext boxes.  The
        # origins use a full metrics pitch; the last box ends at its actual
        # glyph height, exactly as the renderer below does.
        text_h = (len(lines) - 1) * _line_pitch(font, style) + line_heights[-1]
        box_h = text_h + 2 * pad_y
        if bw <= avail_w and box_h <= avail_h:
            x = max(rect["left"] + pad_x, min(rect["right"] - pad_x - bw, (width - bw) // 2))
            # Keep the established lower-third feeling, but clamp to safe area.
            desired = round(height * (float(center_y) if center_y is not None else .65) - box_h / 2 + pad_y)
            y = max(rect["top"] + pad_y, min(rect["bottom"] - pad_y - text_h, desired))
            return {"text": wrapped, "fontsize": size, "x": x, "y": y,
                    "line_pitch": _line_pitch(font, style),
                    "text_box": {"left": x, "top": y, "right": x + bw, "bottom": y + text_h},
                    "box": {"left": x - pad_x, "top": y - pad_y, "right": x + bw + pad_x, "bottom": y + text_h + pad_y}, "safe": rect}
    # At 8px, input necessarily fits after truncation; defensive clamp guarantees frame bounds.
    font = _font(style, 8); wrapped = source[:1]
    bw, bh = _measure(font, wrapped, stroke)
    x, y = rect["left"] + pad_x, rect["top"] + pad_y
    return {"text": wrapped, "fontsize": 8, "x": x, "y": y, "line_pitch": _line_pitch(font, style),
            "text_box": {"left": x, "top": y, "right": x + bw, "bottom": y + bh},
            "box": {"left": x - pad_x, "top": y - pad_y, "right": x + bw + pad_x, "bottom": y + bh + pad_y}, "safe": rect}
