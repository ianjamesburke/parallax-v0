"""Zero-spend motion-graphic renderers: exact duration/resolution and visible animation."""
import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from parallax import motion_graphics as mg
from parallax.assembly import _zoom_filter

RES = "270x480"


def _probe(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,nb_frames", "-of", "json", str(p)], capture_output=True, text=True).stdout
    s = json.loads(out)["streams"][0]
    return s["width"], s["height"], int(s["nb_frames"])


def _frame(p, t, tmp):
    f = tmp / f"f{t}.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(t), "-i", str(p), "-frames:v", "1", str(f)], check=True)
    return Image.open(f).convert("L")


def _diff(a, b):
    import numpy as np
    return float(abs(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)).mean())


@pytest.mark.parametrize("fn,kw", [
    (mg.clock_window, {"label": "YOUR CLOCK", "sub": "MISS IT"}),
    (mg.meter, {"value": 6.3, "max_value": 10, "unit": " HRS"}),
    (mg.curve, {"label": "ENERGY"}),
])
def test_diagrams_animate_exact_duration(tmp_path, fn, kw):
    out = tmp_path / "d.mp4"
    fn(1.0, str(out), resolution_=RES, **kw)
    w, h, n = _probe(out)
    assert (w, h, n) == (270, 480, 30)
    assert _diff(_frame(out, 0.05, tmp_path), _frame(out, 0.95, tmp_path)) > 1.0


def test_end_card_and_full_bleed_app_screen(tmp_path):
    logo = tmp_path / "logo.png"; Image.new("RGBA", (400, 100), (255, 255, 255, 255)).save(logo)
    icon = tmp_path / "icon.png"; Image.new("RGBA", (256, 256), (255, 120, 0, 255)).save(icon)
    shot = tmp_path / "shot.png"; Image.new("RGB", (1242, 2208), (30, 200, 120)).save(shot)
    e = tmp_path / "e.mp4"
    mg.end_card(str(logo), 1.5, str(e), icon=str(icon), resolution_=RES)
    assert _probe(e)[2] == 45
    assert _diff(_frame(e, 0.05, tmp_path), _frame(e, 1.4, tmp_path)) > 1.0
    a = tmp_path / "a.mp4"
    mg.app_screen(str(shot), 1.0, str(a), full_bleed=True, highlight=(0.5, 0.4, 0.3, 0.2), callout="DEBT", resolution_=RES)
    first = _frame(a, 0.02, tmp_path)
    # full-bleed: the screenshot covers the frame edge-to-edge (no drawn phone/background border)
    import numpy as np
    arr = np.asarray(Image.open(tmp_path / "f0.02.png").convert("RGB"))
    assert abs(int(arr[2, 2, 1]) - 200) < 25 and abs(int(arr[-3, -3, 1]) - 200) < 25
    assert _diff(first, _frame(a, 0.9, tmp_path)) > 0.5


def test_rejected_voiceover_dir_env(monkeypatch, tmp_path):
    """Rejected paid takes go to PARALLAX_REJECT_DIR (the CLI sets it to the --out folder)."""
    src = Path(__import__("parallax.voiceover", fromlist=["x"]).__file__).read_text()
    assert "PARALLAX_REJECT_DIR" in src
    cli = Path(__import__("parallax.cli._audio", fromlist=["x"]).__file__).read_text()
    assert 'setdefault("PARALLAX_REJECT_DIR", str(out_path.parent))' in cli


def test_ken_burns_filter_uses_target_width_not_a_hard_coded_1080_crop():
    """Non-1080 graphics must retain a centered, continuous crop path."""
    vf = _zoom_filter("in", 1.15, 2.0, "720", "1280")
    assert "1080" not in vf
    assert "(iw-720)/2" in vf
    assert "eval=frame" in vf


def test_pill_clamps_animated_long_text_inside_frame():
    from PIL import Image
    im = Image.new("RGB", (270, 480), "black")
    bounds = mg._pill(im, 5, 10, "CORTISOL · C21H30O5 " * 3,
                      mg._font(28), "#721CFF", scale=1.3)
    assert bounds is not None
    left, top, right, bottom = bounds
    assert 0 <= left < right <= im.width
    assert 0 <= top < bottom <= im.height
