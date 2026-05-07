"""Tests for --words / words_path caching in audio commands and stage_voiceover."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import parallax.audio as audio_mod
import parallax.whisper_backend as wb_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wav(path: Path) -> Path:
    """Generate a 2-second silent wav at 44100 Hz via ffmpeg."""
    wav = path / "test.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", "2", str(wav)],
        check=True,
        capture_output=True,
    )
    return wav


def _stub_words() -> list[dict]:
    return [
        {"word": "hello", "start": 0.1, "end": 0.4},
        {"word": "world", "start": 0.5, "end": 0.9},
    ]


# ---------------------------------------------------------------------------
# 1. transcribe_words with preloaded words skips transcribe_wav
# ---------------------------------------------------------------------------

class TestTranscribeWordsWithPreloadedWords:
    def test_skips_transcribe_wav(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)
        out = tmp_path / "words.json"
        words = _stub_words()

        with patch.object(wb_mod, "transcribe_wav") as mock_transcribe:
            result = audio_mod.transcribe_words(str(wav), str(out), words=words)

        mock_transcribe.assert_not_called()
        assert result == words

    def test_writes_correct_json(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)
        out = tmp_path / "words.json"
        words = _stub_words()

        with patch.object(wb_mod, "transcribe_wav"):
            audio_mod.transcribe_words(str(wav), str(out), words=words)

        data = json.loads(out.read_text())
        assert data["words"] == words
        assert data["total_duration_s"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# 2. cap_pauses with preloaded words skips forced_align
# ---------------------------------------------------------------------------

class TestCapPausesWithPreloadedWords:
    def test_skips_align_words(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)
        out = tmp_path / "out.wav"
        # Words with no large gaps — no cuts will be made, output is just re-encoded.
        words = _stub_words()

        import parallax.forced_align as fa_mod
        with patch.object(fa_mod, "align_words") as mock_align:
            audio_mod.cap_pauses(str(wav), str(out), words=words)

        mock_align.assert_not_called()
        assert out.exists()


# ---------------------------------------------------------------------------
# 3. stage_voiceover reuses cached vo_words.json
# ---------------------------------------------------------------------------

class TestStageVoiceoverReusesCachedVoWords:
    def test_reuses_cached_words_no_forced_align(self, tmp_path: Path) -> None:
        from dataclasses import dataclass, field
        from typing import Any
        from parallax.stages import stage_voiceover, PipelineState
        from parallax.settings import Settings, ProductionMode

        # Create a fake audio wav next to which vo_words.json will sit.
        wav = _make_wav(tmp_path)
        words = _stub_words()
        words_file = wav.with_name("vo_words.json")
        words_file.write_text(json.dumps({"words": words, "total_duration_s": 0.9}))

        # Build minimal Settings — only fields stage_voiceover touches.
        settings = Settings(
            folder=tmp_path,
            plan_path=tmp_path / "plan.yaml",
            concept_prefix="",
            image_model="test",
            video_model="test",
            aspect="9:16",
            resolution="720x1280",
            animate_resolution="480x854",
            video_width=720,
            video_height=1280,
            res_scale=1.0,
            voice="nova",
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
        )

        plan = {"audio_path": str(wav)}
        state = PipelineState()

        import parallax.forced_align as fa_mod
        with patch.object(fa_mod, "align_words") as mock_align:
            result = stage_voiceover(plan, settings, state)

        mock_align.assert_not_called()
        # stage_voiceover writes to state, not back to the plan dict
        assert state.words_path is not None
        assert state.words_path.endswith("vo_words.json")


# ---------------------------------------------------------------------------
# 4. CLI cap-pauses parser accepts --words
# ---------------------------------------------------------------------------

class TestCliCapPausesAcceptsWordsArg:
    def test_words_arg_parsed(self, tmp_path) -> None:
        from parallax import cli
        dummy_in = tmp_path / "in.wav"
        dummy_in.write_bytes(b"")
        dummy_out = tmp_path / "out.wav"
        words_file = tmp_path / "foo.json"
        words_file.write_text("[]")
        import parallax.audio as audio_mod
        import unittest.mock as mock
        def fake_cap_pauses(**kw):
            captured["words"] = kw.get("words")
            return {"gaps_trimmed": 0, "max_gap_s": 0.75,
                    "original_duration_s": 1.0, "new_duration_s": 1.0,
                    "seconds_removed": 0.0, "output": str(dummy_out)}
        captured = {}
        with mock.patch.object(audio_mod, "cap_pauses", side_effect=fake_cap_pauses):
            rc = cli.main([
                "audio", "cap-pauses",
                "--input", str(dummy_in),
                "--output", str(dummy_out),
                "--words", str(words_file),
            ])
        assert rc == 0


# ---------------------------------------------------------------------------
# 5. CLI transcribe parser accepts --words
# ---------------------------------------------------------------------------

class TestCliTranscribeAcceptsWordsArg:
    def test_words_arg_parsed(self, tmp_path) -> None:
        from parallax import cli
        dummy_in = tmp_path / "in.wav"
        dummy_in.write_bytes(b"")
        out_path = tmp_path / "words.json"
        words_file = tmp_path / "foo.json"
        words_file.write_text('[{"word": "hi", "start": 0.0, "end": 0.4}]')
        import parallax.audio as audio_mod
        import unittest.mock as mock
        with mock.patch.object(audio_mod, "transcribe_words",
                               return_value=[{"word": "hi", "start": 0.0, "end": 0.4}]):
            rc = cli.main([
                "audio", "transcribe",
                str(dummy_in),
                "--out", str(out_path),
                "--words", str(words_file),
            ])
        assert rc == 0
