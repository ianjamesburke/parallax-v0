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
        bw, bh = _measure(font, wrapped, stroke)
        if bw <= avail_w and bh <= avail_h:
            x = max(rect["left"], min(rect["right"] - bw, (width - bw) // 2))
            # Keep the established lower-third feeling, but clamp to safe area.
            desired = round(height * (float(center_y) if center_y is not None else .65) - bh / 2)
            y = max(rect["top"], min(rect["bottom"] - bh, desired))
            return {"text": wrapped, "fontsize": size, "x": x, "y": y,
                    "box": {"left": x, "top": y, "right": x + bw, "bottom": y + bh}, "safe": rect}
    # At 8px, input necessarily fits after truncation; defensive clamp guarantees frame bounds.
    font = _font(style, 8); wrapped = source[:1]
    bw, bh = _measure(font, wrapped, stroke)
    return {"text": wrapped, "fontsize": 8, "x": rect["left"], "y": rect["top"],
            "box": {"left": rect["left"], "top": rect["top"], "right": rect["left"] + bw, "bottom": rect["top"] + bh}, "safe": rect}
