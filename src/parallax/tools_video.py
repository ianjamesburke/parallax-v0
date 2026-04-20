"""Video pipeline tools: voiceover → Ken Burns → captions.

Each function is a self-contained tool callable by the agent.
No manifest dependency in signatures — the agent manages state in its context.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from .log import get_logger
from .shim import is_test_mode, output_dir

log = get_logger("tools_video")

# Offline shorthand aliases → ElevenLabs voice IDs.
# These are common voices kept here so the tool works without a network call
# when a well-known shorthand is used. For anything else, _resolve_voice()
# fetches the full voice list and matches by name.
VOICE_IDS: dict[str, str] = {
    "george": "JBFqnCBsd6RMkjVDRZzb",
    "rachel": "21m00Tcm4TlvDq8ikWAM",
    "domi": "AZnzlk1XvdvUeBnXmlld",
    "bella": "EXAVITQu4vr4xnSDxMaL",
    "daniel": "onwK4e9ZLuTAKqWW03F9",
    "arnold": "VR6AewLTigWG4xSOukaG",
}

_RAW_ID_MIN_LEN = 18  # ElevenLabs IDs are 20 alphanumeric chars


def _resolve_voice(voice: str, api_key: str) -> str:
    """Resolve a voice name or ID to an ElevenLabs voice_id.

    Resolution order:
      1. Offline alias dict (e.g. 'george') — no API call needed.
      2. Raw voice ID — looks like 18+ alphanumeric chars, use as-is.
      3. Live ElevenLabs name match — fetches voice list, matches by name
         (exact first, then partial case-insensitive).
    Raises ValueError with a helpful message if no match is found.
    """
    # 1. Known alias
    alias_id = VOICE_IDS.get(voice.lower())
    if alias_id:
        return alias_id

    # 2. Looks like a raw voice ID (alphanumeric, no spaces, long enough)
    if len(voice) >= _RAW_ID_MIN_LEN and voice.replace("-", "").isalnum():
        return voice

    # 3. Fetch and match by name
    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        resp = client.voices.get_all()
        voices = resp.voices
    except Exception as e:
        raise ValueError(f"Could not fetch ElevenLabs voice list to resolve {voice!r}: {e}") from e

    needle = voice.lower()
    # Exact name match first
    for v in voices:
        if (v.name or "").lower() == needle:
            log.info("voice resolved: %r → %s (%s)", voice, v.voice_id, v.name)
            return v.voice_id
    # Partial match — pick first
    matches = [v for v in voices if needle in (v.name or "").lower()]
    if matches:
        chosen = matches[0]
        log.info("voice resolved (partial): %r → %s (%s)", voice, chosen.voice_id, chosen.name)
        return chosen.voice_id

    raise ValueError(
        f"No ElevenLabs voice found matching {voice!r}. "
        f"Run 'parallax voices --filter {voice}' to search, or pass a raw voice ID."
    )

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}

_FONTS_DIR = Path(__file__).parent / "fonts"

# Five TikTok-native caption styles. Each is applied by both the drawtext and
# Pillow code paths, so they stay visually consistent regardless of backend.
CAPTION_STYLES: dict[str, dict] = {
    "bangers": {
        # Kill Tony style — chunky, heavy stroke, all-caps
        # x_expr uses 1.2× tw to give room for Bangers' rightward italic slant
        "fontfile": "Bangers-Regular.ttf",
        "fontcolor": "white",
        "bordercolor": "black",
        "borderw": 6,
        "shadowx": 0,
        "shadowy": 0,
        "box": False,
        "uppercase": True,
        "x_expr": "(w-tw)/2",
        "y_expr": "h*65/100-th",
    },
    "impact": {
        # Classic meme — system Impact, thin outline
        "fontfile": None,
        "system_font": "/Library/Fonts/Impact.ttf",
        "fontcolor": "white",
        "bordercolor": "black",
        "borderw": 3,
        "shadowx": 0,
        "shadowy": 0,
        "box": False,
        "uppercase": True,
        "x_expr": "(w-tw)/2",
        "y_expr": "h*65/100-th",
    },
    "bebas": {
        # Viral TikTok — Bebas Neue, electric yellow, thick stroke
        "fontfile": "BebasNeue-Regular.ttf",
        "fontcolor": "#FFE600",
        "bordercolor": "black",
        "borderw": 5,
        "shadowx": 0,
        "shadowy": 0,
        "box": False,
        "uppercase": True,
        "x_expr": "(w-tw)/2",
        "y_expr": "h*65/100-th",
    },
    "anton": {
        # Bold podcast — Anton, white with soft drop shadow
        "fontfile": "Anton-Regular.ttf",
        "fontcolor": "white",
        "bordercolor": "black",
        "borderw": 2,
        "shadowx": 4,
        "shadowy": 4,
        "box": False,
        "uppercase": True,
        "x_expr": "(w-tw)/2",
        "y_expr": "h*65/100-th",
    },
    "clean": {
        # Modern/clean — Montserrat Black, white on semi-transparent dark pill
        "fontfile": "Montserrat-Black.ttf",
        "fontcolor": "white",
        "bordercolor": None,
        "borderw": 0,
        "shadowx": 0,
        "shadowy": 0,
        "box": True,
        "boxcolor": "black@0.55",
        "boxborderw": 20,
        "uppercase": False,
        "x_expr": "(w-tw)/2",
        "y_expr": "h*65/100-th",
    },
}


# ---------------------------------------------------------------------------
# scan_project_folder
# ---------------------------------------------------------------------------

_CLIP_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".png", ".jpg", ".jpeg", ".webp"}


def scan_project_folder(folder_path: str) -> str:
    """Scan a project folder for a script and either numbered clips or a character image.

    Returns JSON with:
      - mode: "video_clips" (numbered clip files found) or "ken_burns" (still images / no clips)
      - script_path, script_text: the script file
      - clips: {str(number): path} — only present in video_clips mode
      - character_image_path: only relevant in ken_burns mode
      - folder: resolved folder path
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

    # Detect numbered clips (e.g. 001.mp4, 002.mov, 011.png)
    numbered_clips: dict[int, str] = {}
    for f in sorted(folder.iterdir()):
        if re.match(r"^\d+$", f.stem) and f.suffix.lower() in _CLIP_EXTS and f.is_file():
            numbered_clips[int(f.stem)] = str(f)

    mode = "video_clips" if len(numbered_clips) >= 3 else "ken_burns"

    # Find character image (only meaningful in ken_burns mode; exclude numbered clips)
    numbered_paths = set(numbered_clips.values())
    char_path: Path | None = None
    for name in ("character.jpg", "character.jpeg", "character.png", "character.webp"):
        candidate = folder / name
        if candidate.exists():
            char_path = candidate
            break
    if char_path is None:
        imgs = [
            f for f in folder.iterdir()
            if f.suffix.lower() in IMAGE_EXTS and f.is_file() and str(f) not in numbered_paths
        ]
        if len(imgs) == 1:
            char_path = imgs[0]
        elif len(imgs) > 1:
            char_path = sorted(imgs)[0]
            log.info("Multiple images found; using %s as character reference", char_path.name)

    # Create versioned output directory: {folder}/.parallax/output/v1/, v2/, ...
    parallax_dir = folder / ".parallax"
    output_base = parallax_dir / "output"
    output_base.mkdir(parents=True, exist_ok=True)
    existing_versions = []
    for d in output_base.iterdir():
        if d.is_dir() and d.name.startswith("v"):
            try:
                existing_versions.append(int(d.name[1:]))
            except ValueError:
                pass
    version = max(existing_versions, default=0) + 1
    versioned_output = output_base / f"v{version}"
    versioned_output.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "folder": str(folder),
        "mode": mode,
        "version": version,
        "output_dir": str(versioned_output),
        "script_path": str(script_path) if script_path else None,
        "script_text": script_path.read_text().strip() if script_path else None,
        "character_image_path": str(char_path) if char_path else None,
        "clips": {str(num): path for num, path in sorted(numbered_clips.items())} if mode == "video_clips" else {},
    }
    log.info("scan_project_folder: mode=%s script=%s clips=%d version=v%d", mode, script_path, len(numbered_clips), version)
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

    voice_id = _resolve_voice(voice, key)
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

    # Derive word timestamps from character-level alignment.
    # Space characters mark the acoustic END of the preceding word — capture those
    # timestamps so we know when speech actually stops vs when the next word starts.
    # This lets _trim_long_pauses detect inter-sentence silence accurately.
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
                # Space start = when the preceding word's acoustic sound ended
                words_raw.append({"word": cur_word, "start": word_start, "acoustic_end": starts[i]})
                cur_word = ""
                word_start = None
        else:
            if word_start is None:
                word_start = starts[i]
            cur_word += ch
    if cur_word and word_start is not None:
        words_raw.append({"word": cur_word, "start": word_start, "acoustic_end": None})

    # End times: use acoustic_end (space timestamp) when available; fall back to next word's start
    words_with_ends: list[dict] = []
    for i, w in enumerate(words_raw):
        if w.get("acoustic_end") is not None:
            end = w["acoustic_end"]
        elif i + 1 < len(words_raw):
            end = words_raw[i + 1]["start"]
        else:
            end = raw_duration
        words_with_ends.append({
            "word": w["word"],
            "start": round(w["start"], 3),
            "end": round(end, 3),
        })

    # Apply atempo speed-up
    audio_path = dest / "voiceover.mp3"
    sped_path = dest / "voiceover_sped_tmp.mp3"
    words_sped, sped_duration = _apply_atempo(raw_path, words_with_ends, sped_path, speed)

    # Surgically remove inter-word gaps > 400ms using word timestamps (no amplitude detection)
    words_sped, sped_duration = _trim_long_pauses(sped_path, words_sped, audio_path)
    sped_path.unlink(missing_ok=True)

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


