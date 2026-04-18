"""Claude Code backend — routes through the user's `claude` CLI via claude-agent-sdk.

Uses the user's Claude subscription (Max/Team/etc.) when the CLI is logged in.
No API key required. Session state is managed natively by the SDK — resume via
the session_id returned from a prior `run()`. We do NOT duplicate the history
into our own NDJSON store here.

This is the default backend. Opt out with --backend anthropic-api.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any, AsyncIterator, Callable

from ..tools import dispatch_tool

NAME = "claude-code"
MAX_TURNS = 20

SYSTEM_PROMPT = (
    "You are Parallax, an agentic creative production assistant. "
    "Your job is to take a creative brief and produce image assets by calling the generate_image tool. "
    "For each image the user requests, call generate_image with a concrete prompt and the FAL model they specified. "
    "Report back concisely with the file paths you generated. "
    "If the user has not specified a model, ask — never guess."
)


def check_available() -> None:
    """Fail fast if the `claude` CLI is not on PATH. No silent fallback."""
    if shutil.which("claude") is None:
        raise RuntimeError(
            "claude-code backend requires the `claude` CLI to be installed and on PATH. "
            "Install Claude Code (https://claude.com/claude-code) or switch to "
            "--backend anthropic-api with ANTHROPIC_API_KEY set."
        )


def run(
    brief: str,
    session_id: str | None = None,
    *,
    query_fn: Callable[..., AsyncIterator[Any]] | None = None,
    max_turns: int = MAX_TURNS,
) -> dict[str, Any]:
    """Run the brief via claude-agent-sdk. Returns {"session_id", "text"}.

    `query_fn` is dependency-injectable for hermetic tests — leave None in
    production to use the real SDK.
    """
    return asyncio.run(_run_async(brief, session_id, query_fn=query_fn, max_turns=max_turns))


async def _run_async(
    brief: str,
    session_id: str | None,
    *,
    query_fn: Callable[..., AsyncIterator[Any]] | None,
    max_turns: int,
) -> dict[str, Any]:
    # Imported lazily so the SDK is only loaded when this backend is selected.
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        SystemMessage,
        TextBlock,
        create_sdk_mcp_server,
        query,
        tool,
    )

    @tool(
        "generate_image",
        "Generate an image from a text prompt via FAL. Returns the filesystem path to the generated PNG. "
        "Never guess model IDs — only pass models explicitly requested by the user.",
        {"prompt": str, "model": str},
    )
    async def _generate_image(args: dict[str, Any]) -> dict[str, Any]:
        try:
            path = dispatch_tool("generate_image", args)
            return {"content": [{"type": "text", "text": path}]}
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error: {type(e).__name__}: {e}"}],
                "isError": True,
            }

    server = create_sdk_mcp_server(name="parallax", version="0.0.1", tools=[_generate_image])

    options = ClaudeAgentOptions(
        mcp_servers={"parallax": server},
        allowed_tools=["mcp__parallax__generate_image"],
        system_prompt=SYSTEM_PROMPT,
        resume=session_id,
        max_turns=max_turns,
        permission_mode="acceptEdits",
    )

    runner = query_fn if query_fn is not None else query

    captured_session_id: str | None = session_id
    final_text_parts: list[str] = []
    errored = False

    async for message in runner(prompt=brief, options=options):
        if isinstance(message, SystemMessage):
            # init event carries the fresh session_id
            data = getattr(message, "data", {}) or {}
            sid = data.get("session_id") if isinstance(data, dict) else None
            if sid:
                captured_session_id = sid
        elif isinstance(message, AssistantMessage):
            for block in getattr(message, "content", []) or []:
                if isinstance(block, TextBlock):
                    text = getattr(block, "text", "") or ""
                    if text:
                        final_text_parts.append(text)
        elif isinstance(message, ResultMessage):
            sid = getattr(message, "session_id", None)
            if sid:
                captured_session_id = sid
            if getattr(message, "is_error", False):
                errored = True
                result_text = getattr(message, "result", None) or "unknown error"
                raise RuntimeError(f"claude-code backend returned error: {result_text}")

    if errored:  # pragma: no cover — raised above
        raise RuntimeError("claude-code backend errored")

    return {
        "session_id": captured_session_id or "",
        "text": "\n".join(p for p in final_text_parts if p).strip(),
    }
