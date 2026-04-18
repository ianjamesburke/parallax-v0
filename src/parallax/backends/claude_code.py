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
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from ..context import current_backend, current_session_id
from ..log import get_logger
from ..pricing import alias_guidance
from ..tools import dispatch_tool

log = get_logger("backends.claude_code")

NAME = "claude-code"
MAX_TURNS = 20

SYSTEM_PROMPT = (
    "You are Parallax, an agentic creative production assistant. "
    "Your job is to take a creative brief and produce image assets by calling the generate_image tool. "
    "Report back concisely with the file paths you generated.\n\n"
    + alias_guidance()
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
    log.info("session: %s", "resuming " + session_id if session_id else "new")
    current_backend.set(NAME)
    if session_id:
        current_session_id.set(session_id)
    result = asyncio.run(_run_async(brief, session_id, query_fn=query_fn, max_turns=max_turns))
    if result.get("session_id"):
        transcript = _transcript_path_hint(result["session_id"])
        log.info("SDK transcript: %s", transcript)
    return result


def _transcript_path_hint(session_id: str) -> str:
    """Best-effort path where claude-agent-sdk stores the full jsonl transcript.

    The real sanitization rule is internal to the SDK; this hint is usually
    correct, and ls-ing the parent dir is the fallback when it isn't.
    """
    cwd = Path.cwd()
    sanitized = "-" + str(cwd).lstrip("/").replace("/", "-")
    return str(Path.home() / ".claude" / "projects" / sanitized / f"{session_id}.jsonl")


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

    # The SDK runs the MCP tool handler in a separate task context from this
    # coroutine, so ContextVars set on the outer loop do NOT propagate in.
    # Use a mutable holder the closure can read, and re-set the ContextVar
    # inside the handler so usage records carry the right session_id.
    sid_holder: dict[str, str | None] = {"sid": session_id}

    @tool(
        "generate_image",
        "Generate an image from a text prompt via FAL. Returns the filesystem path to the generated PNG. "
        "Never guess model IDs — only pass models explicitly requested by the user.",
        {"prompt": str, "model": str},
    )
    async def _generate_image(args: dict[str, Any]) -> dict[str, Any]:
        current_backend.set(NAME)
        current_session_id.set(sid_holder["sid"])
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
        log.debug("sdk message: %s", type(message).__name__)
        if isinstance(message, SystemMessage):
            # init event carries the fresh session_id
            data = getattr(message, "data", {}) or {}
            sid = data.get("session_id") if isinstance(data, dict) else None
            if sid:
                captured_session_id = sid
                sid_holder["sid"] = sid
                current_session_id.set(sid)
                log.debug("sdk session_id captured: %s", sid)
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
