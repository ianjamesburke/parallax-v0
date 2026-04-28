"""Per-run JSONL event log.

Every `parallax produce` invocation gets a unique run_id and a dedicated log
file at `~/.parallax/logs/<run_id>.log`. One JSON object per line. Always
debug-level — verbosity flags only affect stderr (see log.py).

Schema (every line):
  ts: ISO-8601 UTC
  run_id: str
  level: "DEBUG" | "INFO" | "WARN" | "ERROR"
  event: short event name, e.g. "stage.start", "openrouter.call", "openrouter.response"
  ...arbitrary structured fields per event

External-call events MUST include: provider, model_id, duration_ms, cost_usd,
test_mode, and either request/response or an error field. This is the trace
the agent loop reads back when iterating.
"""

from __future__ import annotations

import json
import os
import secrets
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_current_run_id: ContextVar[str | None] = ContextVar("parallax_run_id", default=None)
_current_log_path: ContextVar[Path | None] = ContextVar("parallax_run_log_path", default=None)


def logs_dir() -> Path:
    override = os.environ.get("PARALLAX_LOG_DIR")
    if override:
        return Path(override).expanduser()
    return Path(os.path.expanduser("~/.parallax/logs"))


def new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{secrets.token_hex(3)}"


def start_run(run_id: str | None = None) -> str:
    """Open a new run log. Returns the run_id."""
    rid = run_id or new_run_id()
    path = logs_dir() / f"{rid}.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError as e:
        raise RuntimeError(f"runlog: could not create log file {path}: {e}") from e
    _current_run_id.set(rid)
    _current_log_path.set(path)
    event("run.start", run_id=rid, log_path=str(path))
    return rid


def end_run(status: str = "ok", **fields: Any) -> None:
    rid = _current_run_id.get()
    if rid is None:
        return
    event("run.end", status=status, **fields)
    _current_run_id.set(None)
    _current_log_path.set(None)


def current_run_id() -> str | None:
    return _current_run_id.get()


def current_log_path() -> Path | None:
    return _current_log_path.get()


def event(event_name: str, *, level: str = "INFO", **fields: Any) -> None:
    """Append one JSON line to the active run's log.

    Silent no-op if no run is active — keeps the rest of the CLI usable
    (e.g. `parallax usage`) without forcing every code path to start a run.
    """
    path = _current_log_path.get()
    if path is None:
        return
    rec: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": _current_run_id.get(),
        "level": level,
        "event": event_name,
    }
    for k, v in fields.items():
        rec[k] = _coerce(v)
    try:
        with path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        # Fail-soft: never let logging crash the pipeline. The stderr logger
        # in log.py is the redundant channel.
        pass


def _coerce(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, dict):
        return {k: _coerce(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_coerce(x) for x in v]
    return repr(v)


def tail(run_id: str, *, follow: bool = False) -> int:
    """Print all events for a given run_id. Returns 0 on success, 1 if not found."""
    import sys
    import time as _time

    path = logs_dir() / f"{run_id}.log"
    if not path.exists():
        print(f"runlog: no such run: {run_id} (looked at {path})", file=sys.stderr)
        return 1
    with path.open() as f:
        for line in f:
            sys.stdout.write(line)
        sys.stdout.flush()
        if not follow:
            return 0
        try:
            while True:
                line = f.readline()
                if not line:
                    _time.sleep(0.5)
                    continue
                sys.stdout.write(line)
                sys.stdout.flush()
        except KeyboardInterrupt:
            return 0
