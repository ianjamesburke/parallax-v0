"""Thin compatibility shim — delegates to openrouter.generate_image.

Kept so existing callsites (`from parallax.tools import generate_image`)
keep working. New code should call `parallax.openrouter.generate_image`
directly. This file may be removed in a future cleanup.
"""

from __future__ import annotations

from pathlib import Path

from .openrouter import generate_image as _generate_image


def generate_image(
    prompt: str,
    model: str,
    reference_images: list[str] | None = None,
    out_dir: str | None = None,
    aspect_ratio: str | None = None,
) -> str:
    out = Path(out_dir) if out_dir else None
    path = _generate_image(
        prompt=prompt,
        alias=model,
        reference_images=reference_images,
        out_dir=out,
        aspect_ratio=aspect_ratio,
    )
    return str(path)
