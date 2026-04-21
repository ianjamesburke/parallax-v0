from __future__ import annotations

import hashlib
import os
import textwrap
from pathlib import Path

from typing import Any

from PIL import Image, ImageDraw, ImageFont


def is_test_mode() -> bool:
    return os.environ.get("PARALLAX_TEST_MODE", "").lower() in ("1", "true", "yes")


def output_dir() -> Path:
    return Path(os.environ.get("PARALLAX_OUTPUT_DIR", "output"))


def _load_font(size: int) -> Any:
    for candidate in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(candidate):
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def render_mock_image(prompt: str, model: str, out_dir: Path | None = None) -> Path:
    """Render a PNG that contains the request parameters as readable text.

    This is the test-mode substitute for any real external image generator.
    The goal is full transparency: the returned file IS the record of what
    the agent asked for — no network, no spend.
    """
    out = out_dir or output_dir()
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(f"shim: could not create output dir {out}: {e}") from e

    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), color=(30, 30, 46))
    draw = ImageDraw.Draw(img)
    label_font = _load_font(28)
    body_font = _load_font(36)

    wrapped = textwrap.fill(prompt, width=28)

    # Center the text block vertically and horizontally
    _, _, tw, th = draw.multiline_textbbox((0, 0), wrapped, font=body_font, spacing=10)
    label = f"SCENE · {model.upper()}"
    _, _, lw, lh = draw.textbbox((0, 0), label, font=label_font)

    total_h = lh + 24 + th
    y_start = (H - total_h) // 2

    draw.text(((W - lw) // 2, y_start), label, fill=(160, 160, 200), font=label_font)
    draw.multiline_text(
        ((W - tw) // 2, y_start + lh + 24),
        wrapped,
        fill=(240, 240, 255),
        font=body_font,
        spacing=10,
        align="center",
    )

    key = hashlib.sha1(f"{prompt}|{model}".encode()).hexdigest()[:10]
    path = out / f"mock_{key}.png"
    try:
        img.save(path)
    except OSError as e:
        raise RuntimeError(f"shim: could not write PNG {path}: {e}") from e
    return path
