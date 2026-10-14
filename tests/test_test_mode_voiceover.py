"""Regression: PARALLAX_TEST_MODE must not run WhisperX on the silent stub voiceover.

The mock voiceover supplies synthetic word timings; stage_voiceover must use
them instead of calling forced_align.align_words (which raises "produced 0
words" on silence).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from parallax import forced_align
from parallax.settings import ProductionMode, Settings
from parallax.stages import PipelineState, stage_voiceover


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        folder=tmp_path, plan_path=tmp_path / "plan.yaml",
        concept_prefix="", image_model="draft", video_model="mid",
        aspect="9:16", resolution="720x1280", animate_resolution="480x854",
        video_width=720, video_height=1280, res_scale=1.0,
        voice="alloy", voice_model="tts-mini", voice_speed=1.0,
        style=None, style_hint=None, caption_style="default", fontsize=48,
        words_per_chunk=3, caption_animation_override=None, caption_shift_s=0.0,
        skip_captions=False, headline=None, headline_fontsize=None,
        headline_bg=None, headline_color=None, character_image=None, product_image=None,
        avatar_cfg=None, stills_only=False, mode=ProductionMode.TEST,
        events=lambda *_: None, run_id="test-run",
    )


def _state(tmp_path: Path) -> PipelineState:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    return PipelineState(
        out_dir=str(tmp_path), assets_dir=str(tmp_path),
        stills_dir=str(tmp_path), video_dir=str(tmp_path),
        audio_dir=str(audio_dir), version=1, short_id="test",
        convention_name="test.mp4",
    )


@pytest.fixture(autouse=True)
def _no_whisperx(monkeypatch):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")

    def _boom(*_a, **_k):
        raise AssertionError("forced_align.align_words must not run in test mode")

    monkeypatch.setattr(forced_align, "align_words", _boom)


def test_generated_voiceover_uses_mock_word_timings(tmp_path):
    plan = {"scenes": [{"index": 0, "vo_text": "hello brave new world"}]}
    state = _state(tmp_path)
    stage_voiceover(plan, _settings(tmp_path), state)
    assert state.vo_result is not None
    assert [w["word"] for w in state.vo_result["words"]] == ["hello", "brave", "new", "world"]


def test_existing_audio_without_words_uses_mock_word_timings(tmp_path):
    audio = tmp_path / "vo.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "anullsrc=cl=mono:r=44100", "-t", "2.0",
         "-c:a", "pcm_s16le", str(audio)],
        check=True, capture_output=True,
    )
    plan = {"audio_path": str(audio), "scenes": [{"index": 0, "vo_text": "hello world"}]}
    state = _state(tmp_path)
    stage_voiceover(plan, _settings(tmp_path), state)
    assert state.vo_result is not None
    assert [w["word"] for w in state.vo_result["words"]] == ["hello", "world"]