def _trim_long_pauses(
    audio_path: Path,
    words: list[dict],
    out_path: Path,
    max_gap_s: float = 0.4,
    keep_gap_s: float = 0.1,
) -> tuple[list[dict], float]:
    """Remove inter-word gaps > max_gap_s, trimming each to keep_gap_s.

    Uses ffmpeg atrim+concat for surgical cuts driven by word timestamps.
    No amplitude detection — purely timestamp-based.
    Returns (adjusted_words, new_duration_s).
    """
    import shutil

    gaps: list[tuple[float, float]] = []
    for i in range(len(words) - 1):
        gap = words[i + 1]["start"] - words[i]["end"]
        if gap > max_gap_s:
            gaps.append((words[i]["end"] + keep_gap_s, words[i + 1]["start"]))

    if not gaps:
        shutil.copy2(audio_path, out_path)
        return list(words), words[-1]["end"] if words else 0.0

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True,
    )
    total_dur = float(probe.stdout.strip()) if probe.stdout.strip() else (words[-1]["end"] if words else 0.0)

    # Build the audio segments to keep
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for trim_start, trim_end in gaps:
        if cursor < trim_start:
            keep.append((cursor, trim_start))
        cursor = trim_end
    if cursor < total_dur:
        keep.append((cursor, total_dur))

    n = len(keep)
    parts = [
        f"[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS[s{i}]"
        for i, (s, e) in enumerate(keep)
    ]
    concat_in = "".join(f"[s{i}]" for i in range(n))
    parts.append(f"{concat_in}concat=n={n}:v=0:a=1[out]")

    result = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(audio_path),
         "-filter_complex", ";".join(parts),
         "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "128k",
         str(out_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.warning("_trim_long_pauses: ffmpeg failed (%s), keeping original", result.stderr[:200])
        shutil.copy2(audio_path, out_path)
        return list(words), words[-1]["end"] if words else 0.0

    # Shift timestamps: each word shifts back by total gap removed before it
    shifts = [0.0] * len(words)
    for trim_start, trim_end in gaps:
        removed = trim_end - trim_start
        for j, w in enumerate(words):
            if w["start"] >= trim_end:
                shifts[j] += removed

    adjusted = [
        {"word": w["word"],
         "start": round(w["start"] - shifts[j], 3),
         "end": round(w["end"] - shifts[j], 3)}
        for j, w in enumerate(words)
    ]
    new_dur = adjusted[-1]["end"] if adjusted else 0.0
    total_removed = sum(e - s for s, e in gaps)
    log.info("_trim_long_pauses: %d gaps removed (%.2fs total), duration %.2fs→%.2fs",
             len(gaps), total_removed, total_dur, new_dur)
    return adjusted, new_dur


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
# assemble_clip_video
# ---------------------------------------------------------------------------

def assemble_clip_video(
    scenes_json: str,
    audio_path: str,
    output_path: str | None = None,
    resolution: str | None = None,
) -> str:
    """Assemble a video from pre-existing numbered clips + aligned scene durations.

    Use this instead of ken_burns_assemble when scan_project_folder returns mode='video_clips'.
    Each scene in scenes_json must have clip_paths (list of file paths) and duration_s.
    Clips are looped or trimmed to fill each scene's target duration.
    Returns the assembled video path.
    """
    scenes: list[dict] = json.loads(scenes_json)
    if not scenes:
        raise ValueError("No scenes provided")

    # Auto-detect resolution from first available video clip
    if resolution is None:
        for scene in scenes:
            for cp in scene.get("clip_paths", []):
                if Path(cp).exists() and Path(cp).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    probe = subprocess.run(
                        ["ffprobe", "-v", "error", "-select_streams", "v:0",
                         "-show_entries", "stream=width,height",
                         "-of", "csv=p=0", cp],
                        capture_output=True, text=True,
                    )
                    parts = probe.stdout.strip().split(",")
                    if len(parts) >= 2:
                        resolution = f"{parts[0]}x{parts[1]}"
                        break
            if resolution:
                break
        resolution = resolution or "720x1280"

    out_w, out_h = (int(x) for x in resolution.split("x"))
    out = Path(output_path or str(output_dir() / "clip_assembly.mp4"))
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        segment_paths: list[str] = []
        for i, scene in enumerate(scenes):
            clip_paths = scene.get("clip_paths", [])
            duration_s = float(scene.get("duration_s", 5.0))
            if not clip_paths:
                log.warning("Scene %d: no clip_paths, skipping", i)
                continue
            segment_path = str(Path(tmp_dir) / f"seg_{i:04d}.mp4")
            _make_clip_segment(clip_paths, duration_s, segment_path, out_w, out_h, tmp_dir, i)
            segment_paths.append(segment_path)

        if not segment_paths:
            raise RuntimeError("No scenes with valid clip_paths to assemble")

        # Concat all segments — re-encode to avoid black-first-frame from stream copy
        list_file = Path(tmp_dir) / "segments.txt"
        list_file.write_text("\n".join(f"file '{p}'" for p in segment_paths))
        no_audio = Path(tmp_dir) / "no_audio.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", str(list_file),
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

    log.info("assemble_clip_video: wrote %s (res=%s)", out, resolution)
    return str(out)


def _make_clip_segment(
    clip_paths: list[str],
    duration_s: float,
    output_path: str,
    out_w: int,
    out_h: int,
    tmp_dir: str,
    scene_idx: int,
) -> None:
    """Normalize clips for one scene, concat them, then loop/trim to duration_s."""
    image_exts = {".png", ".jpg", ".jpeg", ".webp"}
    scale_filter = (
        f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
        f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2"
    )
    normalized: list[str] = []

    for j, cp in enumerate(clip_paths):
        p = Path(cp)
        if not p.exists():
            log.warning("Clip not found: %s, skipping", cp)
            continue
        norm_path = str(Path(tmp_dir) / f"norm_{scene_idx:04d}_{j:04d}.mp4")

        if p.suffix.lower() in image_exts:
            # Apply Ken Burns so no still frames appear in the final video
            _make_kb_clip(str(p), duration_s, norm_path, f"{out_w}x{out_h}", scene_idx)
        else:
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(p),
                 "-vf", scale_filter,
                 "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                 "-r", "30", "-an", norm_path],
                check=True,
            )
        normalized.append(norm_path)

    if not normalized:
        raise RuntimeError(f"Scene {scene_idx}: no valid clips found in {clip_paths}")

    # Concat all clips within this scene
    if len(normalized) == 1:
        combined = normalized[0]
    else:
        concat_list = Path(tmp_dir) / f"inner_{scene_idx:04d}.txt"
        concat_list.write_text("\n".join(f"file '{p}'" for p in normalized))
        combined = str(Path(tmp_dir) / f"combined_{scene_idx:04d}.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
             combined],
            check=True,
        )

    # Loop/trim to exact target duration
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-stream_loop", "-1", "-i", combined,
         "-t", str(duration_s),
         "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
         "-an", output_path],
        check=True,
    )


