"""Tests for concurrent stage_stills and stage_animate.

Guards:
- stage_stills with N unlocked scenes produces N still_paths with no gaps.
- _lock_field_in_plan called concurrently from N threads does not corrupt plan.yaml.
- stage_stills log includes a summary line mentioning "concurrent".
- stage_animate with N animate scenes produces N clip_paths and locks all.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
import yaml

from parallax import runlog
from parallax.stages import (
    PipelineState,
    SceneRuntime,
    _lock_field_in_plan,
    stage_animate,
    stage_stills,
)
from parallax.settings import ProductionMode, Settings


@pytest.fixture
def bound_run(tmp_path: Path):
    runlog.start_run("parallel-stages-test")
    runlog.bind_output_dir(tmp_path)
    yield tmp_path
    runlog.end_run()


def _make_settings(tmp_path: Path, log_lines: list[str] | None = None) -> Settings:
    folder = tmp_path / "project"
    folder.mkdir(exist_ok=True)
    plan_path = folder / "plan.yaml"
    plan_path.write_text("scenes: []\n")

    def _emit(event_type: str, payload: dict) -> None:
        if event_type == "log" and log_lines is not None:
            log_lines.append(payload.get("msg", ""))

    return Settings(
        folder=folder,
        plan_path=plan_path,
        concept_prefix="",
        image_model="draft",
        video_model="mid",
        aspect="9:16",
        resolution="1080x1920",
        animate_resolution="480x854",
        video_width=1080,
        video_height=1920,
        res_scale=1.0,
        voice="alloy",
        voice_model="tts-mini",
        voice_speed=1.0,
        style=None,
        style_hint=None,
        caption_style="default",
        fontsize=48,
        words_per_chunk=3,
        caption_animation_override=None,
        caption_shift_s=0.0,
        skip_captions=False,
        headline=None,
        headline_fontsize=None,
        headline_bg=None,
        headline_color=None,
        character_image=None,
        avatar_cfg=None,
        stills_only=False,
        mode=ProductionMode.TEST,
        events=_emit,
        run_id="test-run-parallel",
    )


def _make_state(tmp_path: Path) -> PipelineState:
    out = tmp_path / "output"
    out.mkdir(exist_ok=True)
    assets = tmp_path / "assets"
    assets.mkdir(exist_ok=True)
    (out / "stills").mkdir(exist_ok=True)
    (out / "video").mkdir(exist_ok=True)
    (out / "audio").mkdir(exist_ok=True)
    return PipelineState(
        out_dir=str(out),
        assets_dir=str(assets),
        stills_dir=str(out / "stills"),
        video_dir=str(out / "video"),
        audio_dir=str(out / "audio"),
        version=1,
        short_id="abc123",
        convention_name="project-v1-abc123.mp4",
    )


def _base_plan(n: int) -> dict:
    return {
        "scenes": [
            {"index": i, "prompt": f"scene {i}", "vo_text": f"vo {i}"}
            for i in range(n)
        ]
    }


# ---------------------------------------------------------------------------
# _lock_field_in_plan — concurrent safety
# ---------------------------------------------------------------------------

def test_concurrent_plan_locking_no_corruption(tmp_path: Path):
    """N threads locking different scenes concurrently must not corrupt plan.yaml."""
    n = 8
    folder = tmp_path / "project"
    folder.mkdir()
    plan_path = folder / "plan.yaml"
    plan = {"scenes": [{"index": i, "vo_text": f"s{i}"} for i in range(n)]}
    plan_path.write_text(yaml.dump(plan))

    errors: list[Exception] = []

    def lock_scene(idx: int) -> None:
        try:
            _lock_field_in_plan(
                plan_path, plan, idx, "still_path",
                str(folder / f"still_{idx}.png"), folder,
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=lock_scene, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent lock raised: {errors}"

    on_disk = yaml.safe_load(plan_path.read_text())
    for i in range(n):
        scene = next(s for s in on_disk["scenes"] if s["index"] == i)
        assert scene.get("still_path") == f"still_{i}.png", (
            f"scene {i} still_path missing or wrong: {scene}"
        )


# ---------------------------------------------------------------------------
# stage_stills — concurrent generation in TEST_MODE
# ---------------------------------------------------------------------------

def test_stage_stills_generates_all_scenes(tmp_path: Path, monkeypatch, bound_run):
    """stage_stills in TEST_MODE must populate still_path for every scene."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    n = 4
    settings = _make_settings(tmp_path)
    state = _make_state(tmp_path)
    plan = _base_plan(n)
    settings.plan_path.write_text(yaml.dump(plan))

    stage_stills(plan, settings, state)

    assert len(state.scenes) == n
    for scene_rt in state.scenes:
        assert scene_rt.still_path, f"scene {scene_rt.index} missing still_path"
        assert Path(scene_rt.still_path).exists(), f"still not on disk: {scene_rt.still_path}"


