"""Video pipeline tools: voiceover → Ken Burns → captions.

Each function is a self-contained tool callable by the agent.
No manifest dependency in signatures — the agent manages state in its context.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from .log import get_logger
from .shim import is_test_mode, output_dir

log = get_logger("tools_video")

VOICE_IDS: dict[str, str] = {
    "george": "JBFqnCBsd6RMkjVDRZzb",
    "rachel": "21m00Tcm4TlvDq8ikWAM",
    "domi": "AZnzlk1XvdvUeBnXmlld",
    "bella": "EXAVITQu4vr4xnSDxMaL",
    "daniel": "onwK4e9ZLuTAKqWW03F9",
    "arnold": "VR6AewLTigWG4xSOukaG",
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}


# ---------------------------------------------------------------------------
# scan_project_folder
# ---------------------------------------------------------------------------

def scan_project_folder(folder_path: str) -> str:
    """Scan a project folder for a script file and character reference image.

    Returns JSON: {script_path, character_image_path, script_text, folder}.
    Fails fast if neither is found.
    """
    folder = Path(folder_path).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"Not a directory: {folder}")

    # Find script: prefer script.txt / script.md; fall back to any lone .txt
    script_path: Path | None = None
    for name in ("script.txt", "script.md", "brief.txt", "brief.md"):
        candidate = folder / name
        if candidate.exists():
            script_path = candidate
            break
    if script_path is None:
        txts = [f for f in folder.iterdir() if f.suffix in (".txt", ".md") and f.is_file()]
        if len(txts) == 1:
            script_path = txts[0]
        elif len(txts) > 1:
            raise ValueError(
                f"Multiple text files found in {folder}; name one 'script.txt' to disambiguate: "
                + ", ".join(f.name for f in txts)
            )

    # Find character image: prefer character.jpg/png; fall back to any lone image
    char_path: Path | None = None
    for name in ("character.jpg", "character.jpeg", "character.png", "character.webp"):
        candidate = folder / name
        if candidate.exists():
            char_path = candidate
            break
    if char_path is None:
        imgs = [
            f for f in folder.iterdir()
            if f.suffix.lower() in IMAGE_EXTS and f.is_file()
        ]
        if len(imgs) == 1:
            char_path = imgs[0]
        elif len(imgs) > 1:
            # Pick alphabetically first (stable)
            char_path = sorted(imgs)[0]
            log.info("Multiple images found; using %s as character reference", char_path.name)

    result: dict[str, Any] = {
        "folder": str(folder),
        "script_path": str(script_path) if script_path else None,
        "script_text": script_path.read_text().strip() if script_path else None,
        "character_image_path": str(char_path) if char_path else None,
    }
    log.info("scan_project_folder: script=%s char=%s", script_path, char_path)
    return json.dumps(result)


# ---------------------------------------------------------------------------
# generate_voiceover
# ---------------------------------------------------------------------------

def generate_voiceover(
    text: str,
    voice: str = "george",
    speed: float = 1.1,
    out_dir: str | None = None,
) -> str:
    """Generate voiceover via ElevenLabs and apply atempo speed-up.

    Returns JSON: {audio_path, words_path, words, total_duration_s}.
    words = [{word, start, end}] at the sped-up rate.
    """
    key = os.environ.get("AI_VIDEO_ELEVENLABS_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError(
            "ElevenLabs key required: set AI_VIDEO_ELEVENLABS_KEY or ELEVENLABS_API_KEY"
        )

    voice_id = VOICE_IDS.get(voice.lower(), voice)
    dest = Path(out_dir or str(output_dir()))
    dest.mkdir(parents=True, exist_ok=True)

    if is_test_mode():
        return _mock_voiceover(text, dest)

    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=key)
    t0 = time.monotonic()
    log.info("voiceover: voice=%s speed=%.2f chars=%d", voice, speed, len(text))

    # Generate with timestamps
    response = client.text_to_speech.convert_with_timestamps(
        voice_id=voice_id,
        text=text,
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
    )

    import base64
    audio_b64 = getattr(response, "audio_base_64", None) or getattr(response, "audio_base64", None)
    if not audio_b64:
        raise RuntimeError("ElevenLabs response missing audio data")
    raw_mp3 = base64.b64decode(audio_b64)
    raw_path = dest / "voiceover_raw.mp3"
    raw_path.write_bytes(raw_mp3)

    # Extract raw audio duration via ffprobe
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(raw_path)],
        capture_output=True, text=True,
    )
    raw_duration = float(probe.stdout.strip()) if probe.stdout.strip() else 0.0

    # Derive word timestamps from character-level alignment
    alignment = getattr(response, "alignment", None)
    if alignment is None:
        raise RuntimeError("ElevenLabs response missing alignment data")
    chars = list(alignment.characters)
    starts = list(alignment.character_start_times_seconds)
    words_raw: list[dict] = []
    cur_word = ""
    word_start: float | None = None
    for i, ch in enumerate(chars):
        if ch.strip() == "":
            if cur_word and word_start is not None:
                words_raw.append({"word": cur_word, "start": word_start})
                cur_word = ""
                word_start = None
        else:
            if word_start is None:
                word_start = starts[i]
            cur_word += ch
    if cur_word and word_start is not None:
        words_raw.append({"word": cur_word, "start": word_start})

    # Add end times
    words_with_ends: list[dict] = []
    for i, w in enumerate(words_raw):
        end = words_raw[i + 1]["start"] if i + 1 < len(words_raw) else raw_duration
        words_with_ends.append({
            "word": w["word"],
            "start": round(w["start"], 3),
            "end": round(end, 3),
        })

    # Apply atempo speed-up
    audio_path = dest / "voiceover.mp3"
    words_sped, sped_duration = _apply_atempo(raw_path, words_with_ends, audio_path, speed)

    # Save word timestamps
    words_path = dest / "vo_words.json"
    words_path.write_text(json.dumps({
        "words": words_sped,
        "total_duration_s": sped_duration,
    }, indent=2))

    elapsed = int((time.monotonic() - t0) * 1000)
    log.info("voiceover: done duration=%.2fs elapsed=%dms", sped_duration, elapsed)

    return json.dumps({
        "audio_path": str(audio_path),
        "words_path": str(words_path),
        "words": words_sped,
        "total_duration_s": sped_duration,
    })


def _apply_atempo(raw_path: Path, words: list[dict], out_path: Path, speed: float) -> tuple[list[dict], float]:
    result = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
         "-i", str(raw_path), "-af", f"atempo={speed}",
         "-c:a", "libmp3lame", "-b:a", "128k", str(out_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out_path.exists():
        log.warning("atempo failed, using raw audio: %s", result.stderr[:200])
        raw_path.rename(out_path)
        return words, words[-1]["end"] if words else 0.0

    scale = 1.0 / speed
    sped = [
        {"word": w["word"], "start": round(w["start"] * scale, 3), "end": round(w["end"] * scale, 3)}
        for w in words
    ]
    duration = sped[-1]["end"] if sped else 0.0
    return sped, duration


def _mock_voiceover(text: str, dest: Path) -> str:
    words_text = text.split()
    t = 0.0
    words = []
    for w in words_text:
        dur = max(0.3, len(w) * 0.06)
        words.append({"word": w, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur + 0.05
    total = words[-1]["end"] if words else 0.0

    # Silence mp3
    audio_path = dest / "voiceover.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
         "-t", str(total), "-c:a", "libmp3lame", str(audio_path)],
        capture_output=True,
    )
    words_path = dest / "vo_words.json"
    words_path.write_text(json.dumps({"words": words, "total_duration_s": total}, indent=2))
    return json.dumps({
        "audio_path": str(audio_path),
        "words_path": str(words_path),
        "words": words,
        "total_duration_s": total,
    })


# ---------------------------------------------------------------------------
# align_scenes
# ---------------------------------------------------------------------------

def align_scenes(scenes_json: str, words_json: str) -> str:
    """Assign start_s/end_s/duration_s to each scene based on its vo_text word count.

    scenes_json: JSON list of {index, vo_text, ...}
    words_json: JSON string of [{word, start, end}] from generate_voiceover

    Returns updated scenes JSON list.
    """
    scenes: list[dict] = json.loads(scenes_json)
    payload = json.loads(words_json)
    words: list[dict] = payload if isinstance(payload, list) else payload.get("words", [])

    cursor = 0
    for scene in scenes:
        vo_text = scene.get("vo_text", "").strip()
        if not vo_text:
            continue
        count = len(vo_text.split())
        if cursor + count > len(words):
            log.warning("Scene %s needs %d words but only %d remain; extending to end",
                        scene.get("index", "?"), count, len(words) - cursor)
            count = len(words) - cursor
        if count == 0:
            continue
        first = words[cursor]
        last = words[cursor + count - 1]
        scene["start_s"] = round(first["start"], 3)
        scene["end_s"] = round(last["end"], 3)
        scene["duration_s"] = round(scene["end_s"] - scene["start_s"], 3)
        cursor += count

    # Close inter-scene gaps
    for i in range(len(scenes) - 1):
        curr = scenes[i]
        nxt = scenes[i + 1]
        if curr.get("end_s", 0) < nxt.get("start_s", 0):
            curr["end_s"] = nxt["start_s"]
            curr["duration_s"] = round(curr["end_s"] - curr["start_s"], 3)

    # Extend last scene to full audio
    total = payload.get("total_duration_s", words[-1]["end"] if words else 0) if isinstance(payload, dict) else (words[-1]["end"] if words else 0)
    if scenes and total > scenes[-1].get("end_s", 0):
        scenes[-1]["end_s"] = round(total, 3)
        scenes[-1]["duration_s"] = round(scenes[-1]["end_s"] - scenes[-1]["start_s"], 3)

    log.info("align_scenes: %d scenes aligned, total=%.2fs", len(scenes), total)
    return json.dumps(scenes)


# ---------------------------------------------------------------------------
# ken_burns_assemble
# ---------------------------------------------------------------------------

def ken_burns_assemble(
    scenes_json: str,
    audio_path: str,
    output_path: str | None = None,
    resolution: str = "1080x1920",
) -> str:
    """Assemble Ken Burns draft video from stills + aligned scene durations.

    scenes_json: JSON list of {still_path, duration_s, index?}
    audio_path: path to voiceover.mp3
    output_path: where to write the final .mp4 (default: output/ken_burns_draft.mp4)
    resolution: WxH e.g. "1080x1920" (vertical) or "1920x1080" (landscape)

    Returns the output video path.
    """
    scenes: list[dict] = json.loads(scenes_json)
    if not scenes:
        raise ValueError("No scenes provided")

    out = Path(output_path or str(output_dir() / "ken_burns_draft.mp4"))
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        clip_paths: list[str] = []
        for i, scene in enumerate(scenes):
            still = scene.get("still_path") or scene.get("image_path")
            dur = float(scene.get("duration_s", 5.0))
            if not still or not Path(still).exists():
                log.warning("Scene %d: still not found at %r, skipping", i, still)
                continue
            clip_out = str(Path(tmp_dir) / f"scene_{i:04d}.mp4")
            _make_kb_clip(still, dur, clip_out, resolution=resolution, scene_index=i)
            clip_paths.append(clip_out)

        if not clip_paths:
            raise RuntimeError("No scenes with valid stills to assemble")

        # Concat all clips
        list_file = Path(tmp_dir) / "clips.txt"
        list_file.write_text("\n".join(f"file '{p}'" for p in clip_paths))
        no_audio = Path(tmp_dir) / "no_audio.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-vf", f"scale={resolution.replace('x', ':')}",
             "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
             str(no_audio)],
            check=True,
        )

        # Mux with audio
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(no_audio),
             "-i", str(audio_path),
             "-c:v", "copy", "-c:a", "aac", "-shortest",
             str(out)],
            check=True,
        )

    log.info("ken_burns_assemble: wrote %s", out)
    return str(out)


def _make_kb_clip(
    image_path: str,
    duration: float,
    output_path: str,
    resolution: str = "1080x1920",
    scene_index: int = 0,
) -> None:
    """Pillow-based Ken Burns with float-precision crop (no zoompan jitter)."""
    from PIL import Image  # type: ignore[import]

    out_w, out_h = (int(x) for x in resolution.split("x"))
    fps = 30
    total_frames = max(1, round(duration * fps))

    # Motion presets: (start_zoom, end_zoom, pan_x, pan_y)
    motions = [
        (1.0, 1.15, 0.0, 0.0),
        (1.15, 1.0, 0.0, 0.0),
        (1.0, 1.12, 0.4, 0.0),
        (1.0, 1.12, -0.4, 0.0),
        (1.0, 1.12, 0.0, 0.4),
        (1.0, 1.12, 0.0, -0.4),
    ]
    start_zoom, end_zoom, pan_x, pan_y = motions[scene_index % len(motions)]

    src_w, src_h = round(out_w * 1.5), round(out_h * 1.5)
    img = Image.open(image_path).convert("RGB")
    scale = max(src_w / img.width, src_h / img.height)
    scaled = img.resize(
        (round(img.width * scale), round(img.height * scale)),
        Image.Resampling.LANCZOS,
    )
    x0 = (scaled.width - src_w) // 2
    y0 = (scaled.height - src_h) // 2
    img = scaled.crop((x0, y0, x0 + src_w, y0 + src_h))
    cx, cy = src_w / 2.0, src_h / 2.0

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{out_w}x{out_h}", "-pix_fmt", "rgb24", "-r", str(fps),
        "-i", "pipe:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-vframes", str(total_frames),
        output_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for n in range(total_frames):
            t = n / max(total_frames - 1, 1)
            zoom = start_zoom + (end_zoom - start_zoom) * t
            crop_w = src_w / zoom
            crop_h = src_h / zoom
            avail_x = (src_w - crop_w) / 2
            avail_y = (src_h - crop_h) / 2
            left = cx - crop_w / 2 + pan_x * avail_x * t
            top = cy - crop_h / 2 + pan_y * avail_y * t
            frame = img.transform(
                (out_w, out_h),
                Image.Transform.EXTENT,
                (left, top, left + crop_w, top + crop_h),
                Image.Resampling.BICUBIC,
            )
            proc.stdin.write(frame.tobytes())
    except Exception as e:
        proc.kill()
        raise RuntimeError(f"Ken Burns frame write failed for {image_path}: {e}") from e
    finally:
        proc.stdin.close()
        proc.wait()


# ---------------------------------------------------------------------------
# burn_captions
# ---------------------------------------------------------------------------

def _ffmpeg_has_drawtext() -> bool:
    """Return True if the system ffmpeg was compiled with libfreetype (drawtext filter)."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True, text=True,
    )
    return "drawtext" in result.stdout


