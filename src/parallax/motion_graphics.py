"""Zero-spend branded motion-graphic renderers (PIL frames -> silent 30 fps MP4).

Layout contract (9:16, 1080x1920 reference): graphics live in the upper ~68% of the
frame inside an 8% horizontal safe margin; the band y in [0.70, 0.88] is left clear for
burned captions. All sizes scale with frame width.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Callable, Optional, Sequence

FONT_DIR = Path(__file__).parent / "fonts"
SAFE_X = 0.08
CAPTION_TOP = 0.70  # graphics should stay above this unless full-bleed

BRAND_DEFAULT = {"bg": "#14102A", "bg2": "#2A1E52", "accent": "#721CFF",
                 "sunrise": ["#721CFF", "#FF3963", "#FD8206", "#FFD600"],
                 "good": "#01EDB8", "text": "#FFFFFF", "muted": "#9A93B8"}


# ---------- helpers ----------
def _font(size: int, weight: str = "black"):
    from PIL import ImageFont
    name = {"black": "Montserrat-Black.ttf", "bold": "Montserrat-ExtraBold.ttf",
            "semi": "Montserrat-SemiBold.ttf"}[weight]
    p = FONT_DIR / name
    if not p.exists():
        p = FONT_DIR / "Montserrat-Black.ttf"
    return ImageFont.truetype(str(p), max(8, int(size)))


def _rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _grad_color(stops: Sequence[str], t: float):
    cs = [_rgb(s) for s in stops]
    t = min(1.0, max(0.0, t)) * (len(cs) - 1)
    i = min(int(t), len(cs) - 2)
    return _mix(cs[i], cs[i + 1], t - i)


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def seg(t: float, a: float, b: float) -> float:
    """Local progress of t within [a, b] (0..1)."""
    return clamp01((t - a) / max(1e-6, b - a))


def ease_out_cubic(x: float) -> float:
    return 1 - (1 - clamp01(x)) ** 3


def ease_in_out(x: float) -> float:
    x = clamp01(x)
    return 4 * x ** 3 if x < .5 else 1 - (-2 * x + 2) ** 3 / 2


def ease_out_back(x: float, s: float = 1.70158) -> float:
    x = clamp01(x)
    return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2


def _background(w, h, bg, bg2, t, glow=None):
    """Vertical gradient + soft drifting radial glow (cheap: drawn at 1/8 res)."""
    from PIL import Image, ImageDraw, ImageFilter
    sw, sh = max(8, w // 8), max(8, h // 8)
    im = Image.new("RGB", (sw, sh))
    d = ImageDraw.Draw(im)
    a, b = _rgb(bg), _rgb(bg2)
    for y in range(sh):
        d.line([(0, y), (sw, y)], fill=_mix(a, b, (y / sh) ** 1.4))
    if glow:
        g = Image.new("RGB", (sw, sh), (0, 0, 0))
        gd = ImageDraw.Draw(g)
        cx = sw * (0.5 + 0.08 * math.sin(t * 2 * math.pi * 0.5))
        cy = sh * 0.42
        r = sw * 0.55
        gd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_rgb(glow))
        g = g.filter(ImageFilter.GaussianBlur(sw * 0.18))
        from PIL import ImageChops
        im = ImageChops.add(im, Image.eval(g, lambda v: int(v * 0.35)))
    return im.resize((w, h), Image.BILINEAR)


def _glow_layer(size, draw_fn, radius):
    from PIL import Image, ImageFilter
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw_fn(layer)
    return layer.filter(ImageFilter.GaussianBlur(radius))


def _text_center(d, xy, text, font, fill, anchor="mm", alpha=255):
    if alpha >= 255:
        d.text(xy, text, font=font, fill=fill, anchor=anchor)
    else:
        rgb = _rgb(fill) if isinstance(fill, str) else tuple(fill[:3])
        d.text(xy, text, font=font, fill=(*rgb, alpha), anchor=anchor)


def _fit_font(text, max_w, size, weight="black"):
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    while size > 12:
        f = _font(size, weight)
        l, t, r, b = d.textbbox((0, 0), text, font=f)
        if r - l <= max_w:
            return f
        size = int(size * 0.92)
    return _font(size, weight)


def _pill(im, cx, cy, text, font, fill, scale=1.0, text_fill="#FFFFFF", alpha=1.0, glow=True):
    """Rounded pill label centered at (cx, cy), scaled about its center."""
    from PIL import Image, ImageDraw
    if scale <= 0.01 or alpha <= 0.01:
        return
    d0 = ImageDraw.Draw(im)
    l, t, r, b = d0.textbbox((0, 0), text, font=font)
    tw, th = r - l, b - t
    padx, pady = int(th * 0.9), int(th * 0.6)
    W, H = tw + 2 * padx, th + 2 * pady
    tile = Image.new("RGBA", (W + 40, H + 40), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    td.rounded_rectangle((20, 20, 20 + W, 20 + H), radius=H // 2, fill=(*_rgb(fill), 255))
    td.text((20 + W / 2, 20 + H / 2), text, font=font, fill=text_fill, anchor="mm")
    if scale != 1.0:
        tile = tile.resize((max(1, int(tile.width * scale)), max(1, int(tile.height * scale))), Image.LANCZOS)
    if alpha < 1:
        a = tile.getchannel("A").point(lambda v: int(v * alpha))
        tile.putalpha(a)
    x, y = int(cx - tile.width / 2), int(cy - tile.height / 2)
    if glow:
        from PIL import ImageFilter
        gl = Image.new("RGBA", tile.size, (*_rgb(fill), 0))
        gl.putalpha(tile.getchannel("A").point(lambda v: int(v * 0.6)))
        gl = gl.filter(ImageFilter.GaussianBlur(18 * scale))
        im.paste(gl, (x, y), gl)
    im.paste(tile, (x, y), tile)
    return (x, y, x + tile.width, y + tile.height)


def _title(im, t, text, w, h, y=0.13, size=None, start=0.0):
    """Kinetic title: slides up + fades in."""
    if not text:
        return
    from PIL import ImageDraw
    p = ease_out_cubic(seg(t, start, start + 0.18))
    if p <= 0:
        return
    f = _fit_font(text, int(w * (1 - 2 * SAFE_X)), size or int(w * 0.075))
    layer = im.convert("RGBA")
    d = ImageDraw.Draw(layer)
    d.text((w / 2, h * y + (1 - p) * w * 0.04), text, font=f, fill=(255, 255, 255, int(255 * p)), anchor="mm")
    im.paste(layer.convert("RGB"))


def render(duration: float, out: str, draw_frame: Callable, resolution_: str = "1080x1920") -> str:
    """Render frames(t in 0..1 over the clip, seconds) to an exact-duration silent MP4."""
    import subprocess
    import tempfile
    from .ffmpeg_utils import parse_resolution, run_ffmpeg
    w, h = parse_resolution(resolution_)
    count = max(1, round(float(duration) * 30))
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="parallax-mg-") as td:
        proc = subprocess.Popen(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{w}x{h}", "-framerate", "30", "-i", "-", "-frames:v", str(count), "-an",
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", str(target)],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdin is not None
        for n in range(count):
            t = n / max(1, count - 1)
            frame = draw_frame(t, n / 30.0, w, h).convert("RGB")
            if frame.size != (w, h):
                frame = frame.resize((w, h))
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode() if proc.stderr else ""
        if proc.wait():
            raise RuntimeError(err[:500])
    _ = run_ffmpeg  # keep import parity with local_video
    return str(target)


def _colors(colors: Optional[str], accent: Optional[str] = None):
    pal = dict(BRAND_DEFAULT)
    if colors:
        cs = [c.strip() for c in colors.split(",") if c.strip()]
        if cs:
            pal["accent"] = cs[0]
            if len(cs) >= 2:
                pal["sunrise"] = cs
    if accent:
        pal["accent"] = accent
    return pal


# ---------- primitives ----------
def clock_window(duration, out, *, label="YOUR MASTER CLOCK", sub=None, colors=None, bg="#14102A",
                 window=(22.0, 1.0), resolution_="1080x1920"):
    """24-hour clock face; a glowing 'window' arc lights up and the hand sweeps toward/past it."""
    from PIL import Image, ImageDraw
    pal = _colors(colors)
    ws, we = window

    def hour_to_deg(hr):  # 0h at top, clockwise; PIL angles: 0 = 3 o'clock
        return hr / 24 * 360 - 90

    def frame(t, sec, w, h):
        im = _background(w, h, bg, pal["bg2"], t, glow=pal["accent"])
        cx, cy, R = w / 2, h * 0.43, w * 0.33
        rgba = im.convert("RGBA")
        d = ImageDraw.Draw(rgba)
        appear = ease_out_back(seg(t, 0.0, 0.22))
        r = R * (0.6 + 0.4 * appear)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 255, 255, 230), width=max(4, int(w * 0.008)))
        for k in range(24):
            a = math.radians(hour_to_deg(k))
            l1 = r * (0.86 if k % 6 else 0.80)
            d.line((cx + math.cos(a) * l1, cy + math.sin(a) * l1, cx + math.cos(a) * r * 0.94, cy + math.sin(a) * r * 0.94),
                   fill=(255, 255, 255, 200 if k % 6 == 0 else 110), width=max(2, int(w * (0.006 if k % 6 == 0 else 0.003))))
        fnum = _font(w * 0.03, "bold")
        for k, txt in ((0, "12AM"), (6, "6AM"), (12, "12PM"), (18, "6PM")):
            a = math.radians(hour_to_deg(k))
            d.text((cx + math.cos(a) * r * 0.66, cy + math.sin(a) * r * 0.66), txt, font=fnum, fill=(200, 196, 224, 220), anchor="mm")
        # window arc grows in
        wp = ease_out_cubic(seg(t, 0.15, 0.45))
        a0 = hour_to_deg(ws)
        span = ((we - ws) % 24) / 24 * 360
        if wp > 0:
            rr = r * 1.09
            def draw_arc(layer, width_mult=1.0):
                ld = ImageDraw.Draw(layer)
                steps = 40
                for i in range(int(steps * wp)):
                    c = _grad_color(pal["sunrise"], i / steps)
                    ld.arc((cx - rr, cy - rr, cx + rr, cy + rr), a0 + span * i / steps, a0 + span * (i + 1.4) / steps,
                           fill=(*c, 255), width=int(w * 0.035 * width_mult))
            pulse = 0.75 + 0.25 * math.sin(sec * 6)
            gl = _glow_layer(rgba.size, lambda L: draw_arc(L, 1.6), w * 0.02)
            gl.putalpha(gl.getchannel("A").point(lambda v: int(v * pulse)))
            rgba = Image.alpha_composite(rgba, gl)
            d = ImageDraw.Draw(rgba)
            draw_arc(rgba)
            d = ImageDraw.Draw(rgba)
        # hand: sweeps from 18h toward the window end and a bit past
        hp = ease_in_out(seg(t, 0.1, 0.95))
        hr = 18 + (((we - 18) % 24) + 1.5) * hp
        a = math.radians(hour_to_deg(hr))
        d.line((cx, cy, cx + math.cos(a) * r * 0.78, cy + math.sin(a) * r * 0.78), fill=(255, 255, 255, 255), width=max(6, int(w * 0.012)))
        d.ellipse((cx - w * 0.022, cy - w * 0.022, cx + w * 0.022, cy + w * 0.022), fill=(*_rgb(pal["accent"]), 255))
        im = rgba.convert("RGB")
        _title(im, t, label, w, h, y=0.13)
        if sub:
            sp = ease_out_back(seg(t, 0.45, 0.6))
            _pill(im, w / 2, cy + R * 1.32, sub, _font(w * 0.045, "black"), pal["accent"], scale=sp)
        return im
    return render(duration, out, frame, resolution_)


def meter(duration, out, *, value=63.0, label="SLEEP DEBT", unit="", display=None, max_value=100.0, colors=None, bg="#14102A",
          resolution_="1080x1920"):
    """Semicircle gauge; needle eases (with overshoot) to value% and a big number counts up."""
    from PIL import Image, ImageDraw
    pal = _colors(colors)
    stops = [pal.get("good", "#01EDB8"), "#FFD600", "#FD8206", "#FF3963"]

    def frame(t, sec, w, h):
        im = _background(w, h, bg, pal["bg2"], t, glow="#FF3963")
        rgba = im.convert("RGBA")
        d = ImageDraw.Draw(rgba)
        cx, cy, R = w / 2, h * 0.47, w * 0.36
        appear = ease_out_cubic(seg(t, 0.0, 0.2))
        steps = 60
        for i in range(int(steps * appear)):
            c = _grad_color(stops, i / steps)
            d.arc((cx - R, cy - R, cx + R, cy + R), 180 + 180 * i / steps, 180 + 180 * (i + 1.3) / steps, fill=(*c, 255), width=int(w * 0.05))
        p = ease_out_back(seg(t, 0.15, 0.7), 1.2)
        v = max(0.0, min(1.0, float(value) / float(max_value))) * p
        a = math.radians(180 + 180 * v)
        tip = (cx + math.cos(a) * R * 0.82, cy + math.sin(a) * R * 0.82)
        d.line((cx, cy, *tip), fill=(255, 255, 255, 255), width=max(8, int(w * 0.014)))
        d.ellipse((cx - w * 0.03, cy - w * 0.03, cx + w * 0.03, cy + w * 0.03), fill=(255, 255, 255, 255))
        im = rgba.convert("RGB")
        d = ImageDraw.Draw(im)
        cv = float(value) * ease_out_cubic(seg(t, 0.15, 0.7))
        shown = display if display is not None else (f"{cv:.1f}{unit}" if float(value) < 20 else f"{cv:.0f}{unit}")
        if display is not None and "{" in display:
            shown = display.format(v=float(value) * ease_out_cubic(seg(t, 0.15, 0.7)))
        d.text((cx, cy + w * 0.16), shown, font=_fit_font(shown, int(w * 0.8), int(w * 0.16)), fill="#FFFFFF", anchor="mm")
        _title(im, t, label, w, h, y=0.15)
        return im
    return render(duration, out, frame, resolution_)


def _energy(x: float) -> float:
    """Stylized circadian energy curve over a waking day (x 0..1): groggy start, AM peak, PM dip, evening peak, wind-down."""
    return (0.55 * math.exp(-((x - 0.22) / 0.13) ** 2) + 0.42 * math.exp(-((x - 0.68) / 0.12) ** 2)
            - 0.25 * math.exp(-((x - 0.47) / 0.08) ** 2) - 0.35 * math.exp(-((x - 0.0) / 0.07) ** 2)
            - 0.3 * max(0, x - 0.8) / 0.2 + 0.25)


def curve(duration, out, *, label="YOUR ENERGY TODAY", peak="PEAK", dip="DIP", axis=("7A", "12P", "3P", "9P"),
          colors=None, bg="#14102A", resolution_="1080x1920"):
    """Energy curve draws left-to-right with a sunrise gradient; PEAK/DIP pills pop as the pen passes."""
    from PIL import Image, ImageDraw
    pal = _colors(colors)
    N = 160

    def frame(t, sec, w, h):
        im = _background(w, h, bg, pal["bg2"], t, glow=pal["accent"])
        x0, x1 = w * SAFE_X + w * 0.02, w * (1 - SAFE_X) - w * 0.02
        ytop, ybot = h * 0.27, h * 0.58
        vals = [_energy(i / (N - 1)) for i in range(N)]
        lo, hi = min(vals), max(vals)
        pts = [(x0 + (x1 - x0) * i / (N - 1), ybot - (v - lo) / (hi - lo) * (ybot - ytop)) for i, v in enumerate(vals)]
        rgba = im.convert("RGBA")
        d = ImageDraw.Draw(rgba)
        # axis
        d.line((x0, ybot + w * 0.05, x1, ybot + w * 0.05), fill=(255, 255, 255, 70), width=3)
        fa = _font(w * 0.032, "bold")
        for i, lab in enumerate(axis):
            xx = x0 + (x1 - x0) * [0.02, 0.36, 0.55, 0.92][i % 4]
            d.text((xx, ybot + w * 0.1), lab, font=fa, fill=(200, 196, 224, 200), anchor="mm")
        p = ease_in_out(seg(t, 0.05, 0.8))
        k = max(2, int(N * p))
        def draw_line(layer, wmult):
            ld = ImageDraw.Draw(layer)
            for i in range(k - 1):
                c = _grad_color(pal["sunrise"], i / N)
                ld.line((*pts[i], *pts[i + 1]), fill=(*c, 255), width=int(w * 0.018 * wmult))
        rgba = Image.alpha_composite(rgba, _glow_layer(rgba.size, lambda L: draw_line(L, 2.2), w * 0.02))
        draw_line(rgba, 1.0)
        d = ImageDraw.Draw(rgba)
        px, py = pts[k - 1]
        d.ellipse((px - w * 0.02, py - w * 0.02, px + w * 0.02, py + w * 0.02), fill=(255, 255, 255, 255))
        im = rgba.convert("RGB")
        ipk = max(range(N // 2), key=lambda i: vals[i])
        idp = min(range(int(N * 0.35), int(N * 0.6)), key=lambda i: vals[i])
        fp = _font(w * 0.05, "black")
        for idx, txt, dy in ((ipk, peak, -w * 0.09), (idp, dip, w * 0.09)):
            s = ease_out_back(seg(t, (idx / N) * 0.75 + 0.08, (idx / N) * 0.75 + 0.2))
            if k > idx:
                _pill(im, pts[idx][0], pts[idx][1] + dy, txt, fp, pal["accent"], scale=s)
        _title(im, t, label, w, h, y=0.14)
        return im
    return render(duration, out, frame, resolution_)


def end_card(logo, duration, out, *, icon=None, headline="Become a morning person", cta="Try RISE free",
             sub="Download on the App Store", bg="#14102A", accent="#721CFF", colors=None, resolution_="1080x1920"):
    """CTA end card: icon pops, wordmark fades up, headline slides up, CTA pill pops then pulses."""
    from PIL import Image, ImageDraw
    pal = _colors(colors, accent)
    mark = Image.open(logo).convert("RGBA")
    ic = Image.open(icon).convert("RGBA") if icon else None

    def frame(t, sec, w, h):
        im = _background(w, h, bg, pal["bg2"], t, glow=pal["accent"])
        y = h * 0.20
        if ic is not None:
            s = ease_out_back(seg(sec, 0.0, 0.45))
            sz = int(w * 0.30 * max(0.01, s))
            if sz > 2:
                icr = ic.resize((sz, sz), Image.LANCZOS)
                mask = Image.new("L", (sz, sz), 0)
                ImageDraw.Draw(mask).rounded_rectangle((0, 0, sz, sz), radius=int(sz * 0.23), fill=255)
                a = icr.getchannel("A")
                from PIL import ImageChops
                icr.putalpha(ImageChops.multiply(a, mask))
                im.paste(icr, (int(w / 2 - sz / 2), int(y + w * 0.15 - sz / 2)), icr)
            y += w * 0.36
        mp = ease_out_cubic(seg(sec, 0.25, 0.7))
        mw = int(w * 0.46)
        mh = int(mark.height * mw / mark.width)
        m = mark.resize((mw, mh), Image.LANCZOS)
        if mp < 1:
            m.putalpha(m.getchannel("A").point(lambda v: int(v * mp)))
        im.paste(m, (int(w / 2 - mw / 2), int(y + (1 - mp) * w * 0.03)), m)
        y += mh + w * 0.10
        hp = ease_out_cubic(seg(sec, 0.45, 0.9))
        if hp > 0:
            layer = im.convert("RGBA")
            d = ImageDraw.Draw(layer)
            f = _fit_font(headline, int(w * (1 - 2 * SAFE_X)), int(w * 0.07))
            d.text((w / 2, y + (1 - hp) * w * 0.04), headline, font=f, fill=(255, 255, 255, int(255 * hp)), anchor="mm")
            im = layer.convert("RGB")
        y += w * 0.20
        cp = ease_out_back(seg(sec, 0.7, 1.1))
        pulse = 1 + 0.035 * max(0.0, math.sin((sec - 1.1) * 5)) if sec > 1.1 else 1.0
        _pill(im, w / 2, y, cta, _font(w * 0.065, "black"), pal["accent"], scale=cp * pulse)
        sp = ease_out_cubic(seg(sec, 1.0, 1.4))
        if sp > 0 and sub:
            layer = im.convert("RGBA")
            d = ImageDraw.Draw(layer)
            d.text((w / 2, y + w * 0.14), sub, font=_font(w * 0.038, "semi"), fill=(220, 216, 240, int(230 * sp)), anchor="mm")
            im = layer.convert("RGB")
        return im
    return render(duration, out, frame, resolution_)


def app_screen(screen, duration, out, *, bg="#272137", bg_image=None, callout=None, highlight=None,
               zoom_to_highlight=False, accent="#721CFF", title=None, full_bleed=False, callout_y=None,
               resolution_="1080x1920"):
    """Real app screenshot (pixels used as-is) with push-in, animated highlight and kinetic callout.

    full_bleed: the screenshot fills the frame (use for App Store screenshots that already include a
    device frame + headline). Otherwise it is placed in a drawn rounded phone frame on a branded bg.
    highlight: (x, y, w, h) fractions of the screenshot.
    """
    from PIL import Image, ImageDraw
    src = Image.open(screen).convert("RGB")
    pal = _colors(None, accent)

    def frame(t, sec, w, h):
        hz = ease_in_out(seg(t, 0.0, 1.0))
        if full_bleed:
            scale0 = max(w / src.width, h / src.height)
            z = 1.0 + (0.16 if zoom_to_highlight and highlight else 0.06) * hz
            sc = scale0 * z
            if highlight and zoom_to_highlight:
                fx = highlight[0] + highlight[2] / 2
                fy = highlight[1] + highlight[3] / 2
            else:
                fx, fy = 0.5, 0.5
            # keep focus point drifting toward center of frame
            cxs = src.width * sc * (0.5 + (fx - 0.5) * hz) - w / 2
            cys = src.height * sc * (0.5 + (fy - 0.5) * hz) - h / 2
            cxs = min(max(0, cxs), src.width * sc - w)
            cys = min(max(0, cys), src.height * sc - h)
            big = src.resize((int(src.width * sc), int(src.height * sc)), Image.BILINEAR)
            im = big.crop((int(cxs), int(cys), int(cxs) + w, int(cys) + h))
            to_frame = lambda X, Y: (X * src.width * sc - cxs, Y * src.height * sc - cys)
        else:
            im = Image.open(bg_image).convert("RGB").resize((w, h)) if bg_image else _background(w, h, bg, pal["bg2"], t, glow=accent)
            top = h * (0.2 if title else 0.1)
            pw = w * 0.66 * (1 + 0.04 * hz)
            ph = pw * src.height / src.width
            if ph > h * 0.66:
                ph = h * 0.66
                pw = ph * src.width / src.height
            x = (w - pw) / 2
            y = top + (h * 0.68 - top - ph) / 2 + w * 0.01 * math.sin(sec * 2)
            d = ImageDraw.Draw(im)
            bez = w * 0.022
            d.rounded_rectangle((x - bez, y - bez, x + pw + bez, y + ph + bez), radius=int(w * 0.07), fill="#0B0A12", outline="#3A3550", width=3)
            im.paste(src.resize((int(pw), int(ph)), Image.LANCZOS), (int(x), int(y)))
            to_frame = lambda X, Y: (x + X * pw, y + Y * ph)
        if highlight:
            hp = ease_out_back(seg(t, 0.18, 0.42))
            if hp > 0:
                X0, Y0 = to_frame(highlight[0], highlight[1])
                X1, Y1 = to_frame(highlight[0] + highlight[2], highlight[1] + highlight[3])
                cx, cy = (X0 + X1) / 2, (Y0 + Y1) / 2
                sx, sy = (X1 - X0) / 2 * (1.25 - 0.25 * hp), (Y1 - Y0) / 2 * (1.25 - 0.25 * hp)
                pulse = 0.7 + 0.3 * math.sin(sec * 7)
                rgba = im.convert("RGBA")
                box = (cx - sx, cy - sy, cx + sx, cy + sy)
                gl = _glow_layer(rgba.size, lambda L: ImageDraw.Draw(L).rounded_rectangle(box, radius=int(w * 0.04), outline=(*_rgb(accent), 255), width=int(w * 0.03)), w * 0.02)
                gl.putalpha(gl.getchannel("A").point(lambda v: int(v * pulse * min(1, hp))))
                rgba = Image.alpha_composite(rgba, gl)
                ImageDraw.Draw(rgba).rounded_rectangle(box, radius=int(w * 0.04), outline=(255, 255, 255, int(255 * min(1, hp))), width=max(4, int(w * 0.008)))
                im = rgba.convert("RGB")
                if callout:
                    cp = ease_out_back(seg(t, 0.32, 0.5))
                    f = _fit_font(callout, int(w * 0.78), int(w * 0.06))
                    ccy = callout_y * h if callout_y is not None else (cy - sy - w * 0.09 if cy - sy - w * 0.09 > h * 0.12 else cy + sy + w * 0.09)
                    ccx = min(max(cx, w * 0.42), w * 0.58)
                    _pill(im, ccx, ccy, callout, f, accent, scale=cp)
        elif callout:
            cp = ease_out_back(seg(t, 0.2, 0.4))
            _pill(im, w / 2, (callout_y or 0.62) * h, callout, _fit_font(callout, int(w * 0.78), int(w * 0.06)), accent, scale=cp)
        if title:
            _title(im, t, title, w, h, y=0.1)
        return im
    return render(duration, out, frame, resolution_)
