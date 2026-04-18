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


def render_mock_image(prompt: str, model: str) -> Path:
    """Render a PNG that contains the request parameters as readable text.

    This is the test-mode substitute for any real external image generator.
    The goal is full transparency: the returned file IS the record of what
    the agent asked for — no network, no spend.
    """
    out = output_dir()
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(f"shim: could not create output dir {out}: {e}") from e

    img = Image.new("RGB", (1024, 1024), color=(245, 245, 250))
    draw = ImageDraw.Draw(img)
    header_font = _load_font(34)
    body_font = _load_font(24)

    draw.text((40, 40), f"FAL {model}", fill=(18, 18, 36), font=header_font)
    draw.line([(40, 96), (984, 96)], fill=(18, 18, 36), width=2)
    draw.text((40, 120), "prompt:", fill=(90, 90, 110), font=body_font)
    draw.multiline_text(
        (40, 156),
        textwrap.fill(prompt, width=44),
        fill=(30, 30, 60),
        font=body_font,
        spacing=6,
    )

    key = hashlib.sha1(f"{prompt}|{model}".encode()).hexdigest()[:10]
    path = out / f"mock_{key}.png"
    try:
        img.save(path)
    except OSError as e:
        raise RuntimeError(f"shim: could not write PNG {path}: {e}") from e
    return path