def burn_captions(
    video_path: str,
    words_json: str,
    output_path: str | None = None,
    words_per_chunk: int = 3,
    fontsize: int = 70,
) -> str:
    """Burn word-by-word captions onto a video.

    Tries ffmpeg drawtext first. Falls back to Pillow frame-by-frame rendering
    when ffmpeg lacks libfreetype (e.g. minimal Homebrew builds).

    words_json: JSON string of [{word, start, end}] or path to vo_words.json
    Returns captioned video path.
    """
    wjson_path = Path(words_json)
    if wjson_path.exists():
        payload = json.loads(wjson_path.read_text())
        words: list[dict] = payload if isinstance(payload, list) else payload.get("words", [])
    else:
        payload = json.loads(words_json)
        words = payload if isinstance(payload, list) else payload.get("words", [])

    if not words:
        log.warning("burn_captions: no words, returning original video")
        return video_path

    out = Path(output_path or str(Path(video_path).with_stem(Path(video_path).stem + "_captioned")))
    out.parent.mkdir(parents=True, exist_ok=True)

    # Group into chunks
    chunks: list[dict] = []
    for i in range(0, len(words), words_per_chunk):
        group = words[i: i + words_per_chunk]
        chunks.append({
            "text": " ".join(w["word"] for w in group),
            "start": group[0]["start"],
            "end": group[-1]["end"],
        })

    if _ffmpeg_has_drawtext():
        _burn_captions_drawtext(video_path, chunks, out, fontsize)
    else:
        log.info("burn_captions: drawtext unavailable, using Pillow fallback")
        _burn_captions_pillow(video_path, chunks, out, fontsize)

    log.info("burn_captions: wrote %s", out)
    return str(out)


