"""Tests for the WhisperX backend in whisper_backend, forced_align, and audio."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import parallax.whisper_backend as wb_mod
import parallax.forced_align as fa_mod
import parallax.audio as audio_mod


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


def _mock_whisperx(word_segments: list[dict]) -> MagicMock:
    """Return a mock whisperx module that produces the given word_segments."""
    mock_wx = MagicMock()
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {"segments": [], "language": "en"}
    mock_wx.load_model.return_value = mock_model
    mock_wx.load_audio.return_value = MagicMock()
    mock_wx.load_align_model.return_value = (MagicMock(), MagicMock())
    mock_wx.align.return_value = {"word_segments": word_segments}
    return mock_wx


class TestWhisperBackendWhisperX:
    def test_transcribe_wav_calls_whisperx(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)
        mock_wx = _mock_whisperx([
            {"word": "hello", "start": 0.1, "end": 0.4},
            {"word": "world", "start": 0.5, "end": 0.9},
        ])
        with patch.object(wb_mod, "_whisperx", mock_wx):
            words = wb_mod.transcribe_wav(str(wav))
        mock_wx.load_model.assert_called_once()
        assert words == _stub_word_list()

    def test_output_shape_is_canonical(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)
        mock_wx = _mock_whisperx([{"word": "  test  ", "start": 0.0, "end": 1.0}])
        with patch.object(wb_mod, "_whisperx", mock_wx):
            words = wb_mod.transcribe_wav(str(wav))
        assert isinstance(words, list)
        assert all(isinstance(w["word"], str) for w in words)
        assert all(isinstance(w["start"], float) for w in words)
        assert all(isinstance(w["end"], float) for w in words)
        assert all(w["word"] == w["word"].strip() for w in words)

    def test_skips_words_with_missing_timestamps(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)
        mock_wx = _mock_whisperx([
            {"word": "ok", "start": 0.1, "end": 0.3},
            {"word": "uh", "start": None, "end": None},
        ])
        with patch.object(wb_mod, "_whisperx", mock_wx):
            words = wb_mod.transcribe_wav(str(wav))
        assert len(words) == 1
        assert words[0]["word"] == "ok"


class TestForcedAlignDelegation:
    def test_align_words_delegates_to_backend(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)
        mock_wx = _mock_whisperx([
            {"word": "hello", "start": 0.1, "end": 0.4},
            {"word": "world", "start": 0.5, "end": 0.9},
        ])
        with patch.object(wb_mod, "_whisperx", mock_wx):
            words = fa_mod.align_words(wav)
        mock_wx.load_model.assert_called_once()
        assert words == _stub_word_list()


class TestTranscribeWordsBackendSelection:
    def test_transcribe_words_uses_whisperx(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)
        out = tmp_path / "words.json"
        mock_wx = _mock_whisperx([{"word": "hello", "start": 0.1, "end": 0.4}])
        with patch.object(wb_mod, "_whisperx", mock_wx):
            words = audio_mod.transcribe_words(str(wav), str(out))
        mock_wx.load_model.assert_called_once()
        assert words == [{"word": "hello", "start": 0.1, "end": 0.4}]
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["words"] == words
        assert "total_duration_s" in data

    def test_writes_valid_json_structure(self, tmp_path: Path) -> None:
        wav = _make_wav(tmp_path)
        out = tmp_path / "out.json"
        mock_wx = _mock_whisperx([
            {"word": "a", "start": 0.0, "end": 0.5},
            {"word": "b", "start": 0.6, "end": 1.0},
        ])
        with patch.object(wb_mod, "_whisperx", mock_wx):
            audio_mod.transcribe_words(str(wav), str(out))
        data = json.loads(out.read_text())
        assert "words" in data
        assert "total_duration_s" in data
        assert data["total_duration_s"] == 1.0
