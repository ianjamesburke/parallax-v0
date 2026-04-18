from __future__ import annotations

import json

import pytest

from parallax.sessions import Session, sessions_dir


@pytest.fixture(autouse=True)
def _isolate_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLAX_SESSIONS_DIR", str(tmp_path / "sessions"))
    yield


def test_create_writes_session_start_event():
    s = Session.create()
    assert s.path.exists()
    lines = s.path.read_text().strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["type"] == "session_start"
    assert event["session_id"] == s.session_id


def test_messages_round_trip_via_resume():
    s1 = Session.create()
    s1.add_user_message("hello")
    s1.add_assistant_message([{"type": "text", "text": "world"}], stop_reason="end_turn")
    s1.end()

    s2 = Session.resume(s1.session_id)
    assert s2.messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "world"}]},
    ]


def test_resume_missing_session_raises():
    with pytest.raises(FileNotFoundError):
        Session.resume("does-not-exist")


def test_sessions_dir_respects_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLAX_SESSIONS_DIR", str(tmp_path / "custom"))
    assert sessions_dir() == tmp_path / "custom"
