from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import fal, usage
from .context import current_backend, current_session_id
from .log import get_logger
from .pricing import ALIASES, ModelSpec, resolve
from .shim import is_test_mode, render_mock_image

log = get_logger("tools")

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "generate_image",
        "description": (
            "Generate an image from a text prompt via FAL. "
            "Returns the filesystem path to the generated image. "
            "Pass `model` as exactly one of: draft, mid, premium, nano-banana, grok. "
            "Never pass raw FAL model IDs. "
            "Pass `reference_images` as a list of local filesystem paths to condition on "
            "(only models marked as supporting reference_images accept this)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Description of the image to generate, or the edit instruction when reference_images are provided.",
                },
                "model": {
                    "type": "string",
                    "enum": list(ALIASES),
                    "description": (
                        "Agent-facing model alias. 'mid' is the default if the user did not "
                        "specify a tier. Must support reference_images when those are provided."
                    ),
                },
                "reference_images": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of local filesystem paths to use as reference / input images. "
                        "Only pass when the user has supplied or implied input images to edit, remix, or condition on."
                    ),
                },
            },
            "required": ["prompt", "model"],
        },
    },
]


def dispatch_tool(name: str, args: dict[str, Any]) -> str:
    log.info("tool call: %s(%s)", name, _summarize_args(args))
    log.debug("tool call args full: %s", args)
    try:
        if name == "generate_image":
            result = generate_image(**args)
        else:
            raise ValueError(f"Unknown tool: {name!r}")
    except Exception as e:
        log.info("tool result (error): %s: %s", type(e).__name__, e)
        raise
    log.info("tool result: %s", result)
    return result


def generate_image(
    prompt: str,
    model: str,
    reference_images: list[str] | None = None,
) -> str:
    spec = resolve(model)  # fails fast on unknown alias
    refs = _validate_refs(reference_images, spec)
    test_mode = is_test_mode()

    t0 = time.monotonic()
    if test_mode:
        output_path = str(render_mock_image(prompt=prompt, model=spec.alias))
    else:
        output_path = str(fal.generate(prompt=prompt, spec=spec, reference_images=refs))
    duration_ms = int((time.monotonic() - t0) * 1000)
    cost_usd = 0.0 if test_mode else spec.price_usd_per_image

    rec = usage.record(
        session_id=current_session_id.get(),
        backend=current_backend.get(),
        alias=spec.alias,
        fal_id=spec.fal_id,
        tier=spec.tier,
        prompt=prompt,
        output_path=output_path,
        duration_ms=duration_ms,
        cost_usd=cost_usd,
        test_mode=test_mode,
    )
    log.info(
        "usage: alias=%s duration=%dms cost=$%.4f%s",
        rec.alias,
        rec.duration_ms,
        rec.cost_usd,
        " [test]" if test_mode else "",
    )
    return output_path


def _validate_refs(reference_images: list[str] | None, spec: ModelSpec) -> list[Path]:
    """Coerce, validate, and resolve reference paths. Fails fast on the caller's behalf."""
    if not reference_images:
        return []
    if not spec.supports_reference:
        raise ValueError(
            f"Model {spec.alias!r} does not support reference_images. "
            f"Use one of: {', '.join(a for a, s in _ref_capable().items())}."
        )
    if len(reference_images) > spec.max_refs:
        raise ValueError(
            f"Model {spec.alias!r} accepts at most {spec.max_refs} reference image(s); "
            f"got {len(reference_images)}."
        )
    resolved: list[Path] = []
    for ref in reference_images:
        p = Path(ref).expanduser()
        if not p.is_file():
            raise ValueError(f"reference_images path not found or not a file: {ref!r}")
        resolved.append(p)
    return resolved


def _ref_capable() -> dict[str, str]:
    from .pricing import MODELS

    return {a: s.description for a, s in MODELS.items() if s.supports_reference}


def _summarize_args(args: dict[str, Any]) -> str:
    parts: list[str] = []
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 60:
            parts.append(f"{k}={v[:57]!r}...")
        else:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts)
