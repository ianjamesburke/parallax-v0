from __future__ import annotations

from typing import Any

from .log import get_logger
from .shim import is_test_mode, render_mock_image

log = get_logger("tools")

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "generate_image",
        "description": (
            "Generate an image from a text prompt via FAL. "
            "Returns the filesystem path to the generated PNG. "
            "Never guess model IDs — only pass models explicitly requested by the user."
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
                    "description": "FAL model ID (e.g. flux-schnell, flux-dev, flux-pro).",
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
    if is_test_mode():
        path = render_mock_image(prompt=prompt, model=model)
        return str(path)
    raise NotImplementedError(
        "Real FAL integration lands in commit 2. Set PARALLAX_TEST_MODE=1 to run the shim."
    )


def _summarize_args(args: dict[str, Any]) -> str:
    parts: list[str] = []
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 60:
            parts.append(f"{k}={v[:57]!r}...")
        else:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts)
