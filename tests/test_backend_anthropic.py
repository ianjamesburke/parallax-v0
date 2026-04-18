"""Hermetic end-to-end test for the agent loop.

Stubs the Anthropic client with a canned response script and verifies the
full tool_use → tool_result → end_turn cycle end to end: the mock PNG gets
written by the shim, the tool_result is threaded back into the next
messages.create call, the session NDJSON captures every event.

No network, no API key, no spend.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from parallax.backends.anthropic_api import run


class FakeAnthropic:
    """Minimal stand-in for anthropic.Anthropic.messages.create — replays a canned list."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.messages = SimpleNamespace(create=self._create)
        self.calls: list[dict] = []

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeAnthropic exhausted canned responses")
        return self._responses.pop(0)


def _dict_block(**fields):
    """Content block implemented as a plain object with .model_dump()."""

    def dump(exclude_none: bool = False):
        if exclude_none:
            return {k: v for k, v in fields.items() if v is not None}
        return dict(fields)

    block = SimpleNamespace(**fields)
    block.model_dump = dump
    return block


def _response(content_blocks, stop_reason):
    return SimpleNamespace(content=content_blocks, stop_reason=stop_reason)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("PARALLAX_SESSIONS_DIR", str(tmp_path / "sessions"))
    yield


def test_full_loop_tool_use_then_end_turn():
    tool_use = _dict_block(
        type="tool_use",
        id="toolu_test_1",
        name="generate_image",
        input={"prompt": "a watercolor cat", "model": "premium"},
    )
    summary = _dict_block(type="text", text="Generated 1 image at output/mock_*.png.")

    client = FakeAnthropic(
        [
            _response([tool_use], "tool_use"),
            _response([summary], "end_turn"),
        ]
    )

    result = run(
        brief="Make one image of a watercolor cat at premium tier.",
        client=client,
    )

    assert result["session_id"]
    assert "Generated 1 image" in result["text"]

    # The loop made exactly two model calls (tool_use, then end_turn).
    assert len(client.calls) == 2

    # Every call carried the cached system prompt.
    for call in client.calls:
        assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert any(t["name"] == "generate_image" for t in call["tools"])

    # The second call's messages must include the tool_result threaded in
    # from the first turn, and its content must be an existing PNG path.
    second_messages = client.calls[1]["messages"]
    tool_result_block = None
    for msg in second_messages:
        if msg["role"] != "user":
            continue
        content = msg["content"]
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_result_block = block
    assert tool_result_block is not None, "tool_result was never threaded back to the model"
    png_path = Path(tool_result_block["content"])
    assert png_path.exists()
    assert png_path.suffix == ".png"

    # Session NDJSON should contain start, user, assistant(tool_use),
    # tool_result, assistant(end_turn), session_end — at minimum.
    from parallax.sessions import sessions_dir

    session_file = sessions_dir() / f"{result['session_id']}.ndjson"
    lines = [json.loads(line) for line in session_file.read_text().splitlines() if line.strip()]
    types = [e["type"] for e in lines]
    assert types[0] == "session_start"
    assert "user_message" in types
    assert types.count("assistant_message") == 2
    assert "tool_result" in types
    assert types[-1] == "session_end"
    assert lines[-1]["reason"] == "completed"


def test_tool_error_is_reported_as_tool_result():
    # Ask the tool loop to call an *unknown* tool name — the loop must
    # surface the ValueError as a tool_result with is_error=True, not crash.
    bad_tool = _dict_block(
        type="tool_use",
        id="toolu_bad",
        name="nonexistent_tool",
        input={},
    )
    summary = _dict_block(type="text", text="done")

    client = FakeAnthropic(
        [
            _response([bad_tool], "tool_use"),
            _response([summary], "end_turn"),
        ]
    )
    run(brief="try it", client=client)

    second_messages = client.calls[1]["messages"]
    error_result = None
    for msg in second_messages:
        if isinstance(msg["content"], list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    error_result = block
    assert error_result is not None
    assert error_result.get("is_error") is True
    assert "Unknown tool" in error_result["content"]
