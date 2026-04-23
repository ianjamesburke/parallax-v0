"""Per-call usage log.

Every `generate_image` call appends one JSON record to
`~/.parallax/usage.ndjson` (override via `PARALLAX_USAGE_LOG`). The file is
the single source of truth for duration and cost accounting across both
backends. Test-mode calls are recorded with `test_mode: true` and
`cost_usd: 0.0`; real runs carry the priced cost from pricing.MODELS.

summarize() aggregates by alias and by session for the `parallax usage`
subcommand.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def usage_log_path() -> Path:
    override = os.environ.get("PARALLAX_USAGE_LOG")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~/.parallax/usage.ndjson"))


@dataclass
class UsageRecord:
    ts: str
    session_id: str | None
    backend: str
    alias: str
    fal_id: str
    tier: str
    prompt_preview: str
    output_path: str
    duration_ms: int
    cost_usd: float
    test_mode: bool


def record(
    *,
    session_id: str | None,
    backend: str,
    alias: str,
    fal_id: str,
    tier: str,
    prompt: str,
    output_path: str,
    duration_ms: int,
    cost_usd: float,
    test_mode: bool,
) -> UsageRecord:
    """Append a single usage event to the NDJSON log."""
    rec = UsageRecord(
        ts=datetime.now(timezone.utc).isoformat(),
        session_id=session_id,
        backend=backend,
        alias=alias,
        fal_id=fal_id,
        tier=tier,
        prompt_preview=prompt[:120],
        output_path=output_path,
        duration_ms=duration_ms,
        cost_usd=cost_usd,
        test_mode=test_mode,
    )
    path = usage_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(asdict(rec)) + "\n")
    except OSError as e:
        raise RuntimeError(f"Failed to write usage record to {path}: {e}") from e
    return rec


def load_records(include_test: bool = False) -> list[dict[str, Any]]:
    path = usage_log_path()
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if not include_test and rec.get("test_mode"):
                    continue
                out.append(rec)
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Failed to read usage log {path}: {e}") from e
    return out


def session_total(session_id: str, include_test: bool = False) -> float:
    """Return total cost_usd for all records matching a given session_id."""
    records = load_records(include_test=include_test)
    return round(sum(float(r.get("cost_usd", 0.0)) for r in records if r.get("session_id") == session_id), 4)


def summarize(include_test: bool = False) -> dict[str, Any]:
    records = load_records(include_test=include_test)
    by_alias: dict[str, dict[str, Any]] = {}
    sessions: set[str] = set()
    total_calls = 0
    total_cost = 0.0
    total_ms = 0

    for rec in records:
        total_calls += 1
        total_cost += float(rec.get("cost_usd", 0.0))
        total_ms += int(rec.get("duration_ms", 0))
        sid = rec.get("session_id")
        if sid:
            sessions.add(sid)
        alias = rec.get("alias", "unknown")
        slot = by_alias.setdefault(
            alias, {"calls": 0, "cost_usd": 0.0, "duration_ms": 0, "tier": rec.get("tier", "")}
        )
        slot["calls"] += 1
        slot["cost_usd"] += float(rec.get("cost_usd", 0.0))
        slot["duration_ms"] += int(rec.get("duration_ms", 0))

    return {
        "total_calls": total_calls,
        "total_cost_usd": round(total_cost, 4),
        "total_duration_ms": total_ms,
        "session_count": len(sessions),
        "by_alias": by_alias,
        "include_test_mode": include_test,
        "log_path": str(usage_log_path()),
    }
