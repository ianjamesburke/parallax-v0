"""Avatar generation, chroma-key, and PiP compositing.

Three-stage pipeline:
  1. `generate_avatar_clips` — call fal.ai/creatify/aurora to generate a
     lip-synced talking-head from the character image + voiceover audio.
  2. `key_avatar_track` — pre-key the blue/green-screen clip once into a
     ProRes 4444 .mov with real alpha, so subsequent composites don't
     have to re-run chromakey on every pass.
  3. `burn_avatar` — composite the (raw or pre-keyed) avatar track as a
     picture-in-picture overlay onto the main video timeline.

The chroma-key chain is the most regression-prone part of the pipeline
(see commit 0241c22 for the ProRes color-range fix); tests at
tests/test_avatar_chain.py exercise the full chain end-to-end.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .ffmpeg_utils import _get_ffmpeg
from .log import get_logger

log = get_logger("tools_video")


def generate_avatar_clips(
    scenes_json: str,
    audio_path: str,
    character_image: str,
    avatar_scene_indices: list[int],
    out_dir: str,
    aurora_prompt: str | None = None,
    full_audio: bool = False,
) -> str:
    """Generate a lip-synced avatar track via fal-ai/creatify/aurora.

    full_audio=True (recommended): one Aurora call with the full voiceover — one
    continuous talking clip starting at t=0. avatar_scene_indices is ignored.

    full_audio=False (legacy): splits audio per scene, calls Aurora once per scene,
    concatenates. Use only when the avatar should only appear during specific scenes.

    Returns JSON:
      {"clips": [...], "avatar_track": path, "track_start_s": float}
    """
    import urllib.request
    import fal_client

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    aurora_args: dict = {"resolution": "720p"}
    if aurora_prompt:
        aurora_args["prompt"] = aurora_prompt

    log.info("avatar: uploading character image %s", character_image)
    aurora_args["image_url"] = fal_client.upload_file(Path(character_image))

    if full_audio:
        log.info("avatar: full-audio mode — uploading full voiceover")
        aurora_args["audio_url"] = fal_client.upload_file(Path(audio_path))
        log.info("avatar: calling Aurora (full voiceover)")
        try:
            result = fal_client.subscribe("fal-ai/creatify/aurora", arguments=aurora_args)
        except Exception as e:
            raise RuntimeError(f"Aurora failed: {e}") from e
        video_url = (result.get("video") or {}).get("url") or result.get("url")
        if not video_url:
            raise RuntimeError(f"Aurora returned no video URL: {result}")
        avatar_track = out / "avatar_track.mp4"
        log.info("avatar: downloading full clip")
        urllib.request.urlretrieve(video_url, str(avatar_track))
        log.info("avatar: track written %s", avatar_track)
        return json.dumps({
            "clips": [{"index": -1, "path": str(avatar_track), "start_s": 0.0}],
            "avatar_track": str(avatar_track),
            "track_start_s": 0.0,
        })

    # Per-scene mode (legacy)
    ffmpeg = _get_ffmpeg()
    scenes: list[dict] = json.loads(scenes_json)
    clips: list[dict] = []
    for scene in scenes:
        idx = scene["index"]
        if idx not in avatar_scene_indices:
            continue
        start_s = scene.get("start_s", 0.0)
        dur = scene.get("duration_s", 0.0)
        if dur <= 0:
            log.warning("avatar: scene %d has zero duration, skipping", idx)
            continue

        audio_clip = out / f"avatar_audio_{idx:03d}.mp3"
        subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
             "-i", audio_path, "-ss", str(start_s), "-t", str(dur),
             "-c", "copy", str(audio_clip)],
            check=True,
        )
        log.info("avatar: scene %d — uploading audio clip (%.2fs)", idx, dur)
        scene_args = {**aurora_args, "audio_url": fal_client.upload_file(audio_clip)}
        log.info("avatar: scene %d — calling Aurora", idx)
        try:
            result = fal_client.subscribe("fal-ai/creatify/aurora", arguments=scene_args)
        except Exception as e:
            raise RuntimeError(f"Aurora failed for scene {idx}: {e}") from e
        video_url = (result.get("video") or {}).get("url") or result.get("url")
        if not video_url:
            raise RuntimeError(f"Aurora returned no video URL for scene {idx}: {result}")
        clip_path = out / f"avatar_clip_{idx:03d}.mp4"
        log.info("avatar: scene %d — downloading clip", idx)
        urllib.request.urlretrieve(video_url, str(clip_path))
        clips.append({"index": idx, "path": str(clip_path),
                      "start_s": start_s, "end_s": scene.get("end_s", start_s + dur),
                      "duration_s": dur})

    if not clips:
        raise RuntimeError("No avatar clips generated — check avatar_scene_indices")

    concat_list = out / "avatar_concat.txt"
    concat_list.write_text("\n".join(f"file '{c['path']}'" for c in clips))
    avatar_track = out / "avatar_track.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(concat_list),
         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
         "-c:a", "aac", str(avatar_track)],
        check=True,
    )
    log.info("avatar: track written %s (%d clips)", avatar_track, len(clips))
    return json.dumps({
        "clips": clips,
        "avatar_track": str(avatar_track),
        "track_start_s": clips[0]["start_s"],
    })


def key_avatar_track(
    avatar_track: str,
    chroma_key: str,
    output_path: str | None = None,
    similarity: float = 0.30,
    blend: float = 0.03,
) -> str:
    """Apply chromakey to avatar_track once, saving a ProRes 4444 clip with alpha.

    The result can be overlaid directly without any chroma key filter, eliminating
    the need to re-key on every composite pass.
    """
    src = Path(avatar_track)
    out = Path(output_path) if output_path else src.with_stem(src.stem + "_keyed").with_suffix(".mov")
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _get_ffmpeg()
    result = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(src),
         "-vf", f"format=yuva444p12le,chromakey={chroma_key}:{similarity}:{blend}",
         "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
         str(out)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"key_avatar_track failed:\n{result.stderr[:800]}")
    log.info("key_avatar_track: wrote %s", out)
    return str(out)


def burn_avatar(
    video_path: str,
    avatar_track: str,
    track_start_s: float,
    output_path: str | None = None,
    position: str = "bottom_left",
    size: float = 0.40,
    out_width: int = 1080,
    chroma_key: str | None = None,
    chroma_similarity: float = 0.30,
    chroma_blend: float = 0.10,
    y_offset_pct: float | None = None,
    crop_px: int = 0,
) -> str:
    """Composite a talking avatar track as PiP over a video.

    avatar_track: path to avatar clip — either raw (pass chroma_key to key at composite
                  time) or pre-keyed .mov with alpha (omit chroma_key for clean overlay).
    track_start_s: when in the main video the avatar track should begin
    position: bottom_left | bottom_right | top_left | top_right
    size: avatar width as fraction of out_width (default 0.40 = 40% of output frame)
    out_width: output frame width in pixels (used to convert size to absolute pixels)
    chroma_key: key out this color at composite time (use only if not pre-keyed)
    y_offset_pct: bottom edge position as fraction from screen bottom (0.4 = 40% up)
    crop_px: pixels to crop from top AND bottom of avatar before scaling (masks edge artifacts)
    """
    src = Path(video_path)
    out = Path(output_path) if output_path else src.with_stem(src.stem + "_avatar")
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _get_ffmpeg()

    avatar_px_w = int(out_width * size)
    margin = 20
    crop_filter = f"crop=iw:ih-{2*crop_px}:0:{crop_px}," if crop_px > 0 else ""

    if chroma_key:
        scale_filter = f"[1:v]{crop_filter}scale={avatar_px_w}:-1[av_raw]"
        key_filter = f"[av_raw]chromakey={chroma_key}:{chroma_similarity}:{chroma_blend}[av]"
        av_label = "[av]"
        extra_filters = f"{scale_filter};{key_filter}"
    else:
        # Pre-keyed ProRes 4444 with alpha — preserve alpha channel through scale
        scale_filter = f"[1:v]{crop_filter}scale={avatar_px_w}:-1,format=rgba[av_raw]"
        av_label = "[av_raw]"
        extra_filters = scale_filter

    is_right = "right" in position
    x = f"W-w-{margin}" if is_right else str(margin)
    if y_offset_pct is not None:
        # Place avatar bottom edge at y_offset_pct from screen bottom
        y = f"H*{1.0 - y_offset_pct:.4f}-h"
    elif "top" in position:
        y = str(margin)
    else:
        y = f"H-h-{margin}"
    xy = f"{x}:{y}"
    overlay_filter = f"[0:v]{av_label}overlay={xy}:format=auto:eof_action=endall[out]"
    filter_complex = f"{extra_filters};{overlay_filter}"

    result = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-i", video_path,
         "-itsoffset", str(track_start_s), "-i", avatar_track,
         "-filter_complex", filter_complex,
         "-map", "[out]", "-map", "0:a",
         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
         "-c:a", "copy", str(out)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"burn_avatar failed:\n{result.stderr[:800]}")
    log.info("burn_avatar: wrote %s", out)
    return str(out)
