"""Tests for the whisperx-or-faster-whisper backend selection in whisper_backend,
forced_align, and audio.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import parallax.whisper_backend as wb_mod
import parallax.forced_align as fa_mod
import parallax.audio as audio_mod


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


def _stub_word_list() -> list[dict]:
    return [
        {"word": "hello", "start": 0.1, "end": 0.4},
        {"word": "world", "start": 0.5, "end": 0.9},
    ]


# ---------------------------------------------------------------------------
# whisper_backend — backend selection (shared path)
# ---------------------------------------------------------------------------

class TestWhisperBackendSelection:
    def test_uses_whisperx_when_available(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)
        mock_wx = MagicMock()
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"segments": [], "language": "en"}
        mock_wx.load_model.return_value = mock_model
        mock_wx.load_audio.return_value = MagicMock()
        mock_wx.load_align_model.return_value = (MagicMock(), MagicMock())
        mock_wx.align.return_value = {
            "word_segments": [
                {"word": "hello", "start": 0.1, "end": 0.4},
                {"word": "world", "start": 0.5, "end": 0.9},
            ]
        }

        with patch.object(wb_mod, "_HAS_WHISPERX", True), \
             patch.object(wb_mod, "_whisperx", mock_wx):
            words = wb_mod.transcribe_wav(str(wav))

        mock_wx.load_model.assert_called_once()
        assert words == _stub_word_list()

    def test_falls_back_to_faster_whisper_when_whisperx_absent(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)

        mock_word = SimpleNamespace(word=" hello ", start=0.1, end=0.4)
        mock_word2 = SimpleNamespace(word=" world ", start=0.5, end=0.9)
        mock_segment = SimpleNamespace(words=[mock_word, mock_word2])
        mock_info = SimpleNamespace(language="en")

        mock_fw_model = MagicMock()
        mock_fw_model.transcribe.return_value = ([mock_segment], mock_info)
        MockWhisperModel = MagicMock(return_value=mock_fw_model)

        with patch.object(wb_mod, "_HAS_WHISPERX", False), \
             patch("faster_whisper.WhisperModel", MockWhisperModel):
            words = wb_mod.transcribe_wav(str(wav))

        assert words == _stub_word_list()

    def test_fallback_logs_warning(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)

        mock_word = SimpleNamespace(word=" hi ", start=0.1, end=0.3)
        mock_segment = SimpleNamespace(words=[mock_word])
        mock_info = SimpleNamespace(language="en")
        mock_fw_model = MagicMock()
        mock_fw_model.transcribe.return_value = ([mock_segment], mock_info)
        MockWhisperModel = MagicMock(return_value=mock_fw_model)

        with patch.object(wb_mod.log, "warning") as mock_warn, \
             patch.object(wb_mod, "_HAS_WHISPERX", False), \
             patch("faster_whisper.WhisperModel", MockWhisperModel):
            wb_mod.transcribe_wav(str(wav))

        mock_warn.assert_called_once()
        assert "faster-whisper" in mock_warn.call_args[0][0]

    def test_output_shape_is_canonical(self, tmp_path: Path) -> None:
        """Both backends must return [{word, start, end}] with the right types."""
        wav = _make_wav(tmp_path)
        mock_wx = MagicMock()
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"segments": [], "language": "en"}
        mock_wx.load_model.return_value = mock_model
        mock_wx.load_audio.return_value = MagicMock()
        mock_wx.load_align_model.return_value = (MagicMock(), MagicMock())
        mock_wx.align.return_value = {
            "word_segments": [{"word": "  test  ", "start": 0.0, "end": 1.0}]
        }

        with patch.object(wb_mod, "_HAS_WHISPERX", True), \
             patch.object(wb_mod, "_whisperx", mock_wx):
            words = wb_mod.transcribe_wav(str(wav))

        assert isinstance(words, list)
        assert all(isinstance(w["word"], str) for w in words)
        assert all(isinstance(w["start"], float) for w in words)
        assert all(isinstance(w["end"], float) for w in words)
        assert all(w["word"] == w["word"].strip() for w in words)

    def test_skips_words_with_missing_timestamps(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)
        mock_wx = MagicMock()
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"segments": [], "language": "en"}
        mock_wx.load_model.return_value = mock_model
        mock_wx.load_audio.return_value = MagicMock()
        mock_wx.load_align_model.return_value = (MagicMock(), MagicMock())
        mock_wx.align.return_value = {
            "word_segments": [
                {"word": "ok", "start": 0.1, "end": 0.3},
                {"word": "uh", "start": None, "end": None},  # should be skipped
            ]
        }

        with patch.object(wb_mod, "_HAS_WHISPERX", True), \
             patch.object(wb_mod, "_whisperx", mock_wx):
            words = wb_mod.transcribe_wav(str(wav))

        assert len(words) == 1
        assert words[0]["word"] == "ok"


# ---------------------------------------------------------------------------
# forced_align — delegates to whisper_backend
# ---------------------------------------------------------------------------

class TestForcedAlignDelegation:
    def test_align_words_uses_whisperx_via_backend(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)
        mock_wx = MagicMock()
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"segments": [], "language": "en"}
        mock_wx.load_model.return_value = mock_model
        mock_wx.load_audio.return_value = MagicMock()
        mock_wx.load_align_model.return_value = (MagicMock(), MagicMock())
        mock_wx.align.return_value = {
            "word_segments": [
                {"word": "hello", "start": 0.1, "end": 0.4},
                {"word": "world", "start": 0.5, "end": 0.9},
            ]
        }

        with patch.object(wb_mod, "_HAS_WHISPERX", True), \
             patch.object(wb_mod, "_whisperx", mock_wx):
            words = fa_mod.align_words(wav)

        mock_wx.load_model.assert_called_once()
        assert words == _stub_word_list()

    def test_align_words_falls_back_to_faster_whisper(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)

        mock_word = SimpleNamespace(word=" hello ", start=0.1, end=0.4)
        mock_word2 = SimpleNamespace(word=" world ", start=0.5, end=0.9)
        mock_segment = SimpleNamespace(words=[mock_word, mock_word2])
        mock_info = SimpleNamespace(language="en")

        mock_fw_model = MagicMock()
        mock_fw_model.transcribe.return_value = ([mock_segment], mock_info)
        MockWhisperModel = MagicMock(return_value=mock_fw_model)

        with patch.object(wb_mod, "_HAS_WHISPERX", False), \
             patch("faster_whisper.WhisperModel", MockWhisperModel):
            words = fa_mod.align_words(wav)

        assert words == _stub_word_list()

    def test_fallback_logs_warning_via_backend(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)

        mock_word = SimpleNamespace(word=" hi ", start=0.1, end=0.3)
        mock_segment = SimpleNamespace(words=[mock_word])
        mock_info = SimpleNamespace(language="en")
        mock_fw_model = MagicMock()
        mock_fw_model.transcribe.return_value = ([mock_segment], mock_info)
        MockWhisperModel = MagicMock(return_value=mock_fw_model)

        with patch.object(wb_mod.log, "warning") as mock_warn, \
             patch.object(wb_mod, "_HAS_WHISPERX", False), \
             patch("faster_whisper.WhisperModel", MockWhisperModel):
            fa_mod.align_words(wav)

        mock_warn.assert_called_once()
        assert "faster-whisper" in mock_warn.call_args[0][0]


# ---------------------------------------------------------------------------
# audio.transcribe_words — backend selection + file output
# ---------------------------------------------------------------------------

class TestTranscribeWordsBackendSelection:
    def test_uses_whisperx_when_available(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)
        out = tmp_path / "words.json"

        mock_wx = MagicMock()
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"segments": [], "language": "en"}
        mock_wx.load_model.return_value = mock_model
        mock_wx.load_audio.return_value = MagicMock()
        mock_wx.load_align_model.return_value = (MagicMock(), MagicMock())
        mock_wx.align.return_value = {
            "word_segments": [{"word": "hello", "start": 0.1, "end": 0.4}]
        }

        with patch.object(wb_mod, "_HAS_WHISPERX", True), \
             patch.object(wb_mod, "_whisperx", mock_wx):
            words = audio_mod.transcribe_words(str(wav), str(out))

        mock_wx.load_model.assert_called_once()
        assert words == [{"word": "hello", "start": 0.1, "end": 0.4}]
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["words"] == words
        assert "total_duration_s" in data

    def test_falls_back_to_faster_whisper(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)
        out = tmp_path / "words.json"

        mock_word = SimpleNamespace(word=" world ", start=0.5, end=0.9)
        mock_segment = SimpleNamespace(words=[mock_word])
        mock_info = SimpleNamespace(language="en")
        mock_fw_model = MagicMock()
        mock_fw_model.transcribe.return_value = ([mock_segment], mock_info)
        MockWhisperModel = MagicMock(return_value=mock_fw_model)

        with patch.object(wb_mod, "_HAS_WHISPERX", False), \
             patch("faster_whisper.WhisperModel", MockWhisperModel):
            words = audio_mod.transcribe_words(str(wav), str(out))

        assert words == [{"word": "world", "start": 0.5, "end": 0.9}]
        data = json.loads(out.read_text())
        assert data["words"] == words

    def test_writes_valid_json_structure(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)
        out = tmp_path / "out.json"

        mock_wx = MagicMock()
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"segments": [], "language": "en"}
        mock_wx.load_model.return_value = mock_model
        mock_wx.load_audio.return_value = MagicMock()
        mock_wx.load_align_model.return_value = (MagicMock(), MagicMock())
        mock_wx.align.return_value = {
            "word_segments": [
                {"word": "a", "start": 0.0, "end": 0.5},
                {"word": "b", "start": 0.6, "end": 1.0},
            ]
        }

        with patch.object(wb_mod, "_HAS_WHISPERX", True), \
             patch.object(wb_mod, "_whisperx", mock_wx):
            audio_mod.transcribe_words(str(wav), str(out))

        data = json.loads(out.read_text())
        assert "words" in data
        assert "total_duration_s" in data
        assert data["total_duration_s"] == 1.0