# ---------------------------------------------------------------------------
# burn_captions
# ---------------------------------------------------------------------------

_FFMPEG_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"


def _get_ffmpeg() -> str:
    """Return the best available ffmpeg binary — ffmpeg-full (has drawtext) first."""
    import shutil
    if Path(_FFMPEG_FULL).exists():
        return _FFMPEG_FULL
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffmpeg_has_drawtext() -> bool:
    """Return True if the resolved ffmpeg binary supports the drawtext filter."""
    result = subprocess.run(
        [_get_ffmpeg(), "-hide_banner", "-filters"],
        capture_output=True, text=True,
    )
    return "drawtext" in result.stdout


def burn_captions(
    video_path: str,
    words_json: str,
    output_path: str | None = None,
    words_per_chunk: int = 1,
    fontsize: int = 55,
    caption_style: str = "bangers",
) -> str:
    """Burn word-by-word captions onto a video.

    Tries ffmpeg drawtext first. Falls back to Pillow frame-by-frame rendering
    when ffmpeg lacks libfreetype (e.g. minimal Homebrew builds).

    words_json: JSON string of [{word, start, end}] or path to vo_words.json
    caption_style: one of bangers, impact, bebas, anton, clean
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

    style = CAPTION_STYLES.get(caption_style, CAPTION_STYLES["bangers"])
    if _ffmpeg_has_drawtext():
        try:
            _burn_captions_drawtext(video_path, chunks, out, fontsize, style)
        except RuntimeError as e:
            log.warning("burn_captions: drawtext failed (%s), falling back to Pillow", e)
            if out.exists():
                out.unlink()
            _burn_captions_pillow(video_path, chunks, out, fontsize, style)
    else:
        log.info("burn_captions: drawtext unavailable, using Pillow fallback")
        _burn_captions_pillow(video_path, chunks, out, fontsize, style)

    log.info("burn_captions: wrote %s", out)
    return str(out)


def _style_drawtext_filter(style: dict, text: str, start: float, end: float, fontsize: int) -> str:
    # Escape order matters: backslashes first, then other special chars.
    # Reversing this order would double-escape the backslashes inserted by later steps.
    escaped = text.replace("\\", "\\\\").replace("'", "\u2019").replace(":", "\\:")
    if style.get("uppercase"):
        escaped = escaped.upper()

    fontfile = style.get("fontfile")
    font_path = str(_FONTS_DIR / fontfile) if fontfile else style.get("system_font", "")

    x_expr = style.get("x_expr", "(w-tw)/2")
    y_expr = style.get("y_expr", "h*0.65-th")

    kv: list[str] = [
        f"fontfile='{font_path}'",
        f"text='{escaped}'",
        f"fontsize={fontsize}",
        f"fontcolor={style['fontcolor']}",
    ]
    if style.get("borderw") and style.get("bordercolor"):
        kv += [f"bordercolor={style['bordercolor']}", f"borderw={style['borderw']}"]
    if style.get("shadowx") or style.get("shadowy"):
        kv += [
            f"shadowx={style.get('shadowx', 0)}",
            f"shadowy={style.get('shadowy', 0)}",
            "shadowcolor=black@0.7",
        ]
    if style.get("box"):
        kv += ["box=1", f"boxcolor={style['boxcolor']}", f"boxborderw={style.get('boxborderw', 10)}"]
    kv += [f"x={x_expr}", f"y={y_expr}", f"enable='gte(t,{start})*lt(t,{end})'"]
    return "drawtext=" + ":".join(kv)


def _parse_color(color: str | None) -> tuple[int, int, int]:
    if not color:
        return (255, 255, 255)
    color = color.split("@")[0].strip()
    if color.lower() == "white":
        return (255, 255, 255)
    if color.lower() == "black":
        return (0, 0, 0)
    if color.startswith("#") and len(color) == 7:
        return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))
    return (255, 255, 255)


def _burn_captions_drawtext(
    video_path: str,
    chunks: list[dict],
    out: Path,
    fontsize: int,
    style: dict,
) -> None:
    filters = [
        _style_drawtext_filter(style, c["text"], c["start"], c["end"], fontsize)
        for c in chunks
    ]
    result = subprocess.run(
        [_get_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", video_path,
         "-vf", ",".join(filters),
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
    style: dict,
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




# ---------------------------------------------------------------------------
# burn_headline
# ---------------------------------------------------------------------------

def burn_headline(
    video_path: str,
    text: str,
    output_path: str | None = None,
    fontsize: int = 64,
    bg_color: str = "white",
    text_color: str = "black",
    font_name: str = "bangers",
    y_position: str = "h*12/100",
    end_time_s: float | None = None,
) -> str:
    """Overlay a headline with a solid block background (Instagram/TikTok style).

    end_time_s: if set, headline fades out at this timestamp (use first scene's end_s
                so the headline is only visible during the intro).
    bg_color / text_color accept any ffmpeg color string (e.g. 'white', 'black', '#FF0000').
    y_position is an ffmpeg expression for the TOP of the text block (default: 12% from top).
    font_name: one of bangers, impact, bebas, anton, clean (uses bundled fonts).
    Returns the output video path.
    """
    out = Path(output_path or str(Path(video_path).with_stem(Path(video_path).stem + "_headline")))
    out.parent.mkdir(parents=True, exist_ok=True)

    style = CAPTION_STYLES.get(font_name, CAPTION_STYLES["bangers"])
    fontfile = style.get("fontfile")
    font_path = str(_FONTS_DIR / fontfile) if fontfile else style.get("system_font", "")

    escaped = text.replace("\\", "\\\\").replace("'", "\u2019").replace(":", "\\:")
    if style.get("uppercase"):
        escaped = escaped.upper()

    pad = max(12, fontsize // 4)
    enable_clause = f":enable='lt(t,{end_time_s})'" if end_time_s is not None else ""
    filter_str = (
        f"drawtext=fontfile='{font_path}'"
        f":text='{escaped}'"
        f":fontsize={fontsize}"
        f":fontcolor={text_color}"
        f":box=1:boxcolor={bg_color}:boxborderw={pad}"
        f":x=(w-tw)/2"
        f":y={y_position}"
        f"{enable_clause}"
    )

    result = subprocess.run(
        [_get_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", video_path,
         "-vf", filter_str,
         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
         "-c:a", "copy",
         str(out)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"burn_headline failed:\n{result.stderr[:500]}")
    log.info("burn_headline: wrote %s", out)
    return str(out)


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
