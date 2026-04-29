"""Per-run JSONL event log.

Every `parallax produce` invocation gets a unique run_id and a dedicated log
file at `<output_dir>/run.log`. One JSON object per line.

Pre-output_dir events (run.start, plan.loaded) are buffered in memory until
`bind_output_dir(output_dir)` is called from `stage_scan`, then flushed.

Run index: every `end_run` appends one summary row to
`~/.parallax/runs.ndjson`. `find_run(spec)` resolves "latest" / 6-hex short /
full run_id back to the on-disk row so callers can locate `<output_dir>/run.log`.

Schema (every line in run.log):
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
_current_buffer: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "parallax_run_log_buffer", default=None
)
_current_run_meta: ContextVar[dict[str, Any] | None] = ContextVar(
    "parallax_run_meta", default=None
)


def runs_index_path() -> Path:
    """Path to the global run index NDJSON. Override via PARALLAX_RUNS_INDEX."""
    override = os.environ.get("PARALLAX_RUNS_INDEX")
    if override:
        return Path(override).expanduser()
    return Path(os.path.expanduser("~/.parallax/runs.ndjson"))


def new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{secrets.token_hex(3)}"


def short_id(run_id: str) -> str:
    """Last 6 hex chars of a run_id — used for filename suffixes + lookup."""
    return run_id[-6:]


def start_run(run_id: str | None = None) -> str:
    """Open a new run. Returns the run_id.

    Events are buffered in memory until `bind_output_dir(...)` is called by
    the first stage that knows where the output directory lives. After that
    all events stream straight to `<output_dir>/run.log`.
    """
    rid = run_id or new_run_id()
    _current_run_id.set(rid)
    _current_log_path.set(None)
    _current_buffer.set([])
    _current_run_meta.set({
        "run_id": rid,
        "short": short_id(rid),
        "started": datetime.now(timezone.utc).isoformat(),
        "output_dir": None,
        "plan_path": None,
        "scene_count": None,
    })
    event("run.start", run_id=rid)
    return rid


def bind_output_dir(output_dir: str | Path) -> Path:
    """Bind the active run to an output dir and flush buffered events.

    Called once per run from `stage_scan` after it computes the versioned
    output directory. Returns the resolved path to `<output_dir>/run.log`.
    """
    path = Path(output_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    log_path = path / "run.log"
    try:
        log_path.touch()
    except OSError as e:
        raise RuntimeError(f"runlog: could not create log file {log_path}: {e}") from e
    _current_log_path.set(log_path)

    meta = _current_run_meta.get() or {}
    meta["output_dir"] = str(path)
    _current_run_meta.set(meta)

    # Flush any buffered events into the new file.
    buffered = _current_buffer.get() or []
    if buffered:
        try:
            with log_path.open("a") as f:
                for rec in buffered:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass
    _current_buffer.set(None)
    return log_path


def end_run(status: str = "ok", *, cost_usd: float | None = None, **fields: Any) -> None:
    rid = _current_run_id.get()
    if rid is None:
        return

    # Resolve cost_usd from the per-call usage log if not passed explicitly.
    # Fixes the rollup bug where run.end reported total_cost_usd: 0.0.
    if cost_usd is None:
        try:
            from . import usage as _usage
            cost_usd = _usage.run_total(rid)
        except Exception:
            cost_usd = 0.0

    event("run.end", status=status, cost_usd=cost_usd, **fields)

    meta = _current_run_meta.get() or {}
    meta["ended"] = datetime.now(timezone.utc).isoformat()
    meta["status"] = status
    meta["cost_usd"] = float(cost_usd or 0.0)
    for k in ("plan_path", "scene_count"):
        if k in fields and meta.get(k) is None:
            meta[k] = fields[k]
    # Only persist the index row when a real output_dir was bound — otherwise
    # this is a unit-test run with no on-disk artifact to point at.
    if meta.get("output_dir"):
        _append_run_index(meta)

    _current_run_id.set(None)
    _current_log_path.set(None)
    _current_buffer.set(None)
    _current_run_meta.set(None)


def record_run_meta(**fields: Any) -> None:
    """Stash run-level metadata (plan_path, scene_count, ...) for the index row."""
    meta = _current_run_meta.get()
    if meta is None:
        return
    for k, v in fields.items():
        meta[k] = v


def current_run_id() -> str | None:
    return _current_run_id.get()


def current_log_path() -> Path | None:
    return _current_log_path.get()


def event(event_name: str, *, level: str = "INFO", **fields: Any) -> None:
    """Append one JSON line to the active run's log.

    Before `bind_output_dir` is called, events are buffered in memory.
    Silent no-op if no run is active.
    """
    rid = _current_run_id.get()
    if rid is None:
        return
    rec: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": rid,
        "level": level,
        "event": event_name,
    }
    for k, v in fields.items():
        rec[k] = _coerce(v)

    path = _current_log_path.get()
    if path is None:
        # Pre-output_dir: buffer in memory.
        buf = _current_buffer.get()
        if buf is not None:
            buf.append(rec)
        return
    try:
        with path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        # Fail-soft: never let logging crash the pipeline.
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


# --------------------------------------------------------------------------
# Run index (~/.parallax/runs.ndjson)
# --------------------------------------------------------------------------

def _append_run_index(meta: dict[str, Any]) -> None:
    path = runs_index_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_run_index() -> list[dict[str, Any]]:
    """Read every row from `~/.parallax/runs.ndjson`. Newest last."""
    path = runs_index_path()
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def find_run(spec: str) -> dict[str, Any] | None:
    """Resolve a spec to an index row.

    Accepts:
      - "latest" → most recently appended row
      - 6-hex short id → matching row (most recent if multiple)
      - full run_id → matching row
    """
    rows = load_run_index()
    if not rows:
        return None
    if spec == "latest":
        return rows[-1]
    # Exact run_id match (most recent first).
    for row in reversed(rows):
        if row.get("run_id") == spec:
            return row
    # 6-hex short id.
    if len(spec) == 6:
        for row in reversed(rows):
            if row.get("short") == spec:
                return row
    return None


def tail(spec: str, *, follow: bool = False) -> int:
    """Stream raw NDJSON for the resolved run. Returns 0 on success, 1 if not found."""
    import sys
    import time as _time

    row = find_run(spec)
    if row is None:
        print(f"runlog: no such run: {spec}", file=sys.stderr)
        return 1
    out_dir = row.get("output_dir")
    if not out_dir:
        print(f"runlog: run {row.get('run_id')} has no output_dir in index", file=sys.stderr)
        return 1
    path = Path(out_dir) / "run.log"
    if not path.exists():
        print(f"runlog: log file missing: {path}", file=sys.stderr)
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
