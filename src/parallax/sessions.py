from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sessions_dir() -> Path:
    override = os.environ.get("PARALLAX_SESSIONS_DIR")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~/.parallax/sessions"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


@dataclass
class Session:
    session_id: str
    path: Path
    messages: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def create(cls) -> "Session":
        directory = sessions_dir()
        directory.mkdir(parents=True, exist_ok=True)
        sid = _new_session_id()
        path = directory / f"{sid}.ndjson"
        session = cls(session_id=sid, path=path)
        session._append_event({"type": "session_start", "session_id": sid, "created_at": _now_iso()})
        return session

    @classmethod
    def resume(cls, session_id: str) -> "Session":
        path = sessions_dir() / f"{session_id}.ndjson"
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {path}")
        session = cls(session_id=session_id, path=path)
        session._reload_messages()
        session._append_event({"type": "session_resumed", "at": _now_iso()})
        return session

    def _append_event(self, event: dict[str, Any]) -> None:
        try:
            with self.path.open("a") as f:
                f.write(json.dumps(event) + "\n")
        except OSError as e:
            raise RuntimeError(f"Failed to append session event to {self.path}: {e}") from e

    def _reload_messages(self) -> None:
        try:
            with self.path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    event = json.loads(line)
                    t = event.get("type")
                    if t == "user_message":
                        self.messages.append({"role": "user", "content": event["content"]})
                    elif t == "assistant_message":
                        self.messages.append({"role": "assistant", "content": event["content"]})
                    elif t == "tool_result":
                        self.messages.append({"role": "user", "content": event["content"]})
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Failed to reload session {self.path}: {e}") from e

    def add_user_message(self, content: str | list[dict[str, Any]]) -> None:
        self.messages.append({"role": "user", "content": content})
        self._append_event({"type": "user_message", "content": content, "at": _now_iso()})

    def add_assistant_message(self, content: list[dict[str, Any]], stop_reason: str | None) -> None:
        self.messages.append({"role": "assistant", "content": content})
        self._append_event(
            {"type": "assistant_message", "content": content, "stop_reason": stop_reason, "at": _now_iso()}
        )

    def add_tool_results(self, results: list[dict[str, Any]]) -> None:
        self.messages.append({"role": "user", "content": results})
        self._append_event({"type": "tool_result", "content": results, "at": _now_iso()})

    def end(self, reason: str = "completed") -> None:
        self._append_event({"type": "session_end", "reason": reason, "at": _now_iso()})