def _burn_captions_drawtext(
    video_path: str,
    chunks: list[dict],
    out: Path,
    fontsize: int,
) -> None:
    font = _find_font()
    filters = []
    for chunk in chunks:
        text = chunk["text"].replace("'", "\u2019").replace(":", "\\:").replace("\\", "\\\\")
        start = chunk["start"]
        end = chunk["end"]
        filters.append(
            f"drawtext=font='{font}'"
            f":text='{text}'"
            f":fontsize={fontsize}"
            f":fontcolor=white"
            f":bordercolor=black:borderw=3"
            f":x=(w-tw)/2"
            f":y=h-th-160"
            f":enable='between(t,{start},{end})'"
        )
    filter_str = ",".join(filters)
    result = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", video_path,
         "-vf", filter_str,
         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
         "-c:a", "copy",
         str(out)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"burn_captions (drawtext) failed:\n{result.stderr[:500]}")


def _burn_captions_pillow(
    video_path: str,
    chunks: list[dict],
    out: Path,
    fontsize: int,
) -> None:
    """Pillow-based caption burn: decode each frame, draw text, pipe to ffmpeg."""
    from PIL import Image, ImageDraw, ImageFont  # type: ignore[import]

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

    # Load font
    pil_font: Any = None
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

    # Build chunk lookup: frame_index → text
    def text_at(t: float) -> str:
        for chunk in chunks:
            if chunk["start"] <= t < chunk["end"]:
                return chunk["text"]
        return ""

    # Decode raw frames from input video
    decode = subprocess.Popen(
        ["ffmpeg", "-i", video_path, "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
         "-hide_banner", "-loglevel", "error"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    # Encode output with audio mux in a second pass — write frames to tmp file first
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
                    y = vid_h - th - 160
                    # Draw stroke
                    for dx, dy in [(-3, -3), (3, -3), (-3, 3), (3, 3), (0, -3), (0, 3), (-3, 0), (3, 0)]:
                        draw.text((x + dx, y + dy), caption, font=pil_font, fill=(0, 0, 0))
                    draw.text((x, y), caption, font=pil_font, fill=(255, 255, 255))

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
        if encode.returncode != 0:
            raise RuntimeError(f"Pillow caption encode failed: {encode.stderr.read()[:300]}")  # type: ignore[union-attr]

        # Mux audio back in
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


def _find_font() -> str:
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    raise FileNotFoundError(f"No usable font found. Tried: {candidates}")


# ---------------------------------------------------------------------------
# write_manifest / read_manifest
# ---------------------------------------------------------------------------

def write_manifest(manifest_json: str, manifest_path: str) -> str:
    """Write a manifest dict (JSON string) to a YAML file. Returns the path."""
    data = json.loads(manifest_json)
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))
    log.info("write_manifest: %s", path)
    return str(path)


def read_manifest(manifest_path: str) -> str:
    """Read a manifest YAML file and return its contents as JSON string."""
    data = yaml.safe_load(Path(manifest_path).read_text())
    return json.dumps(data)
