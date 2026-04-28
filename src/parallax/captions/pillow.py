"""Pillow-based caption backend (fallback when drawtext is unavailable).

Decodes raw frames from ffmpeg, draws caption text per-frame with PIL,
re-encodes via ffmpeg. Slower than the drawtext path but works on minimal
ffmpeg builds without libfreetype.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .styles import _FONTS_DIR


def _burn_captions_pillow(
    video_path: str,
    chunks: list[dict],
    out: Path,
    fontsize: int,
    style: dict,
) -> None:
    """Pillow-based caption burn: decode each frame, draw text, pipe to ffmpeg."""
    from PIL import Image, ImageDraw, ImageFont  # type: ignore[import]

    from ..ffmpeg_utils import _parse_color

    # Probe video for width/height/fps
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-of", "csv=p=0", video_path],
        capture_output=True, text=True,
    )
    parts = probe.stdout.strip().split(",")
    if len(parts) < 3:
        raise RuntimeError(f"ffprobe failed to read video info: {probe.stderr[:200]}")
    vid_w, vid_h = int(parts[0]), int(parts[1])
    fps_num, fps_den = (int(x) for x in parts[2].split("/"))
    fps = fps_num / fps_den

    # Load font from style
    fontfile = style.get("fontfile")
    font_path: str | None = str(_FONTS_DIR / fontfile) if fontfile else style.get("system_font")
    pil_font: Any = None
    if font_path and Path(font_path).exists():
        try:
            pil_font = ImageFont.truetype(font_path, fontsize)
        except OSError:
            pass
    if pil_font is None:
        for candidate in (
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ):
            if Path(candidate).exists():
                try:
                    pil_font = ImageFont.truetype(candidate, fontsize)
                    break
                except OSError:
                    continue
    if pil_font is None:
        pil_font = ImageFont.load_default()

    fill_rgb = _parse_color(style.get("fontcolor", "white"))
    stroke_rgb = _parse_color(style.get("bordercolor")) if style.get("bordercolor") else None
    borderw = style.get("borderw", 0)
    uppercase = style.get("uppercase", False)
    use_box = style.get("box", False)
    boxborderw = style.get("boxborderw", 10)

    def text_at(t: float) -> str:
        for chunk in chunks:
            if chunk["start"] <= t < chunk["end"]:
                txt = chunk["text"]
                return txt.upper() if uppercase else txt
        return ""

    decode = subprocess.Popen(
        ["ffmpeg", "-i", video_path, "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
         "-hide_banner", "-loglevel", "error"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        no_audio = Path(tmp_dir) / "captioned_no_audio.mp4"
        encode = subprocess.Popen(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "rawvideo", "-vcodec", "rawvideo",
             "-s", f"{vid_w}x{vid_h}", "-pix_fmt", "rgb24", "-r", str(fps),
             "-i", "pipe:0",
             "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
             str(no_audio)],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert encode.stdin is not None
        assert decode.stdout is not None

        frame_bytes = vid_w * vid_h * 3
        frame_idx = 0
        try:
            while True:
                raw = decode.stdout.read(frame_bytes)
                if len(raw) < frame_bytes:
                    break
                img = Image.frombytes("RGB", (vid_w, vid_h), raw)
                t = frame_idx / fps

                caption = text_at(t)
                if caption:
                    draw = ImageDraw.Draw(img)
                    bbox = draw.textbbox((0, 0), caption, font=pil_font)
                    tw = bbox[2] - bbox[0]
                    th = bbox[3] - bbox[1]
                    x = (vid_w - tw) // 2
                    y = int(vid_h * 0.65 - th)

                    if use_box:
                        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
                        overlay_draw = ImageDraw.Draw(overlay)
                        pad = boxborderw
                        overlay_draw.rectangle(
                            [x - pad, y - pad, x + tw + pad, y + th + pad],
                            fill=(0, 0, 0, 140),
                        )
                        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
                        draw = ImageDraw.Draw(img)

                    if stroke_rgb and borderw:
                        offsets = [
                            (dx, dy)
                            for dx in range(-borderw, borderw + 1)
                            for dy in range(-borderw, borderw + 1)
                            if dx != 0 or dy != 0
                        ]
                        for dx, dy in offsets:
                            draw.text((x + dx, y + dy), caption, font=pil_font, fill=stroke_rgb)
                    draw.text((x, y), caption, font=pil_font, fill=fill_rgb)

                encode.stdin.write(img.tobytes())
                frame_idx += 1
        except Exception as e:
            decode.kill()
            encode.kill()
            raise RuntimeError(f"Pillow caption burn failed at frame {frame_idx}: {e}") from e
        finally:
            encode.stdin.close()
            decode.stdout.close()

        decode.wait()
        encode.wait()
        enc_stderr = encode.stderr
        if encode.returncode != 0:
            err_msg = enc_stderr.read(300).decode(errors="replace") if enc_stderr else ""
            raise RuntimeError(f"Pillow caption encode failed: {err_msg}")

        result = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(no_audio), "-i", video_path,
             "-map", "0:v", "-map", "1:a",
             "-c:v", "copy", "-c:a", "copy", "-shortest",
             str(out)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Caption audio mux failed:\n{result.stderr[:300]}")
