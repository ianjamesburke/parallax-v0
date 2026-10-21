"""Rendered-media contracts for the zero-spend local video commands."""
import subprocess
from pathlib import Path

from parallax.local_video import assemble, overlay_display_text, text_overlay


def _run(*args):
    subprocess.run(args, check=True, capture_output=True)


def test_assemble_delays_audio_and_holds_exact_timeline(tmp_path):
    clip, voice, out = (tmp_path / x for x in ("clip.mp4", "voice.wav", "out.mp4"))
    _run("ffmpeg", "-y", "-f", "lavfi", "-i", "color=black:s=160x90:d=2", "-an", str(clip))
    _run("ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=900:duration=0.2", str(voice))
    assemble([{"path": str(clip), "duration": 1}, {"path": str(clip), "duration": 1}], str(out), voice=str(voice), voice_offset=1)
    duration=float(subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(out)]))
    detect=subprocess.run(["ffmpeg","-hide_banner","-i",str(out),"-af","silencedetect=noise=-40dB:d=0.05","-f","null","-"],capture_output=True,text=True).stderr
    assert abs(duration-2) <= 1/30
    assert "silence_end: 1.0" in detect


def test_overlay_timeline_is_real_frames_and_countup_is_not_static(tmp_path):
    source, out = tmp_path / "source.png", tmp_path / "out.mp4"
    _run("ffmpeg", "-y", "-f", "lavfi", "-i", "color=navy:s=160x90", "-frames:v", "1", str(source))
    text_overlay(str(source), "+20%", 1, str(out), preset="pop", resolution_="320x568", y=.5)
    info=subprocess.check_output(["ffprobe","-v","error","-show_entries","stream=width,height,r_frame_rate:format=duration","-of","default=nw=1",str(out)],text=True)
    assert "width=320" in info and "height=568" in info and "r_frame_rate=30/1" in info and "duration=1.000000" in info
    assert overlay_display_text("+20%", "count-up", .05, 1) != overlay_display_text("+20%", "count-up", .8, 1)
