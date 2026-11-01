"""karaoke + soft caption presets, lowercase option, style errors, font fallback."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from parallax import captions
from parallax.captions import CAPTION_STYLES, _FONTS_DIR, _burn_captions_drawtext, _style_drawtext_filter, resolve_caption_style
from parallax.captions.chunker import _fixed_chunk_words
from parallax.captions.layout import _font, layout_caption
from parallax.captions.styles import resolve_font_path
from parallax.cli._cut import _parse_caption_style
from parallax.cut import CutError, _resolve_cut_caption_style
from parallax.ffmpeg_utils import _ffmpeg_has_drawtext

_requires_drawtext = pytest.mark.skipif(not _ffmpeg_has_drawtext(), reason="ffmpeg lacks drawtext")

WORDS = [
    {"word": "lip", "start": 0.0, "end": 0.3},
    {"word": "filler", "start": 0.5, "end": 0.9},   # 0.2s gap before it
    {"word": "really", "start": 1.0, "end": 1.4},
]


def _video(path: Path, d: float = 1.6, w: int = 720, h: int = 1280) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=gray:s={w}x{h}:d={d}:r=24", "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)


def _captured_graph(style_name, tmp_path, words=WORDS, wpc=3):
    """Run the real layout + drawtext builder with ffmpeg mocked; return the filtergraph."""
    v = tmp_path / "in.mp4"
    v.write_bytes(b"x")
    style = resolve_caption_style(style_name)
    chunks = _fixed_chunk_words(words, wpc)
    keyed = []
    for c in chunks:
        keyed.append({**c, "fontsize": 56, **layout_caption(c["text"], 720, 1280, 56, style, .65)})
    with patch("parallax.captions.drawtext.run_ffmpeg") as run, \
         patch("parallax.ffmpeg_utils.probe_resolution", return_value=(720, 1280)), \
         patch("parallax.ffmpeg_utils._probe_fps", return_value=24.0), \
         patch("parallax.captions.drawtext._probe_duration", return_value=1.6):
        run.return_value = type("R", (), {"returncode": 0, "stderr": ""})()
        _burn_captions_drawtext(str(v), keyed, tmp_path / "o.mp4", 56, style)
    argv = run.call_args.args[0]
    flag = "-filter_complex" if "-filter_complex" in argv else "-vf"
    return argv[argv.index(flag) + 1]


# ─── presets ─────────────────────────────────────────────────────────────

def test_new_presets_resolve():
    k, s = resolve_caption_style("karaoke"), resolve_caption_style("soft")
    assert k["fontfile"] == "Montserrat-ExtraBold.ttf" and k["uppercase"] and k["highlight_color"] == "0xE8457A"
    assert s["fontfile"] == "Montserrat-SemiBold.ttf" and s["lowercase"] and s["max_lines"] == 2
    assert s["max_line_width"] == pytest.approx(0.62) and not s["box"] and not s["borderw"]
    assert s["shadow_blur"] > 0
    for st in (k, s):
        assert (_FONTS_DIR / st["fontfile"]).is_file()


def test_karaoke_per_word_drawtext_with_highlight_windows(tmp_path):
    g = _captured_graph("karaoke", tmp_path)
    parts = g.split(",drawtext=")
    base = [p for p in parts if "box=1" not in p]
    hl = [p for p in parts if "box=1" in p]
    assert len(base) == 3 and len(hl) == 3
    assert all("boxcolor=0xE8457A" in p and "boxborderw=10" in p for p in hl)
    assert [re.search(r"text='(\w+)'", p).group(1) for p in hl] == ["LIP", "FILLER", "REALLY"]
    # base words keep the black stroke; highlight text has none
    assert all("borderw=4" in p for p in base) and not any(":borderw=" in p or "bordercolor" in p for p in hl)
    # windows: gap 0.3-0.5 is held on "lip"; windows tile the chunk without overlap
    wins = [re.search(r"enable='gte\(t,([\d.]+)\)\*lt\(t,([\d.]+)\)'", p).groups() for p in hl]
    assert wins == [("0.0", "0.5"), ("0.5", "1.0"), ("1.0", "1.4")]
    # x positions increase left to right and the group is centred
    xs = [int(re.search(r":x=(\d+):", p).group(1)) for p in base]
    assert xs == sorted(xs)


def test_karaoke_highlight_color_configurable(tmp_path):
    style = {"base": "karaoke", "highlight_color": "0x2255FF", "highlight_padding": 6}
    v = tmp_path / "in.mp4"; v.write_bytes(b"x")
    st = resolve_caption_style(style)
    c = {**_fixed_chunk_words(WORDS, 3)[0], "fontsize": 56, **layout_caption("lip filler really", 720, 1280, 56, st)}
    with patch("parallax.captions.drawtext.run_ffmpeg") as run:
        run.return_value = type("R", (), {"returncode": 0, "stderr": ""})()
        _burn_captions_drawtext(str(v), [c], tmp_path / "o.mp4", 56, st)
    vf = run.call_args.args[0][run.call_args.args[0].index("-vf") + 1]
    assert "boxcolor=0x2255FF" in vf and "boxborderw=6" in vf


def test_karaoke_burn_shifts_word_windows(tmp_path):
    words = [{"word": "a", "start": 1.0, "end": 1.2}, {"word": "bb", "start": 1.3, "end": 1.6}]
    v = tmp_path / "in.mp4"; _video(v, 2.0) if _ffmpeg_has_drawtext() else v.write_bytes(b"x")
    if not _ffmpeg_has_drawtext():
        pytest.skip("needs ffmpeg")
    with patch("parallax.captions.drawtext.run_ffmpeg") as run:
        run.return_value = type("R", (), {"returncode": 0, "stderr": ""})()
        captions.burn_captions(str(v), json.dumps(words), str(tmp_path / "o.mp4"),
                               caption_style="karaoke", shift_s=-0.5, words_per_chunk=3)
    vf = run.call_args.args[0][run.call_args.args[0].index("-vf") + 1]
    assert "gte(t,0.8)*lt(t,1.1)" in vf  # second word 1.3 -> 0.8


def test_soft_lowercase_and_blurred_shadow_layer(tmp_path):
    g = _captured_graph("soft", tmp_path, words=[{"word": "Here’s", "start": 0, "end": .3},
                                                 {"word": "WHAT", "start": .3, "end": .6}])
    assert "gblur=sigma=7" in g and "overlay=0:0" in g and "color=c=black@0.0:s=720x1280" in g
    assert "text='here’s what'" in g
    assert "HERE" not in g and "WHAT" not in g
    shadow, crisp = g.split("[sh];")
    assert "fontcolor=black@0.9" in shadow and "fontcolor=white" not in shadow
    assert "fontcolor=white" in crisp and "shadowx" not in crisp and "shadowy" not in crisp


def test_hard_shadow_presets_unchanged():
    f = _style_drawtext_filter(resolve_caption_style("anton"), "hi", 0, 1, 50)
    assert "shadowcolor=black@0.7" in f and "gblur" not in f


def test_lowercase_option_and_precedence():
    f = _style_drawtext_filter({**resolve_caption_style("clean"), "lowercase": True}, "Hello World", 0, 1, 50)
    assert "text='hello world'" in f
    both = {**resolve_caption_style("bangers"), "lowercase": True}   # uppercase also true
    assert "text='hello'" in _style_drawtext_filter(both, "HeLLo", 0, 1, 50)


# ─── errors / passthrough ───────────────────────────────────────────────

def test_unknown_base_raises_listing_presets():
    with pytest.raises(ValueError, match="karaoke"):
        resolve_caption_style({"base": "nope"})


def test_cli_unknown_style_lists_presets_and_json_dict_parses():
    with pytest.raises(ValueError) as exc:
        _parse_caption_style("neon")
    assert "neon" in str(exc.value) and "karaoke" in str(exc.value) and "soft" in str(exc.value)
    assert _parse_caption_style("karaoke") == "karaoke"
    assert _parse_caption_style('{"base": "soft", "fontcolor": "yellow"}') == {"base": "soft", "fontcolor": "yellow"}


def test_cut_dict_style_passes_through_and_unknown_raises():
    d = {"base": "karaoke", "highlight_color": "0x112233"}
    assert _resolve_cut_caption_style(None, {"caption_style": d}) is d
    assert _resolve_cut_caption_style(d, {}) is d
    assert _resolve_cut_caption_style(None, {}) == "bangers"
    with pytest.raises(CutError) as exc:
        _resolve_cut_caption_style("neon", {})
    assert "karaoke" in str(exc.value)


# ─── font fallback ──────────────────────────────────────────────────────

def test_impact_falls_back_to_bundled_font_with_warning(caplog):
    style = resolve_caption_style("impact")
    if Path(style["system_font"]).is_file():
        pytest.skip("real Impact installed")
    import parallax.captions.styles as styles
    styles._warned_fonts.clear()
    with caplog.at_level("WARNING"):
        assert resolve_font_path(style) == _FONTS_DIR / "Anton-Regular.ttf"
    assert "falling back" in caplog.text
    f = _style_drawtext_filter(style, "hi", 0, 1, 50)
    assert f"fontfile='{_FONTS_DIR / 'Anton-Regular.ttf'}'" in f
    assert _font(style, 40).path.endswith("Anton-Regular.ttf")


def test_missing_custom_font_falls_back_to_montserrat_black(tmp_path):
    style = {**resolve_caption_style("clean"), "fontfile": "Nope.ttf"}
    assert resolve_font_path(style) == _FONTS_DIR / "Montserrat-Black.ttf"


# ─── real ffmpeg ────────────────────────────────────────────────────────

@_requires_drawtext
@pytest.mark.parametrize("name", ["karaoke", "soft"])
def test_real_burn_new_presets(tmp_path, name):
    v = tmp_path / "in.mp4"; _video(v)
    out = captions.burn_captions(str(v), json.dumps(WORDS), str(tmp_path / f"{name}.mp4"),
                                 caption_style=name, words_per_chunk=3)
    assert Path(out).stat().st_size > 0
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", out], capture_output=True, text=True).stdout
    assert float(dur) == pytest.approx(1.6, abs=0.1)
