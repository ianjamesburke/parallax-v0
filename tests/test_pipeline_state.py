"""Typed pipeline state — PipelineState and SceneRuntime contracts.

Guards that:
  - PipelineState fields default correctly (no dict access required)
  - SceneRuntime optional fields default to None
  - Accessing a nonexistent attribute raises AttributeError (not a silent None)
  - A full test-mode produce run still produces a video (state threaded correctly)
  - A stills-only run populates state fields and writes cost.json
  - stage_scan populates state directly (no plan["_runtime"] residue)
"""

from __future__ import annotations

import json

import pytest
import yaml

from parallax.stages import PipelineState, SceneRuntime


# --------------------------------------------------------------------------
# Dataclass contract tests
# --------------------------------------------------------------------------

def test_pipeline_state_defaults():
    state = PipelineState()
    assert state.scenes == []
    assert state.aligned == []
    assert state.current_video is None
    assert state.audio_path is None
    assert state.words_path is None
    assert state.vo_result is None
    assert state.manifest_path is None
    assert state.run_cost == 0.0
    assert state.out_dir == ""
    assert state.stills_dir == ""
    assert state.audio_dir == ""
    assert state.video_dir == ""
    assert state.aligned_json == ""


def test_scene_runtime_required_fields():
    s = SceneRuntime(
        index=0, shot_type="broll", vo_text="hello world",
        prompt="a cat on a rooftop", still_path="/tmp/cat.png", aspect="9:16",
    )
    assert s.index == 0
    assert s.shot_type == "broll"
    assert s.still_path == "/tmp/cat.png"
    assert s.aspect == "9:16"


def test_scene_runtime_optional_fields_default_to_none():
    s = SceneRuntime(
        index=1, shot_type="character", vo_text="text", prompt="p",
        still_path="/tmp/s.png", aspect="16:9",
    )
    assert s.animate is False
    assert s.clip_path is None
    assert s.video_model is None
    assert s.motion_prompt is None
    assert s.animate_resolution is None
    assert s.end_frame_path is None
    assert s.zoom_direction is None
    assert s.zoom_amount is None
    assert s.video_references is None


def test_pipeline_state_attribute_access_raises_on_nonexistent():
    """Typed state surfaces typos as AttributeError immediately, not KeyError later."""
    state = PipelineState()
    with pytest.raises(AttributeError):
        _ = state.nonexistent_field  # type: ignore[attr-defined]


def test_scene_runtime_attribute_access_raises_on_nonexistent():
    s = SceneRuntime(index=0, shot_type="broll", vo_text="", prompt="", still_path="", aspect="9:16")
    with pytest.raises(AttributeError):
        _ = s.nonexistent_field  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# stage_scan populates state (no plan["_runtime"] residue)
# --------------------------------------------------------------------------

def test_stage_scan_populates_state_not_plan(monkeypatch, tmp_path):
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))

    folder = tmp_path / "0001_state_test"
    folder.mkdir()

    from parallax import runlog
    from parallax.settings import resolve_settings, with_run_id
    from parallax.stages import PipelineState, stage_scan

    plan = {"scenes": [{"index": 0, "vo_text": "x", "prompt": "p"}]}
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan))

    run_id = runlog.start_run("scan-state-test")
    settings = resolve_settings(plan, folder, plan_path)
    settings = with_run_id(settings, run_id)

    state = PipelineState()
    stage_scan(plan, settings, state)
    runlog.end_run()

    assert state.out_dir != "", "state.out_dir must be populated by stage_scan"
    assert state.assets_dir != "", "state.assets_dir must be populated by stage_scan"
    assert state.assets_dir.endswith("/assets")
    assert state.version >= 1, "state.version must be >= 1"
    assert state.stills_dir.endswith("/stills")
    assert state.audio_dir.endswith("/audio")
    assert state.video_dir.endswith("/video")
    assert state.short_id != ""
    assert state.convention_name.endswith(".mp4")
    # plan must NOT carry _runtime — state is the only owner
    assert "_runtime" not in plan


# --------------------------------------------------------------------------
# Full produce run (test mode) — state threads all the way through
# --------------------------------------------------------------------------

def test_full_produce_in_test_mode_produces_video(monkeypatch, tmp_path):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))

    folder = tmp_path / "0002_pipeline_state_smoke"
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

    from parallax.produce import run_plan
    result = run_plan(folder=folder, plan_path=plan_path)
    assert result.status == "ok", f"unexpected error: {result.error}"

    candidates = list(folder.rglob(f"{folder.name}-v*.mp4"))
    assert candidates, f"no produced mp4 found under {folder}"
    # plan must NOT carry _runtime after the run
    final_plan = yaml.safe_load(plan_path.read_text())
    assert "_runtime" not in final_plan


# --------------------------------------------------------------------------
# Stills-only run — state threaded through stills-only branch
# --------------------------------------------------------------------------

def test_stills_only_run_writes_cost_json(monkeypatch, tmp_path):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))

    folder = tmp_path / "0003_stills_only_state"
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

    from parallax.produce import run_plan
    result = run_plan(folder=folder, plan_path=plan_path)
    assert result.status == "ok", f"unexpected error: {result.error}"

    # cost.json must exist under the output dir
    cost_files = list(folder.rglob("cost.json"))
    assert cost_files, "stills-only run must write cost.json"
    cost = json.loads(cost_files[0].read_text())
    assert "version" in cost
    assert cost["version"] >= 1
