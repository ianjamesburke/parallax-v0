"""Tests for ffmpeg_utils probe helpers and pipe_rawvideo_frames."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# probe_resolution
# ---------------------------------------------------------------------------

class TestProbeResolution:
    def test_happy_path(self):
        from parallax.ffmpeg_utils import probe_resolution
        mock_result = MagicMock()
        mock_result.stdout = "1920,1080\n"
        with patch("parallax.ffmpeg_utils.run_ffmpeg", return_value=mock_result) as mock_run:
            result = probe_resolution("/tmp/video.mp4")
        assert result == (1920, 1080)
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "ffprobe"
        assert "/tmp/video.mp4" in cmd

    def test_called_process_error_returns_none(self):
        from parallax.ffmpeg_utils import probe_resolution
        with patch("parallax.ffmpeg_utils.run_ffmpeg",
                   side_effect=subprocess.CalledProcessError(1, "ffprobe")):
            result = probe_resolution("/tmp/bad.mp4")
        assert result is None

    def test_malformed_output_returns_none(self):
        from parallax.ffmpeg_utils import probe_resolution
        mock_result = MagicMock()
        mock_result.stdout = "not_valid\n"
        with patch("parallax.ffmpeg_utils.run_ffmpeg", return_value=mock_result):
            result = probe_resolution("/tmp/video.mp4")
        assert result is None


# ---------------------------------------------------------------------------
# probe_duration
# ---------------------------------------------------------------------------

class TestProbeDuration:
    def test_happy_path(self):
        from parallax.ffmpeg_utils import probe_duration
        mock_result = MagicMock()
        mock_result.stdout = "4.567\n"
        with patch("parallax.ffmpeg_utils.run_ffmpeg", return_value=mock_result):
            result = probe_duration("/tmp/video.mp4")
        assert result == pytest.approx(4.567)

    def test_empty_stdout_returns_none(self):
        from parallax.ffmpeg_utils import probe_duration
        mock_result = MagicMock()
        mock_result.stdout = "   \n"
        with patch("parallax.ffmpeg_utils.run_ffmpeg", return_value=mock_result):
            result = probe_duration("/tmp/video.mp4")
        assert result is None

    def test_failure_returns_none(self):
        from parallax.ffmpeg_utils import probe_duration
        with patch("parallax.ffmpeg_utils.run_ffmpeg",
                   side_effect=subprocess.CalledProcessError(1, "ffprobe")):
            result = probe_duration("/tmp/video.mp4")
        assert result is None


# ---------------------------------------------------------------------------
# probe_audio_duration
# ---------------------------------------------------------------------------

class TestProbeAudioDuration:
    def test_happy_path(self):
        from parallax.ffmpeg_utils import probe_audio_duration
        mock_result = MagicMock()
        mock_result.stdout = "3.2\n"
        with patch("parallax.ffmpeg_utils.run_ffmpeg", return_value=mock_result):
            result = probe_audio_duration("/tmp/video.mp4")
        assert result == pytest.approx(3.2)

    def test_na_output_returns_none(self):
        from parallax.ffmpeg_utils import probe_audio_duration
        mock_result = MagicMock()
        mock_result.stdout = "N/A"
        with patch("parallax.ffmpeg_utils.run_ffmpeg", return_value=mock_result):
            result = probe_audio_duration("/tmp/video.mp4")
        assert result is None

    def test_empty_output_returns_none(self):
        from parallax.ffmpeg_utils import probe_audio_duration
        mock_result = MagicMock()
        mock_result.stdout = ""
        with patch("parallax.ffmpeg_utils.run_ffmpeg", return_value=mock_result):
            result = probe_audio_duration("/tmp/video.mp4")
        assert result is None


# ---------------------------------------------------------------------------
# pipe_rawvideo_frames
# ---------------------------------------------------------------------------

class TestPipeRawvideoFrames:
    def _make_mock_proc(self):
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        return mock_proc

    def test_happy_path_writes_all_frames(self):
        from parallax.ffmpeg_utils import pipe_rawvideo_frames
        mock_proc = self._make_mock_proc()
        frames = [b"ab", b"cd", b"ef"]

        with patch("parallax.ffmpeg_utils.subprocess.Popen", return_value=mock_proc) as mock_popen:
            pipe_rawvideo_frames(
                "/tmp/out.mp4",
                width=2, height=1, fps=30, total_frames=3,
                frames=iter(frames),
                source_label="test",
            )

        # Popen called with correct ffmpeg command
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "2x1" in cmd
        assert "rgb24" in cmd
        assert "30" in cmd
        assert "3" in cmd
        assert "/tmp/out.mp4" in cmd

        # Each frame was written
        assert mock_proc.stdin.write.call_count == 3
        mock_proc.stdin.write.assert_any_call(b"ab")
        mock_proc.stdin.write.assert_any_call(b"cd")
        mock_proc.stdin.write.assert_any_call(b"ef")

        # stdin closed and process waited
        mock_proc.stdin.close.assert_called_once()
        mock_proc.wait.assert_called_once()

    def test_exception_during_write_kills_proc_and_raises(self):
        from parallax.ffmpeg_utils import pipe_rawvideo_frames
        mock_proc = self._make_mock_proc()
        mock_proc.stdin.write.side_effect = BrokenPipeError("pipe broken")

        with patch("parallax.ffmpeg_utils.subprocess.Popen", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="rawvideo pipe failed"):
                pipe_rawvideo_frames(
                    "/tmp/out.mp4",
                    width=2, height=1, fps=30, total_frames=1,
                    frames=iter([b"xy"]),
                    source_label="myimage.png",
                )

        mock_proc.kill.assert_called_once()
