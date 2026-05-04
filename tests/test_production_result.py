"""Tests for ProductionResult structured return value from run_plan.

Covers: error paths (bad folder, bad plan path, malformed YAML, no scenes)
and success paths (test-mode full run, stills-only run).
"""

from __future__ import annotations

import os

import pytest
import yaml

from parallax.produce import ProductionResult, run_plan


@pytest.fixture(autouse=True)
def _set_log_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))


# --------------------------------------------------------------------------
# Error paths
# --------------------------------------------------------------------------

def test_invalid_folder_returns_error(tmp_path):
    folder = tmp_path / "does_not_exist"
    plan_path = tmp_path / "plan.yaml"
    result = run_plan(folder=folder, plan_path=plan_path)
    assert isinstance(result, ProductionResult)
    assert result.status == "error"
    assert result.error is not None
    assert "folder not found" in result.error


def test_invalid_plan_path_returns_error(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    plan_path = tmp_path / "missing_plan.yaml"
    result = run_plan(folder=folder, plan_path=plan_path)
    assert isinstance(result, ProductionResult)
    assert result.status == "error"
    assert result.error is not None
    assert "plan file not found" in result.error


def test_malformed_plan_yaml_returns_error(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    plan_path = tmp_path / "plan.yaml"
    # Write syntactically broken YAML with a structural error that Plan.from_yaml rejects
    plan_path.write_text("scenes: not_a_list\n")
    result = run_plan(folder=folder, plan_path=plan_path)
    assert isinstance(result, ProductionResult)
    assert result.status == "error"
    assert result.error is not None


def test_empty_scenes_returns_error(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump({"voice": "nova", "scenes": []}))
    result = run_plan(folder=folder, plan_path=plan_path)
    assert isinstance(result, ProductionResult)
    assert result.status == "error"
    assert result.error is not None
    assert "no scenes" in result.error


# --------------------------------------------------------------------------
# Success paths (test mode — no real API calls)
# --------------------------------------------------------------------------

def test_full_run_returns_ok_result(monkeypatch, tmp_path):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")

    folder = tmp_path / "0001_prod_result_test"
    folder.mkdir()
    plan = {
        "voice": "nova",
        "image_model": "mid",
        "video_model": "mid",
        "captions": "skip",
        "scenes": [
            {"index": 0, "shot_type": "broll",
             "vo_text": "A typed pipeline.", "prompt": "A still."},
        ],
    }
    plan_path = folder / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan))

    result = run_plan(folder=folder, plan_path=plan_path)

    assert isinstance(result, ProductionResult)
    assert result.status == "ok", f"unexpected error: {result.error}"
    assert result.run_id is not None
    assert result.output_dir is not None
    assert result.output_dir.is_dir()
    assert result.final_video is not None
    assert result.cost_usd == 0.0
    assert result.error is None


def test_stills_only_run_returns_ok_with_stills_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")

    folder = tmp_path / "0002_stills_only_result"
    folder.mkdir()
    plan = {
        "voice": "nova",
        "image_model": "mid",
        "captions": "skip",
        "stills_only": True,
        "scenes": [
            {"index": 0, "shot_type": "broll",
             "vo_text": "Stills only.", "prompt": "A scene."},
        ],
    }
    plan_path = folder / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan))

    result = run_plan(folder=folder, plan_path=plan_path)

    assert isinstance(result, ProductionResult)
    assert result.status == "ok", f"unexpected error: {result.error}"
    assert result.run_id is not None
    assert result.output_dir is not None
    assert result.stills_dir is not None
    assert result.final_video is None
    assert result.cost_usd == 0.0
    assert result.error is None
