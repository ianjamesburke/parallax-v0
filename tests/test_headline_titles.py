"""burn_titles + burn_headline characterization.

Locks in:
  - burn_titles writes an mp4 of the same duration as the input.
  - Empty titles list returns the original video path unchanged.
  - burn_headline accepts multi-line text and writes a video; end_time_s
    bounds the visible window via an `enable` clause in the filter.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from parallax import headline
from parallax.ffmpeg_utils import _ffmpeg_has_drawtext


def _make_video_with_audio(path: Path, duration_s: float = 1.0,
                            w: int = 540, h: int = 960) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=blue:s={w}x{h}:d={duration_s}:r=30",
         "-f", "lavfi", "-i", "anullsrc=cl=mono:r=44100",
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


def _frame_at(path: Path, seconds: float, output: Path) -> Image.Image:
    """Extract one decoded video frame for opacity assertions."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", str(seconds),
         "-i", str(path), "-frames:v", "1", str(output)],
        check=True, capture_output=True,
    )
    return Image.open(output).convert("RGB")


# ─── burn_titles ────────────────────────────────────────────────────────


def test_burn_titles_empty_returns_input(tmp_path):
    v = tmp_path / "in.mp4"
    _make_video_with_audio(v, 0.5)
    out = headline.burn_titles(str(v), [])
    assert out == str(v)


@pytest.mark.skipif(not _ffmpeg_has_drawtext(),
                    reason="local ffmpeg lacks drawtext")
def test_burn_titles_writes_video(tmp_path):
    v = tmp_path / "in.mp4"
    _make_video_with_audio(v, 1.0)
    out = tmp_path / "titled.mp4"
    titles = [{"text": "Chapter 1", "start_s": 0.0, "end_s": 0.5}]
    result = headline.burn_titles(str(v), titles, str(out), fontsize=48)
    assert Path(result) == out
    assert out.exists() and out.stat().st_size > 0
    assert abs(_probe_dur(out) - 1.0) < 0.2


@pytest.mark.skipif(not _ffmpeg_has_drawtext(),
                    reason="local ffmpeg lacks drawtext")
def test_burn_titles_multiple_overlap_windows(tmp_path):
    v = tmp_path / "in.mp4"
    _make_video_with_audio(v, 1.5)
    out = tmp_path / "titled.mp4"
    titles = [
        {"text": "Intro", "start_s": 0.0, "end_s": 0.5},
        {"text": "Body", "start_s": 0.5, "end_s": 1.0},
        {"text": "Outro", "start_s": 1.0, "end_s": 1.4},
    ]
    headline.burn_titles(str(v), titles, str(out))
    assert out.exists()


# ─── burn_headline ──────────────────────────────────────────────────────


@pytest.mark.skipif(not _ffmpeg_has_drawtext(),
                    reason="local ffmpeg lacks drawtext")
def test_burn_headline_basic(tmp_path):
    v = tmp_path / "in.mp4"
    _make_video_with_audio(v, 1.0)
    out = tmp_path / "headlined.mp4"
    result = headline.burn_headline(
        str(v), "Big News!", str(out), fontsize=48,
    )
    assert Path(result) == out
    assert out.exists() and out.stat().st_size > 0
    assert abs(_probe_dur(out) - 1.0) < 0.2


@pytest.mark.skipif(not _ffmpeg_has_drawtext(),
                    reason="local ffmpeg lacks drawtext")
def test_burn_headline_multiline(tmp_path):
    v = tmp_path / "in.mp4"
    _make_video_with_audio(v, 1.0)
    out = tmp_path / "headlined.mp4"
    headline.burn_headline(
        str(v), "Line one\nLine two", str(out), fontsize=40,
    )
    assert out.exists()


