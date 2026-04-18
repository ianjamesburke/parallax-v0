from __future__ import annotations

import time
from typing import Any

from . import fal, usage
from .context import current_backend, current_session_id
from .log import get_logger
from .pricing import ALIASES, resolve
from .shim import is_test_mode, render_mock_image

log = get_logger("tools")

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "generate_image",
        "description": (
            "Generate an image from a text prompt via FAL. "
            "Returns the filesystem path to the generated PNG. "
            "Pass `model` as exactly one of: draft, mid, premium, nano-banana, grok. "
            "Never pass raw FAL model IDs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Description of the image to generate.",
                },
                "model": {
                    "type": "string",
                    "enum": list(ALIASES),
                    "description": (
                        "Agent-facing model alias. 'mid' is the default if the user did not "
                        "specify a tier."
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


def generate_image(prompt: str, model: str) -> str:
    spec = resolve(model)  # fails fast on unknown alias
    test_mode = is_test_mode()

    t0 = time.monotonic()
    if test_mode:
        output_path = str(render_mock_image(prompt=prompt, model=spec.alias))
    else:
        output_path = str(fal.generate(prompt=prompt, spec=spec))
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


def _summarize_args(args: dict[str, Any]) -> str:
    parts: list[str] = []
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 60:
            parts.append(f"{k}={v[:57]!r}...")
        else:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts)
