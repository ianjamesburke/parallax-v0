"""Contract tests for the small local `parallax video assemble` manifest."""
from __future__ import annotations

from pathlib import Path

from parallax import local_video


def test_assemble_clip_manifest_honors_trim_start_duration_and_speed(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output = tmp_path / "out.mp4"
    commands = []

    def fake_ffmpeg(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"clip")
        return type("Result", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(local_video, "run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(local_video, "probe_duration", lambda _: 8.0)
    local_video.assemble([{"path": str(source), "trim_start": 1.5, "duration": 2.5, "speed": 1.25}], str(output))
    clip_command = commands[0]
    assert clip_command[clip_command.index("-ss") + 1] == "1.5"
    assert clip_command[clip_command.index("-t") + 1] == "3.125"
    assert "setpts=PTS/1.25" in clip_command[clip_command.index("-vf") + 1]