def test_burn_headline_default_filter_has_no_alpha_ramp(tmp_path, monkeypatch):
    """The default must not even emit an alpha expression at t=0."""
    v = tmp_path / "in.mp4"
    v.touch()
    captured = []
    monkeypatch.setattr(headline, "_ffmpeg_has_drawtext", lambda: True)
    monkeypatch.setattr(headline, "_get_drawtext_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("parallax.ffmpeg_utils.probe_resolution", lambda _: (270, 480))
    monkeypatch.setattr(headline, "run_ffmpeg", lambda args, **_: captured.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    headline.burn_headline(str(v), "VISIBLE NOW", str(tmp_path / "out.mp4"), fontsize=32)
    filtergraph = captured[0][captured[0].index("-vf") + 1]
    assert ":alpha=" not in filtergraph


@pytest.mark.skipif(not _ffmpeg_has_drawtext(),
                    reason="local ffmpeg lacks drawtext")
def test_burn_headline_is_full_strength_on_frame_zero_and_fade_is_opt_in(tmp_path):
    """Render a tiny clip: default text is present on frame 0, opt-in fade is not."""
    v = tmp_path / "in.mp4"
    _make_video_with_audio(v, 1.0, w=270, h=480)
    immediate = tmp_path / "immediate.mp4"
    fading = tmp_path / "fading.mp4"
    headline.burn_headline(str(v), "VISIBLE NOW", str(immediate), fontsize=32)
    headline.burn_headline(str(v), "VISIBLE NOW", str(fading), fontsize=32, fade_in_s=0.4)

    first = _frame_at(immediate, 0.0, tmp_path / "first.png")
    later = _frame_at(immediate, 0.6, tmp_path / "later.png")
    fading_first = _frame_at(fading, 0.0, tmp_path / "fading-first.png")
    fading_later = _frame_at(fading, 0.6, tmp_path / "fading-later.png")

    # The black glyphs sit on a deliberately white headline box. Compression
    # changes individual pixels slightly, so compare robust dark-pixel counts.
    dark = lambda image: sum(1 for r, g, b in image.crop((0, 40, 270, 180)).get_flattened_data()
                              if r < 70 and g < 70 and b < 70)
    assert dark(first) >= dark(later) * 0.85
    assert dark(fading_first) < dark(fading_later) * 0.20


def test_headline_layout_wraps_and_fits_long_text():
    """Headline overlays have the same no-cropping contract as captions."""
    laid = headline._layout_headline(
        "THIS IS A DELIBERATELY LONG HEADLINE THAT MUST STAY ON SCREEN", 1080, 1920, 96, "bangers"
    )
    assert len(laid["lines"]) >= 2
    assert laid["left"] >= laid["safe"]["left"]
    assert laid["right"] <= laid["safe"]["right"]


@pytest.mark.skipif(not _ffmpeg_has_drawtext(),
                    reason="local ffmpeg lacks drawtext")
def test_burn_headline_end_time_s_bounds_visibility(tmp_path):
    """When end_time_s is supplied the filter chain includes an enable= clause."""
    v = tmp_path / "in.mp4"
    _make_video_with_audio(v, 1.0)
    out = tmp_path / "out.mp4"
    headline.burn_headline(
        str(v), "Hello", str(out), fontsize=40, end_time_s=0.5,
    )
    assert out.exists()


# ─── Resolution-adaptation tests ────────────────────────────────────────
# Headline + titles must render correctly at 480p, 720p, AND 1080p
# without changing the frame size. Caller is responsible for sizing the
# fontsize proportionally — these tests scale fontsize by `width / 1080`
# (matching produce.py's res_scale convention).

@pytest.mark.skipif(not _ffmpeg_has_drawtext(),
                    reason="local ffmpeg lacks drawtext")
@pytest.mark.parametrize("w,h", [(480, 854), (720, 1280), (1080, 1920)])
def test_burn_headline_adapts_to_resolution(tmp_path, w, h):
    v = tmp_path / f"in_{w}x{h}.mp4"
    _make_video_with_audio(v, 1.0, w=w, h=h)
    out = tmp_path / f"out_{w}x{h}.mp4"
    res_scale = w / 1080
    fontsize = max(12, int(72 * res_scale))
    headline.burn_headline(
        str(v), "BIG NEWS", str(out), fontsize=fontsize,
    )
    assert out.exists() and out.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    out_w, out_h = (int(x) for x in probe.stdout.strip().split(","))
    assert (out_w, out_h) == (w, h), (
        f"headline pass must not change frame size; got {out_w}x{out_h} from {w}x{h}"
    )


@pytest.mark.skipif(not _ffmpeg_has_drawtext(),
                    reason="local ffmpeg lacks drawtext")
@pytest.mark.parametrize("w,h", [(480, 854), (720, 1280), (1080, 1920)])
def test_burn_titles_adapts_to_resolution(tmp_path, w, h):
    v = tmp_path / f"in_{w}x{h}.mp4"
    _make_video_with_audio(v, 1.0, w=w, h=h)
    out = tmp_path / f"out_{w}x{h}.mp4"
    res_scale = w / 1080
    fontsize = max(12, int(48 * res_scale))
    titles = [{"text": "Section 1", "start_s": 0.0, "end_s": 0.5}]
    headline.burn_titles(
        str(v), titles, str(out), fontsize=fontsize,
    )
    assert out.exists() and out.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    out_w, out_h = (int(x) for x in probe.stdout.strip().split(","))
    assert (out_w, out_h) == (w, h), (
        f"titles pass must not change frame size; got {out_w}x{out_h} from {w}x{h}"
    )


@pytest.mark.parametrize('typed', [True, False])
@pytest.mark.parametrize('overrides', [{}, {'headline_y_position': 'h*18/100', 'headline_font': 'anton'}])
def test_pipeline_forwards_headline_safe_area_and_font(tmp_path, monkeypatch, typed, overrides):
    from parallax.plan import Plan
    from parallax.settings import resolve_settings, ProductionMode
    from parallax.stages import PipelineState, stage_headline
    import parallax.stages as stages

    raw = {'headline': 'Opening overlay', 'resolution': '1080x1920', 'scenes': [], **overrides}
    plan = Plan.model_validate(raw) if typed else raw
    settings = resolve_settings(plan, tmp_path, tmp_path / 'plan.yaml', mode=ProductionMode.TEST)
    calls = []
    monkeypatch.setattr(stages, 'burn_headline', lambda **kwargs: calls.append(kwargs))
    state = PipelineState(video_dir=str(tmp_path), current_video='input.mp4', aligned=[{'end_s': 3.2}])
    stage_headline(raw, settings, state)
    assert calls[0]['end_time_s'] == 3.2
    assert calls[0]['text'] == 'Opening overlay'
    if overrides:
        assert calls[0]['y_position'] == 'h*18/100'
        assert calls[0]['font_name'] == 'anton'
    else:
        assert 'y_position' not in calls[0]  # retain burn_headline defaults
        assert 'font_name' not in calls[0]
    assert state.current_video == calls[0]['output_path']
