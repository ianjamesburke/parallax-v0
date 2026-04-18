"""Hermetic test for the claude-code backend.

Injects a fake `query_fn` that replays a canned async stream of SDK messages.
Verifies: session_id is captured from SystemMessage.init / ResultMessage,
assistant text is concatenated, errored ResultMessage raises.

Does not exercise the in-process MCP server or actual tool dispatch — those
are exercised by real live runs. The anthropic-api e2e covers the full
tool_use loop end-to-end.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest

from parallax.backends import claude_code


def _async_iter(items: list[Any]):
    async def gen(**_kwargs) -> AsyncIterator[Any]:
        for item in items:
            yield item

    return gen


def _system_init(session_id: str):
    from claude_agent_sdk import SystemMessage

    # SystemMessage(subtype, data)
    return SystemMessage(subtype="init", data={"session_id": session_id})


def _assistant_text(text: str):
    from claude_agent_sdk import AssistantMessage, TextBlock

    return AssistantMessage(content=[TextBlock(text=text)], model="claude-sonnet-4-6", parent_tool_use_id=None)


def _result(session_id: str, is_error: bool = False, result_text: str | None = None):
    from claude_agent_sdk import ResultMessage

    return ResultMessage(
        subtype="success" if not is_error else "error",
        duration_ms=0,
        duration_api_ms=0,
        is_error=is_error,
        num_turns=1,
        session_id=session_id,
        stop_reason="end_turn",
        total_cost_usd=0.0,
        usage={},
        result=result_text,
        structured_output=None,
        model_usage={},
        permission_denials=[],
        errors=[],
        uuid="",
    )


def test_captures_session_id_and_assistant_text():
    fake_query = _async_iter(
        [
            _system_init("sess-abc"),
            _assistant_text("Generated 1 image."),
            _result("sess-abc"),
        ]
    )
    out = claude_code.run(brief="make a thing", query_fn=fake_query)
    assert out["session_id"] == "sess-abc"
    assert "Generated 1 image." in out["text"]


def test_errored_result_raises():
    fake_query = _async_iter(
        [
            _system_init("sess-err"),
            _result("sess-err", is_error=True, result_text="model refused"),
        ]
    )
    with pytest.raises(RuntimeError, match="model refused"):
        claude_code.run(brief="make a thing", query_fn=fake_query)


def test_resume_session_id_preserved_when_no_init():
    """When resuming, the SDK may not emit a fresh init — the caller-provided
    session_id should carry through if ResultMessage doesn't override it."""
    fake_query = _async_iter(
        [
            _assistant_text("resumed ok"),
            _result("sess-resumed"),  # SDK typically echoes the same id
        ]
    )
    out = claude_code.run(brief="continue", session_id="sess-resumed", query_fn=fake_query)
    assert out["session_id"] == "sess-resumed"
    assert out["text"] == "resumed ok"
