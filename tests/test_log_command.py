"""Tests for `parallax log` — summary view, list, level filter, follow.

The `log` subcommand replaced `tail` in the refactor-v3-logging pass. These
tests pin the contract: spec resolution, summary section visibility, level
filtering, list-from-index, and clear errors when a run isn't found.
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest


@pytest.fixture
def fresh_index(monkeypatch, tmp_path):
    """Redirect the run index to a tmp file and return its path."""
    idx = tmp_path / "runs.ndjson"
    monkeypatch.setenv("PARALLAX_RUNS_INDEX", str(idx))
    return idx


def _seed_run(idx_path: Path, *, run_id: str, output_dir: Path,
              cost_usd: float = 0.34, status: str = "ok",
              events: list[dict] | None = None) -> Path:
    """Append a row to the index and write a corresponding run.log."""
    short = run_id[-6:]
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "run.log"
    if events is None:
        events = [
            {"ts": "2026-04-29T20:00:00+00:00", "run_id": run_id, "level": "INFO",
             "event": "run.start"},
            {"ts": "2026-04-29T20:00:00+00:00", "run_id": run_id, "level": "INFO",
             "event": "plan.loaded", "scene_count": 2},
            {"ts": "2026-04-29T20:00:01+00:00", "run_id": run_id, "level": "DEBUG",
             "event": "stage.scan.start", "scene_count": 2},
            {"ts": "2026-04-29T20:00:01+00:00", "run_id": run_id, "level": "DEBUG",
             "event": "stage.scan.end", "duration_ms": 12, "scene_count": 2},
            {"ts": "2026-04-29T20:00:02+00:00", "run_id": run_id, "level": "INFO",
             "event": "run.end", "status": status, "cost_usd": cost_usd,
             "final_video": str(output_dir / "result.mp4")},
        ]
    with log_path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    row = {
        "run_id": run_id,
        "short": short,
        "started": "2026-04-29T20:00:00+00:00",
        "ended": "2026-04-29T20:00:02+00:00",
        "output_dir": str(output_dir),
        "plan_path": str(output_dir / "plan.yaml"),
        "scene_count": 2,
        "status": status,
        "cost_usd": cost_usd,
    }
    with idx_path.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return log_path


def _run_cli(argv: list[str], capsys) -> tuple[int, str, str]:
    from parallax.cli import main
    rc = main(argv)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_log_latest_resolves_most_recent_run(fresh_index, tmp_path, capsys):
    _seed_run(fresh_index, run_id="20260429T200000Z-aaaaaa",
              output_dir=tmp_path / "old")
    _seed_run(fresh_index, run_id="20260429T210000Z-bbbbbb",
              output_dir=tmp_path / "new")
    rc, out, _err = _run_cli(["log", "latest"], capsys)
    assert rc == 0
    assert "bbbbbb" in out
    assert "aaaaaa" not in out


def test_log_summary_contains_expected_sections(fresh_index, tmp_path, capsys):
    _seed_run(fresh_index, run_id="20260429T200000Z-c0ffee",
              output_dir=tmp_path / "run", cost_usd=0.42)
    rc, out, _err = _run_cli(["log", "latest"], capsys)
    assert rc == 0
    assert "run 20260429T200000Z-c0ffee" in out
    assert "plan         " in out
    assert "output       " in out
    assert "total cost   $0.42" in out
    assert "Stages" in out


def test_log_level_debug_includes_debug_entries(fresh_index, tmp_path, capsys):
    _seed_run(fresh_index, run_id="20260429T200000Z-debu99",
              output_dir=tmp_path / "run")
    rc, out, _err = _run_cli(["log", "latest", "--level", "debug"], capsys)
    assert rc == 0
    assert "Debug events" in out
    assert "stage.scan.start" in out


def test_log_no_summary_emits_raw_ndjson(fresh_index, tmp_path, capsys):
    _seed_run(fresh_index, run_id="20260429T200000Z-rawraw",
              output_dir=tmp_path / "run")
    rc, out, _err = _run_cli(["log", "latest", "--no-summary"], capsys)
    assert rc == 0
    # Should be raw JSON lines — no human-readable header.
    assert "Stages" not in out
    # INFO-level filter should drop DEBUG events by default.
    assert '"level": "DEBUG"' not in out
    assert '"event": "run.start"' in out


def test_log_list_returns_rows_from_index(fresh_index, tmp_path, capsys):
    _seed_run(fresh_index, run_id="20260429T200000Z-listaa",
              output_dir=tmp_path / "a")
    _seed_run(fresh_index, run_id="20260429T210000Z-listbb",
              output_dir=tmp_path / "b")
    rc, out, _err = _run_cli(["log", "list"], capsys)
    assert rc == 0
    assert "listaa" in out
    assert "listbb" in out


def test_log_missing_run_gives_clear_error(fresh_index, tmp_path, capsys):
    rc, _out, err = _run_cli(["log", "latest"], capsys)
    assert rc == 1
    assert "no run found" in err.lower() or "no such run" in err.lower() \
           or "no runs" in err.lower()