def test_stage_stills_log_includes_concurrent_summary(tmp_path: Path, monkeypatch, bound_run):
    """stage_stills must emit a summary log line mentioning 'concurrent'."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    n = 3
    log_lines: list[str] = []
    settings = _make_settings(tmp_path, log_lines=log_lines)
    state = _make_state(tmp_path)
    plan = _base_plan(n)
    settings.plan_path.write_text(yaml.dump(plan))

    stage_stills(plan, settings, state)

    summary = [l for l in log_lines if "concurrent" in l]
    assert summary, f"No 'concurrent' summary line found in logs: {log_lines}"


def test_stage_stills_locks_plan_for_generated_scenes(tmp_path: Path, monkeypatch, bound_run):
    """plan.yaml must have still_path locked for every generated scene."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    n = 3
    settings = _make_settings(tmp_path)
    state = _make_state(tmp_path)
    plan = _base_plan(n)
    settings.plan_path.write_text(yaml.dump(plan))

    stage_stills(plan, settings, state)

    on_disk = yaml.safe_load(settings.plan_path.read_text())
    for s in on_disk["scenes"]:
        assert s.get("still_path"), f"scene {s['index']} not locked in plan.yaml"


# ---------------------------------------------------------------------------
# stage_animate — concurrent animation in TEST_MODE
# ---------------------------------------------------------------------------

def test_stage_animate_generates_all_animate_scenes(tmp_path: Path, monkeypatch, bound_run):
    """stage_animate in TEST_MODE must populate clip_path for every animate=true scene."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    n = 3
    settings = _make_settings(tmp_path)
    state = _make_state(tmp_path)
    plan = _base_plan(n)
    settings.plan_path.write_text(yaml.dump(plan))

    stills_dir = Path(state.stills_dir)
    scenes: list[SceneRuntime] = []
    for i in range(n):
        fake_still = stills_dir / f"still_{i:02d}.png"
        fake_still.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        rt = SceneRuntime(
            index=i, shot_type="broll", vo_text=f"vo {i}", prompt=f"p {i}",
            still_path=str(fake_still), aspect="9:16",
        )
        rt.animate = True
        scenes.append(rt)
    state.scenes = scenes

    stage_animate(plan, settings, state)

    for rt in state.scenes:
        assert rt.clip_path, f"scene {rt.index} missing clip_path"
        assert Path(rt.clip_path).exists(), f"clip not on disk: {rt.clip_path}"


def test_stage_animate_locks_clips_in_plan(tmp_path: Path, monkeypatch, bound_run):
    """stage_animate must lock clip_path for all animated scenes in plan.yaml."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    n = 2
    settings = _make_settings(tmp_path)
    state = _make_state(tmp_path)
    plan = _base_plan(n)
    settings.plan_path.write_text(yaml.dump(plan))

    stills_dir = Path(state.stills_dir)
    scenes: list[SceneRuntime] = []
    for i in range(n):
        fake_still = stills_dir / f"still_{i:02d}.png"
        fake_still.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        rt = SceneRuntime(
            index=i, shot_type="broll", vo_text="", prompt="",
            still_path=str(fake_still), aspect="9:16",
        )
        rt.animate = True
        scenes.append(rt)
    state.scenes = scenes

    stage_animate(plan, settings, state)

    on_disk = yaml.safe_load(settings.plan_path.read_text())
    for s in on_disk["scenes"]:
        assert s.get("clip_path"), f"scene {s['index']} clip_path not locked in plan.yaml"
