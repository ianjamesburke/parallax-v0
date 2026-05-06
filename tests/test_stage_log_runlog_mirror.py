"""Stage `_log()` calls mirror into the runlog as `stage.log` events.

Guards the contract that `verify_suite`'s `run_log.must_contain` can assert
on stage-level activity. The human-readable stdout via `Settings.events` is
preserved unchanged — the runlog mirror is additive.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parallax import runlog
from parallax.settings import Settings
from parallax.stages import _log


@pytest.fixture
def _bound_run(tmp_path: Path):
    """Start a runlog run and bind it to a tmp output dir."""
    runlog.start_run("stage-log-mirror-test")
    runlog.bind_output_dir(tmp_path)
    yield tmp_path
    runlog.end_run()


def _read_log_events(out_dir: Path) -> list[dict]:
    log_path = out_dir / "run.log"
    assert log_path.exists(), f"run.log was not created at {log_path}"
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def test_log_emits_stage_log_event_into_runlog(_bound_run, capsys):
    captured: list[tuple[str, dict]] = []

    settings = Settings(
        folder=Path("/tmp"),
        plan_path=Path("/tmp/plan.yaml"),
        concept_prefix="",
        image_model="seedream",
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
        product_image=None,
        avatar_cfg=None,
        stills_only=False,
        events=lambda ev, fields: captured.append((ev, fields)),
    )

    _log(settings, "align_scenes — start")

    # Stdout emitter still fires (unchanged contract).
    assert captured == [("log", {"msg": "align_scenes — start"})]

    # Runlog now carries a structured `stage.log` event with the same msg.
    events = _read_log_events(_bound_run)
    stage_logs = [e for e in events if e.get("event") == "stage.log"]
    assert len(stage_logs) == 1, f"expected one stage.log event, got: {events}"
    assert stage_logs[0]["msg"] == "align_scenes — start"
    assert stage_logs[0]["level"] == "INFO"


def test_log_no_runlog_active_is_noop(capsys):
    """`_log` must not raise when called outside a run (e.g. early planner code paths)."""
    captured: list[tuple[str, dict]] = []

    settings = Settings(
        folder=Path("/tmp"),
        plan_path=Path("/tmp/plan.yaml"),
        concept_prefix="",
        image_model="seedream",
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
        product_image=None,
        avatar_cfg=None,
        stills_only=False,
        events=lambda ev, fields: captured.append((ev, fields)),
    )

    # No active run — runlog.event() is a silent no-op; the stdout emitter still fires.
    _log(settings, "no run bound")
    assert captured == [("log", {"msg": "no run bound"})]
