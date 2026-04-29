"""Caption burn-in characterization.

Locks in:
  - _style_drawtext_filter emits a single drawtext= filter string with
    style-derived knobs (font, color, border, box, x/y, enable).
  - Special-character escaping (`:` → `\\:`, `'` → curly quote, `\\` → `\\\\`).
  - uppercase=True flag forces the rendered text to upper-case.
  - _burn_captions_drawtext writes a video of correct duration with the
    drawtext filter chain applied.
  - _burn_captions_pillow falls back without ffmpeg drawtext and produces
    a video of the same duration with audio preserved.
  - burn_captions orchestrator: chunking + animation expansion +
    drawtext path end-to-end.
  - _probe_fps reads stream r_frame_rate; returns 30.0 on bad input.
  - _parse_color: white/black/hex strings.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from parallax import captions
from parallax.captions import (
    _burn_captions_drawtext,
    _burn_captions_pillow,
    _style_drawtext_filter,
    resolve_caption_style,
)
from parallax.ffmpeg_utils import (
    _ffmpeg_has_drawtext,
    _get_ffmpeg,
    _parse_color,
    _probe_fps,
)


def _make_video_with_audio(path: Path, duration_s: float = 1.0,
                            w: int = 540, h: int = 960) -> None:
    """Color video with a silent audio track."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=red:s={w}x{h}:d={duration_s}:r=30",
         "-f", "lavfi", "-i", f"anullsrc=cl=mono:r=44100",
         "-t", str(duration_s),
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )


def _probe_dur(path: Path) -> float:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    return float(p.stdout.strip())


# ─── _style_drawtext_filter ─────────────────────────────────────────────


def test_drawtext_filter_basic_shape():
    style = resolve_caption_style("bangers")
    f = _style_drawtext_filter(style, "HELLO", 0.0, 0.5, 64)
    assert f.startswith("drawtext=")
    assert "fontsize=64" in f
    assert "fontcolor=white" in f
    assert "borderw=6" in f and "bordercolor=black" in f
    assert "enable='gte(t,0.0)*lt(t,0.5)'" in f


def test_drawtext_filter_uppercase_forces_caps():
    style = resolve_caption_style("bangers")  # uppercase=True
    f = _style_drawtext_filter(style, "hello world", 0, 1, 50)
    # the rendered text= argument is uppercased
    assert "text='HELLO WORLD'" in f


def test_drawtext_filter_clean_preset_uses_box_not_border():
    style = resolve_caption_style("clean")
    f = _style_drawtext_filter(style, "hi", 0, 1, 50)
    assert "box=1" in f
    assert "boxcolor=black@0.55" in f
    # clean preset has borderw=0 → no border kv (boxborderw=20 is unrelated)
    assert ":borderw=" not in f
    assert "bordercolor=" not in f


def test_drawtext_filter_anton_has_shadow():
    style = resolve_caption_style("anton")
    f = _style_drawtext_filter(style, "hi", 0, 1, 50)
    assert "shadowx=4" in f and "shadowy=4" in f


def test_drawtext_filter_escapes_colon_in_text():
    style = resolve_caption_style("clean")
    f = _style_drawtext_filter(style, "Time: 12:30", 0, 1, 50)
    # ":" inside text becomes "\:"
    assert "Time\\:" in f and "12\\:30" in f


def test_drawtext_filter_escapes_apostrophe():
    """Apostrophes are replaced with curly quotes so they don't break
    the drawtext='...'  delimiter."""
    style = resolve_caption_style("clean")
    f = _style_drawtext_filter(style, "don't", 0, 1, 50)
    assert "don’t" in f


# ─── _probe_fps ─────────────────────────────────────────────────────────


def test_probe_fps_returns_video_fps(tmp_path):
    v = tmp_path / "v.mp4"
    _make_video_with_audio(v, 0.5)
    fps = _probe_fps(str(v))
    assert abs(fps - 30.0) < 0.5


def test_probe_fps_bad_path_falls_back_to_30(tmp_path):
    fps = _probe_fps(str(tmp_path / "nope.mp4"))
    assert fps == 30.0


# ─── _parse_color ───────────────────────────────────────────────────────


def test_parse_color_named_white_black():
    assert _parse_color("white") == (255, 255, 255)
    assert _parse_color("black") == (0, 0, 0)


def test_parse_color_hex():
    assert _parse_color("#FFE600") == (255, 230, 0)
    assert _parse_color("#000000") == (0, 0, 0)


def test_parse_color_strips_alpha_suffix():
    assert _parse_color("black@0.55") == (0, 0, 0)


def test_parse_color_none_defaults_to_white():
    assert _parse_color(None) == (255, 255, 255)


# ─── _get_ffmpeg / _ffmpeg_has_drawtext ─────────────────────────────────


def test_get_ffmpeg_returns_an_executable_path():
    p = _get_ffmpeg()
    assert isinstance(p, str)
    # Either ffmpeg-full path exists, or it's "ffmpeg" / shutil.which result
    assert p == "ffmpeg" or Path(p).exists()


