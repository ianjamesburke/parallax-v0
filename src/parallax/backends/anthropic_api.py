"""Anthropic API backend — uses raw Messages API with an explicit tool_use loop.

Billed against an ANTHROPIC_API_KEY (separate from any Claude subscription).
Session state is persisted as append-only NDJSON under sessions_dir().
Opt in via --backend anthropic-api or PARALLAX_BACKEND=anthropic-api.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from ..context import current_backend, current_session_id
from ..log import get_logger
from ..pricing import alias_guidance
from ..sessions import Session
from ..tools import TOOL_SCHEMAS, dispatch_tool

log = get_logger("backends.anthropic_api")

NAME = "anthropic-api"
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TURNS = 20
MAX_OUTPUT_TOKENS = 4096

SYSTEM_PROMPT = (
    "You are Parallax, a Head-of-Production agent for AI-assisted short-form video. "
    "You receive a creative brief (a script + character reference, or a folder path) and produce "
    "a finished Ken Burns draft video with voiceover and burned captions.\n\n"
    "## Pipeline — follow this order exactly\n\n"
    "1. **Scan** — call `scan_project_folder` if given a folder. Extracts script_text and character_image_path.\n"
    "2. **Plan scenes** — parse script_text into 4-8 scenes. For each scene, write:\n"
    "   - `vo_text`: the exact words spoken during that scene (verbatim from script)\n"
    "   - `prompt`: a visual scene description for image generation\n"
    "   - `index`: integer starting at 0\n"
    "3. **Generate stills** — call `generate_image` once per scene. Use model='mid'. "
    "   Pass the character_image_path as reference_images so the character is consistent. "
    "   Store each returned path as `still_path` on the scene.\n"
    "4. **Generate voiceover** — call `generate_voiceover` with the full script_text. "
    "   Use voice='george' and speed=1.1 by default unless the brief specifies otherwise.\n"
    "5. **Align scenes** — call `align_scenes` with the scenes list (JSON) and the words from step 4. "
    "   This fills in start_s/end_s/duration_s on each scene.\n"
    "6. **Assemble Ken Burns** — call `ken_burns_assemble` with the aligned scenes JSON, the audio_path, "
    "   and resolution='1080x1920' (vertical) or '1920x1080' (landscape). Gets you a draft .mp4.\n"
    "7. **Burn captions** — call `burn_captions` with the draft video path and the words_path from step 4.\n"
    "8. **Report** — tell the user the final video path and a one-line summary of what was made.\n\n"
    "## Rules\n"
    "- Never skip steps. Never ask the user for approval mid-pipeline unless a tool fails.\n"
    "- If `scan_project_folder` returns null for either script or character image, tell the user what's missing.\n"
    "- Keep scene prompts cinematic and specific — they feed an image model. Include lighting, mood, angle.\n"
    "- vo_text for all scenes concatenated must equal the full script exactly (word for word).\n"
    "- Each scene prompt should reference the character visually (hair, clothing, setting) for consistency.\n\n"
    + alias_guidance()
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
    log.info("session: %s (%s)", session.session_id, "resumed" if session_id else "new")
    log.info("session log: %s", session.path)
    current_backend.set(NAME)
    current_session_id.set(session.session_id)
    session.add_user_message(brief)

    final_text_parts: list[str] = []

    try:
        for turn_idx in range(max_turns):
            log.debug("turn %d: calling model=%s", turn_idx + 1, model)
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
            log.debug("turn %d: stop_reason=%s, blocks=%d", turn_idx + 1, stop_reason, len(assistant_content))

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
