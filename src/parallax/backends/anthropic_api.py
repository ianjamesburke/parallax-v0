"""Anthropic API backend — uses raw Messages API with an explicit tool_use loop.

Billed against an ANTHROPIC_API_KEY (separate from any Claude subscription).
Session state is persisted as append-only NDJSON under sessions_dir().
Opt in via --backend anthropic-api or PARALLAX_BACKEND=anthropic-api.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from ..sessions import Session
from ..tools import TOOL_SCHEMAS, dispatch_tool

NAME = "anthropic-api"
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TURNS = 20
MAX_OUTPUT_TOKENS = 4096

SYSTEM_PROMPT = (
    "You are Parallax, an agentic creative production assistant. "
    "Your job is to take a creative brief and produce image assets by calling the generate_image tool. "
    "For each image the user requests, call generate_image with a concrete prompt and the FAL model they specified. "
    "Report back concisely with the file paths you generated. "
    "If the user has not specified a model, ask — never guess."
)


class _MessagesClient(Protocol):
    messages: Any


def check_available() -> None:
    """Fail fast if ANTHROPIC_API_KEY is not set. No silent fallback."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "anthropic-api backend requires ANTHROPIC_API_KEY to be set. "
            "Either export the key, or switch to --backend claude-code to use your Claude subscription."
        )


def run(
    brief: str,
    session_id: str | None = None,
    *,
    client: _MessagesClient | None = None,
    model: str = DEFAULT_MODEL,
    max_turns: int = MAX_TURNS,
) -> dict[str, Any]:
    """Run the agent loop against a single creative brief.

    Returns {"session_id": ..., "text": ...}. The session is persisted as
    append-only NDJSON under sessions_dir().
    """
    if client is None:
        from anthropic import Anthropic

        client = Anthropic()

    session = Session.resume(session_id) if session_id else Session.create()
    session.add_user_message(brief)

    final_text_parts: list[str] = []

    try:
        for _ in range(max_turns):
            response = client.messages.create(
                model=model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=TOOL_SCHEMAS,
                messages=session.messages,
            )

            assistant_content = [_to_dict(block) for block in response.content]
            stop_reason = getattr(response, "stop_reason", None)
            session.add_assistant_message(assistant_content, stop_reason)

            if stop_reason == "end_turn":
                for block in assistant_content:
                    if block.get("type") == "text":
                        final_text_parts.append(block.get("text", ""))
                break

            if stop_reason == "tool_use":
                session.add_tool_results(_run_tools(assistant_content))
                continue

            raise RuntimeError(f"Unexpected stop_reason: {stop_reason!r}")
        else:
            session.end(reason="max_turns_exceeded")
            raise RuntimeError(f"Agent exceeded max_turns={max_turns}")

        session.end(reason="completed")
    except Exception as e:
        session.end(reason=f"error: {type(e).__name__}: {e}")
        raise

    return {"session_id": session.session_id, "text": "\n".join(p for p in final_text_parts if p).strip()}


def _to_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    dumper = getattr(block, "model_dump", None)
    if callable(dumper):
        dumped = dumper(exclude_none=True)
        if isinstance(dumped, dict):
            return dumped
    raise TypeError(f"Cannot serialize content block: {block!r}")


def _run_tools(assistant_content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for block in assistant_content:
        if block.get("type") != "tool_use":
            continue
        tool_id = block["id"]
        name = block["name"]
        args = block.get("input") or {}
        try:
            output = dispatch_tool(name, args)
            results.append({"type": "tool_result", "tool_use_id": tool_id, "content": output})
        except Exception as e:
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": f"Error: {type(e).__name__}: {e}",
                    "is_error": True,
                }
            )
    return results
