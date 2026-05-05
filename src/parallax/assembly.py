"""Video assembly: scene alignment, Ken Burns stills, and clip-mode concat.

`align_scenes` assigns contiguous start/end times so the video timeline
covers the audio timeline 1:1 (including leading and trailing silence).
`ken_burns_assemble` builds a draft from stills + a voiceover. The
clip-mode pair (`assemble_clip_video` + `_make_clip_segment`) handles
projects whose scenes already have video clips. `_zoom_filter` and
`_make_kb_clip` are the per-scene primitives shared between paths.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from .ffmpeg_utils import _get_ffmpeg, parse_resolution, run_ffmpeg
from .log import get_logger
from .shim import is_test_mode, output_dir

log = get_logger(__name__)


def align_scenes_obj(scenes: list[dict], words_payload: list[dict] | dict) -> list[dict]:
    """Assign start_s/end_s/duration_s so scenes form a contiguous cover of the audio.

    scenes: list of {index, vo_text, ...}
    words_payload: either [{word, start, end}, ...] or
                   {"words": [...], "total_duration_s": float}.
                   When `total_duration_s` is supplied the final scene is
                   extended to that value so the assembled video matches the
                   audio length exactly — without it, trailing silence past
                   the last word is lost on mux (`-shortest` trims audio).

    Invariants enforced on output:
      - scenes[0].start_s == 0          (covers any leading silence)
      - scenes[i].start_s == scenes[i-1].end_s  for i > 0 (no gaps)
      - scenes[-1].end_s == total_duration_s    (covers trailing silence)
      - duration_s = end_s - start_s

    These guarantee the video timeline is 1:1 with the audio timeline, so
    scene cuts land on actual word boundaries and the final mux preserves
    the full voiceover end-to-end.

    Returns updated scenes list (mutates in place, also returns for convenience).
    """
    payload = words_payload
    words: list[dict] = payload if isinstance(payload, list) else payload.get("words", [])

    cursor = 0
    for scene in scenes:
        vo_text = scene.get("vo_text", "").strip()
        if not vo_text:
            continue
        count = len(re.sub(r'\[[^\]]*\]', '', vo_text).split())
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
        cursor += count

    # Resolve total audio duration. Required to cover trailing silence on
    # the last scene; without it the mux would clip the voiceover tail.
    if isinstance(payload, dict) and payload.get("total_duration_s") is not None:
        total = float(payload["total_duration_s"])
    else:
        total = float(words[-1]["end"]) if words else 0.0

    # Make scenes contiguous: each scene starts where the previous one ended.
    # Scene 0 starts at 0 to absorb leading silence; the last scene's end is
    # snapped to total audio duration to absorb trailing silence.
    if scenes:
        scenes[0]["start_s"] = 0.0
        for i in range(1, len(scenes)):
            scenes[i]["start_s"] = scenes[i - 1]["end_s"]
        scenes[-1]["end_s"] = round(total, 3)
        for s in scenes:
            s["duration_s"] = round(s["end_s"] - s["start_s"], 3)

    log.info("align_scenes: %d scenes aligned, total=%.2fs", len(scenes), total)
    from . import runlog
    for s in scenes:
        runlog.event(
            "align.scene",
            level="DEBUG",
            index=s.get("index"),
            start_s=s.get("start_s"),
            end_s=s.get("end_s"),
            duration_s=s.get("duration_s"),
        )
    return scenes


def align_scenes(scenes_json: str, words_json: str) -> str:
    """JSON-string wrapper around align_scenes_obj. Kept for CLI/external callers.

    scenes_json: JSON list of {index, vo_text, ...}
    words_json: JSON of either [{word, start, end}, ...] or
                {"words": [...], "total_duration_s": float}.

    Returns updated scenes as a JSON string.
    """
    scenes: list[dict] = json.loads(scenes_json)
    payload = json.loads(words_json)
    return json.dumps(align_scenes_obj(scenes, payload))


def ken_burns_assemble_obj(
    scenes: list[dict],
    audio_path: str | None,
    output_path: str | None = None,
    resolution: str = "1080x1920",
) -> str:
    """Assemble Ken Burns draft video from stills + aligned scene durations.

    scenes: list of {still_path, duration_s, index?}
    audio_path: path to voiceover.mp3
    output_path: where to write the final .mp4 (default: output/ken_burns_draft.mp4)
    resolution: WxH e.g. "1080x1920" (vertical) or "1920x1080" (landscape)

    Returns the output video path.
    """
    if not scenes:
        raise ValueError("No scenes provided")

    out = Path(output_path or str(output_dir() / "ken_burns_draft.mp4"))
    out.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = _get_ffmpeg()
    w_i, h_i = parse_resolution(resolution)
    w, h = str(w_i), str(h_i)

    with tempfile.TemporaryDirectory() as tmp_dir:
        clip_paths: list[str] = []
        for i, scene in enumerate(scenes):
            dur = float(scene.get("duration_s", 5.0))
            clip_out = str(Path(tmp_dir) / f"scene_{i:04d}.mp4")

            zoom_dir = scene.get("zoom_direction")
            zoom_amount = float(scene.get("zoom_amount", 1.25))

            pre_animated = scene.get("clip_path")
            if pre_animated and Path(pre_animated).exists():
                vf = _zoom_filter(zoom_dir, zoom_amount, dur, w, h)
                probe = run_ffmpeg(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", pre_animated],
                    capture_output=True, text=True,
                )
                clip_dur = float(probe.stdout.strip() or "0")
                src = pre_animated
                if 0 < clip_dur < dur:
                    # Clip is shorter than scene — build ping-pong (fwd+rev) so the
                    # loop seam is a smooth reverse rather than a jump cut.
                    pp_path = str(Path(tmp_dir) / f"pingpong_{i:04d}.mp4")
                    run_ffmpeg(
                        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                         "-i", pre_animated,
                         "-filter_complex",
                         "[0:v]reverse[r];[0:v][r]concat=n=2:v=1:a=0[out]",
                         "-map", "[out]",
                         "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                         pp_path],
                        check=True,
                    )
                    src = pp_path
                run_ffmpeg(
                    [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                     "-stream_loop", "-1", "-i", src, "-t", str(dur),
                     "-vf", vf,
                     "-an", "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                     clip_out],
                    check=True,
                )
            else:
                still = scene.get("still_path") or scene.get("image_path")
                if not still or not Path(still).exists():
                    log.warning("Scene %d: still not found at %r, skipping", i, still)
                    continue
                _make_kb_clip(still, dur, clip_out, resolution=resolution, scene_index=i,
                              zoom_direction=zoom_dir, zoom_amount=zoom_amount)

            clip_paths.append(clip_out)

        if not clip_paths:
            raise RuntimeError("No scenes with valid stills to assemble")

        # Concat — all clips already normalized to same codec/fps/resolution, stream copy is safe
        list_file = Path(tmp_dir) / "clips.txt"
        list_file.write_text("\n".join(f"file '{p}'" for p in clip_paths))
        no_audio = Path(tmp_dir) / "no_audio.mp4"
        run_ffmpeg(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-c:v", "copy", "-an",
             str(no_audio)],
            check=True,
        )

        # Mux with voiceover (skip if no audio provided)
        if audio_path:
            run_ffmpeg(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(no_audio),
                 "-i", str(audio_path),
                 "-c:v", "copy", "-c:a", "aac", "-shortest",
                 str(out)],
                check=True,
            )
        else:
            import shutil as _shutil
            _shutil.copy2(no_audio, out)

    log.info("ken_burns_assemble: wrote %s", out)
    return str(out)


def ken_burns_assemble(
    scenes_json: str,
    audio_path: str | None,
    output_path: str | None = None,
    resolution: str = "1080x1920",
) -> str:
    """JSON-string wrapper around ken_burns_assemble_obj. Kept for CLI/external callers.

    scenes_json: JSON list of {still_path, duration_s, index?}
    Returns the output video path.
    """
    scenes: list[dict] = json.loads(scenes_json)
    return ken_burns_assemble_obj(scenes, audio_path, output_path, resolution)


def _zoom_filter(
    direction: str | None,
    zoom_amount: float,
    duration: float,
    w: str,
    h: str,
    fps: int = 30,
) -> str:
    """Return an FFmpeg -vf filter string that zooms+pans a video clip.

    Uses scale+crop with the `n` frame-counter expression, which reliably
    accumulates across frames (unlike zoompan with d=1 which resets each frame).

    direction: up | down | left | right | in | None (no zoom — normalize only)
    zoom_amount: max zoom factor (e.g. 1.25 = 25% zoom in)
    """
    if not direction:
        return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps}")

    wi, hi = int(w), int(h)
    dur = float(duration)
    zd = float(zoom_amount) - 1.0  # zoom delta (0 at t=0 → zoom_amount-1 at t=dur)

    # Progressive zoom: scale to output size, then scale up further per-frame using eval=frame,
    # then crop the output-size window from the correct anchor position.
    # This gives real zoom-in (not just pan) because the scale factor grows over time.
    # crop filter cannot vary w/h per frame, so we use a fixed-size crop from the growing frame.
    zexpr = f"1+{zd:.4f}*t/{dur}"  # zoom factor expression: 1.0 → zoom_amount over clip

    if direction == "up":
        cx, cy = "(iw-1080)/2", "0"
    elif direction == "down":
        cx, cy = "(iw-1080)/2", f"(ih-{hi})"
    elif direction == "left":
        cx, cy = "0", f"(ih-{hi})/2"
    elif direction == "right":
        cx, cy = f"(iw-{wi})", f"(ih-{hi})/2"
    else:  # "in" — centered
        cx, cy = "(iw-1080)/2", f"(ih-{hi})/2"

    # First scale: fit-to-fill (force_original_aspect_ratio=increase) + center-crop
    # so non-9:16 sources don't get stretched into the target frame. The
    # subsequent per-frame scale operates on a correctly-proportioned base.
    return (
        f"scale={wi}:{hi}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={wi}:{hi},"
        f"scale=w='{wi}*({zexpr})':h='{hi}*({zexpr})':eval=frame:flags=lanczos,"
        f"crop={wi}:{hi}:{cx}:{cy},"
        f"fps={fps}"
    )


def _make_kb_clip(
    image_path: str,
    duration: float,
    output_path: str,
    resolution: str = "1080x1920",
    scene_index: int = 0,
    zoom_direction: str | None = None,
    zoom_amount: float | None = None,
) -> None:
    """Pillow-based Ken Burns with float-precision crop (no zoompan jitter)."""
    from PIL import Image  # type: ignore[import]

    out_w, out_h = parse_resolution(resolution)
    fps = 30
    total_frames = max(1, round(duration * fps))

    if is_test_mode():
        # In test mode, resize directly to output size — no crop, no zoom.
        # Mock images are already 1080×1920; this avoids the center-crop that
        # cuts off text when a square image is scaled into a portrait frame.
        img = Image.open(image_path).convert("RGB")
        img = img.resize((out_w, out_h), Image.Resampling.LANCZOS)
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
        frame_bytes = img.tobytes()
        try:
            for _ in range(total_frames):
                proc.stdin.write(frame_bytes)
        finally:
            proc.stdin.close()
            proc.wait()
        return

    # Motion presets: (start_zoom, end_zoom, pan_x, pan_y)
    motions = [
        (1.0, 1.15, 0.0, 0.0),
        (1.15, 1.0, 0.0, 0.0),
        (1.0, 1.12, 0.4, 0.0),
        (1.0, 1.12, -0.4, 0.0),
        (1.0, 1.12, 0.0, 0.4),
        (1.0, 1.12, 0.0, -0.4),
    ]
    if zoom_direction:
        end_z = zoom_amount if zoom_amount is not None else 1.25
        dir_map = {"up": (0.0, -1.0), "down": (0.0, 1.0),
                   "left": (-1.0, 0.0), "right": (1.0, 0.0), "in": (0.0, 0.0)}
        pan_x, pan_y = dir_map.get(zoom_direction, (0.0, 0.0))
        start_zoom, end_zoom = 1.0, end_z
    else:
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


def assemble_clip_video_obj(
    scenes: list[dict],
    audio_path: str,
    output_path: str | None = None,
    resolution: str | None = None,
) -> str:
    """Assemble a video from pre-existing numbered clips + aligned scene durations.

    Use this instead of ken_burns_assemble_obj when scan_project_folder returns mode='video_clips'.
    Each scene must have clip_paths (list of file paths) and duration_s.
    Clips are looped or trimmed to fill each scene's target duration.
    Returns the assembled video path.
    """
    if not scenes:
        raise ValueError("No scenes provided")

    # Auto-detect resolution from first available video clip
    if resolution is None:
        for scene in scenes:
            for cp in scene.get("clip_paths", []):
                if Path(cp).exists() and Path(cp).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    probe = run_ffmpeg(
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

    out_w, out_h = parse_resolution(resolution)
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
        run_ffmpeg(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
             str(no_audio)],
            check=True,
        )

        # Mux with audio
        run_ffmpeg(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(no_audio),
             "-i", str(audio_path),
             "-c:v", "copy", "-c:a", "aac", "-shortest",
             str(out)],
            check=True,
        )

    log.info("assemble_clip_video: wrote %s (res=%s)", out, resolution)
    return str(out)


def assemble_clip_video(
    scenes_json: str,
    audio_path: str,
    output_path: str | None = None,
    resolution: str | None = None,
) -> str:
    """JSON-string wrapper around assemble_clip_video_obj. Kept for CLI/external callers.

    scenes_json: JSON list of {clip_paths, duration_s, ...}
    Returns the assembled video path.
    """
    scenes: list[dict] = json.loads(scenes_json)
    return assemble_clip_video_obj(scenes, audio_path, output_path, resolution)


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
            run_ffmpeg(
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
        run_ffmpeg(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
             combined],
            check=True,
        )

    # Loop/trim to exact target duration
    run_ffmpeg(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-stream_loop", "-1", "-i", combined,
         "-t", str(duration_s),
         "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
         "-an", output_path],
        check=True,
    )
