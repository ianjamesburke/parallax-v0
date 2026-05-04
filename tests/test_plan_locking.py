"""Tests for `_lock_field_in_plan` — explicit plan asset locking.

Guards:
- Write failure raises RuntimeError and emits a runlog ERROR event.
- Path inside folder is normalized to relative.
- Path outside folder is kept as-is (absolute).
- Successful write mutates the in-memory plan dict and updates plan.yaml on disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from parallax import runlog
from parallax.stages import _lock_field_in_plan


def _read_log_events(out_dir: Path) -> list[dict]:
    log_path = out_dir / "run.log"
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


@pytest.fixture
def bound_run(tmp_path: Path):
    runlog.start_run("plan-lock-test")
    runlog.bind_output_dir(tmp_path)
    yield tmp_path
    runlog.end_run()


def _base_plan() -> dict:
    return {
        "scenes": [
            {"index": 0, "vo_text": "Hello"},
            {"index": 1, "vo_text": "World"},
        ]
    }


def test_write_failure_raises_and_logs_error(bound_run: Path, tmp_path: Path):
    """A YAML write failure must raise RuntimeError (not swallow silently)."""
    folder = tmp_path / "project"
    folder.mkdir()
    plan_path = folder / "plan.yaml"
    plan = _base_plan()
    asset = str(folder / "still.png")

    with patch("yaml.dump", side_effect=OSError("disk full")):
        with pytest.raises(RuntimeError, match="plan lock failed"):
            _lock_field_in_plan(plan_path, plan, 0, "still_path", asset, folder)

    events = _read_log_events(bound_run)
    error_events = [e for e in events if e.get("event") == "plan.lock.error"]
    assert len(error_events) == 1, f"expected one plan.lock.error event, got: {events}"
    assert error_events[0]["level"] == "ERROR"
    assert error_events[0]["scene"] == 0
    assert error_events[0]["field"] == "still_path"


def test_path_inside_folder_stored_as_relative(tmp_path: Path):
    """An asset path inside the project folder is written as a relative path."""
    folder = tmp_path / "project"
    folder.mkdir()
    plan_path = folder / "plan.yaml"
    plan_path.write_text(yaml.dump(_base_plan()))
    plan = _base_plan()

    asset = str(folder / "stills" / "scene_0.png")
    _lock_field_in_plan(plan_path, plan, 0, "still_path", asset, folder)

    assert plan["scenes"][0]["still_path"] == "stills/scene_0.png"
    on_disk = yaml.safe_load(plan_path.read_text())
    assert on_disk["scenes"][0]["still_path"] == "stills/scene_0.png"


def test_path_outside_folder_kept_absolute(tmp_path: Path):
    """An asset path outside the project folder is stored as-is."""
    folder = tmp_path / "project"
    folder.mkdir()
    plan_path = folder / "plan.yaml"
    plan_path.write_text(yaml.dump(_base_plan()))
    plan = _base_plan()

    outside = str(tmp_path / "shared" / "clip.mp4")
    _lock_field_in_plan(plan_path, plan, 1, "clip_path", outside, folder)

    assert plan["scenes"][1]["clip_path"] == outside
    on_disk = yaml.safe_load(plan_path.read_text())
    assert on_disk["scenes"][1]["clip_path"] == outside


def test_success_mutates_plan_and_writes_yaml(tmp_path: Path):
    """A successful lock mutates the in-memory dict and writes plan.yaml."""
    folder = tmp_path / "project"
    folder.mkdir()
    plan_path = folder / "plan.yaml"
    plan_path.write_text(yaml.dump(_base_plan()))
    plan = _base_plan()

    asset = str(folder / "output" / "scene_1.png")
    _lock_field_in_plan(plan_path, plan, 1, "still_path", asset, folder)

    assert plan["scenes"][1]["still_path"] == "output/scene_1.png"
    on_disk = yaml.safe_load(plan_path.read_text())
    assert on_disk["scenes"][1]["still_path"] == "output/scene_1.png"
    assert "vo_text" in on_disk["scenes"][0], "other plan fields must survive the write"


def test_no_active_run_write_failure_still_raises(tmp_path: Path):
    """Even without an active runlog run, a write failure raises RuntimeError."""
    folder = tmp_path / "project"
    folder.mkdir()
    plan_path = folder / "plan.yaml"
    plan = _base_plan()
    asset = str(folder / "still.png")

    with patch("yaml.dump", side_effect=OSError("permission denied")):
        with pytest.raises(RuntimeError, match="plan lock failed"):
            _lock_field_in_plan(plan_path, plan, 0, "still_path", asset, folder)