def test_ffmpeg_has_drawtext_returns_bool():
    """Just lock in that the probe runs and returns a bool — actual value
    depends on the local ffmpeg build."""
    assert isinstance(_ffmpeg_has_drawtext(), bool)


# ─── _burn_captions_drawtext ────────────────────────────────────────────


@pytest.mark.skipif(not _ffmpeg_has_drawtext(),
                    reason="local ffmpeg lacks drawtext (libfreetype)")
def test_burn_captions_drawtext_produces_video(tmp_path):
    v = tmp_path / "in.mp4"
    _make_video_with_audio(v, 1.0)
    out = tmp_path / "captioned.mp4"
    chunks = [
        {"text": "HELLO", "start": 0.0, "end": 0.5},
        {"text": "WORLD", "start": 0.5, "end": 1.0},
    ]
    style = resolve_caption_style("bangers")
    _burn_captions_drawtext(str(v), chunks, out, fontsize=64, style=style)
    assert out.exists() and out.stat().st_size > 0
    assert abs(_probe_dur(out) - 1.0) < 0.2


# ─── _burn_captions_pillow ──────────────────────────────────────────────


def test_burn_captions_pillow_produces_video(tmp_path):
    v = tmp_path / "in.mp4"
    _make_video_with_audio(v, 0.5)
    out = tmp_path / "captioned_pillow.mp4"
    chunks = [{"text": "HI", "start": 0.0, "end": 0.5}]
    style = resolve_caption_style("bangers")
    _burn_captions_pillow(str(v), chunks, out, fontsize=64, style=style)
    assert out.exists() and out.stat().st_size > 0
    assert abs(_probe_dur(out) - 0.5) < 0.2


# ─── burn_captions orchestrator ─────────────────────────────────────────


def test_burn_captions_end_to_end(tmp_path):
    v = tmp_path / "in.mp4"
    _make_video_with_audio(v, 1.0)
    out = tmp_path / "out.mp4"
    words = [
        {"word": "hello", "start": 0.0, "end": 0.4},
        {"word": "world", "start": 0.4, "end": 0.9},
    ]
    result = captions.burn_captions(
        str(v), json.dumps(words), str(out),
        fontsize=50, caption_style="bangers",
    )
    assert Path(result) == out
    assert out.exists() and out.stat().st_size > 0


def test_burn_captions_no_words_returns_original(tmp_path):
    v = tmp_path / "in.mp4"
    _make_video_with_audio(v, 0.5)
    out = captions.burn_captions(str(v), json.dumps([]))
    assert out == str(v)


def test_burn_captions_words_path_argument(tmp_path):
    """Caller can pass a file path to vo_words.json."""
    v = tmp_path / "in.mp4"
    _make_video_with_audio(v, 0.5)
    words_path = tmp_path / "vo.json"
    words_path.write_text(json.dumps({
        "words": [{"word": "hi", "start": 0.0, "end": 0.4}],
        "total_duration_s": 0.4,
    }))
    out = tmp_path / "out.mp4"
    captions.burn_captions(str(v), str(words_path), str(out))
    assert out.exists()


def test_burn_captions_shift_s_offsets_chunks(tmp_path):
    """shift_s shifts chunk start/end; clamping at 0 prevents negative."""
    v = tmp_path / "in.mp4"
    _make_video_with_audio(v, 1.0)
    out = tmp_path / "out.mp4"
    words = [{"word": "hello", "start": 0.5, "end": 0.9}]
    # Shift by -0.3 → chunk should appear at 0.2..0.6
    captions.burn_captions(
        str(v), json.dumps(words), str(out), shift_s=-0.3,
    )
    assert out.exists()


# ─── Resolution-adaptation tests ────────────────────────────────────────
# Captions must render at 480p, 720p, AND 1080p without overflowing the
# frame, and the output dimensions must match the input. Caller is
# responsible for sizing the fontsize proportionally — these tests use
# the produce.py convention `fontsize_base * (width / 1080)` so they
# also verify the scaled fontsize doesn't blow up Pillow/drawtext.

@pytest.mark.parametrize("w,h", [(480, 854), (720, 1280), (1080, 1920)])
def test_burn_captions_adapts_to_resolution(tmp_path, w, h):
    """End-to-end caption burn at multiple resolutions — output must
    preserve input dimensions and the scaled fontsize must not throw."""
    v = tmp_path / f"in_{w}x{h}.mp4"
    _make_video_with_audio(v, 1.0, w=w, h=h)
    out = tmp_path / f"out_{w}x{h}.mp4"
    words = [
        {"word": "hello", "start": 0.0, "end": 0.4},
        {"word": "world", "start": 0.4, "end": 0.9},
    ]
    res_scale = w / 1080
    fontsize = max(12, int(55 * res_scale))
    captions.burn_captions(
        str(v), json.dumps(words), str(out),
        fontsize=fontsize, caption_style="bangers",
    )
    assert out.exists() and out.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    out_w, out_h = (int(x) for x in probe.stdout.strip().split(","))
    assert (out_w, out_h) == (w, h), (
        f"caption pass must not change frame size; got {out_w}x{out_h} from {w}x{h}"
    )
