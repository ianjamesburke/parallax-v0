from __future__ import annotations

from typing import Any

from .shim import is_test_mode, render_mock_image

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
    if name == "generate_image":
        return generate_image(**args)
    raise ValueError(f"Unknown tool: {name!r}")


def generate_image(prompt: str, model: str) -> str:
    if is_test_mode():
        path = render_mock_image(prompt=prompt, model=model)
        return str(path)
    raise NotImplementedError(
        "Real FAL integration lands in commit 2. Set PARALLAX_TEST_MODE=1 to run the shim."
    )
