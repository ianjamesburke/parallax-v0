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

from .ffmpeg_utils import _get_ffmpeg, parse_resolution, pipe_rawvideo_frames, run_ffmpeg
from .log import get_logger
from .shim import is_test_mode, output_dir

log = get_logger(__name__)


def _norm_word(w: str) -> str:
    """Strip punctuation and lowercase for TTS↔plan word comparison."""
    return re.sub(r'[^\w]', '', w).lower()


def _find_scene_end(words: list[dict], cursor: int, plan_words: list[str], window_cap: int | None = None) -> int | None:
    """Find the TTS word index where a scene ends by matching its last content word.

    Searches forward from *cursor* in a window proportional to the expected
    word count.  Falls back to second-to-last word if the last one isn't found
    (handles TTS mangling proper nouns like "Shilajit" → "Shiligid").

    window_cap: hard upper bound on the search window (prevents cross-scene
    word stealing when adjacent scenes share vocabulary). Computed from
    proportional word distribution by the caller.
    """
    content_words = [w for w in plan_words if _norm_word(w)]
    if not content_words:
        return None

    expected = len(content_words)
    window_end = min(cursor + expected * 2 + 5, len(words))
    if window_cap is not None:
        window_end = min(window_end, window_cap)

    for target_word in reversed(content_words[-2:]):
        target = _norm_word(target_word)
        for i in range(window_end - 1, max(cursor - 1, -1), -1):
            if _norm_word(words[i]["word"]) == target:
                if target_word is content_words[-1]:
                    return i
                return min(i + 1, len(words) - 1)
    return None


def _cross_check_transcript(scenes: list[dict], words: list[dict]) -> None:
    """Compare TTS words against plan vo_text; fix substitutions in place.

    Uses difflib to align plan words against TTS words per scene. For
    same-count replacements (1:1, 2:2, etc.) where the concatenated
    normalized forms differ, patches the TTS word text to match the plan
    so captions display the script text, not TTS hallucinations.
    Merges/splits (different word counts) are logged but left alone since
    the timing doesn't map.
    """
    from difflib import SequenceMatcher

    cursor = 0
    for scene in scenes:
        vo_text = scene.get("vo_text", "").strip()
        if not vo_text:
            continue
        plan_tokens = re.sub(r'\[[^\]]*\]', '', vo_text).split()
        plan_content = [(i, w) for i, w in enumerate(plan_tokens) if _norm_word(w)]
        plan_normed = [_norm_word(w) for _, w in plan_content]

        start_s = scene.get("start_s")
        end_s = scene.get("end_s")
        if start_s is None or end_s is None:
            continue

        scene_tts: list[dict] = []
        while cursor < len(words) and words[cursor]["end"] <= end_s + 0.01:
            scene_tts.append(words[cursor])
            cursor += 1

        tts_normed = [_norm_word(w["word"]) for w in scene_tts]

        sm = SequenceMatcher(None, plan_normed, tts_normed)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "replace":
                continue
            plan_chunk = "".join(plan_normed[i1:i2])
            tts_chunk = "".join(tts_normed[j1:j2])
            if plan_chunk == tts_chunk:
                continue

            plan_span = [plan_tokens[idx] for idx, _ in plan_content[i1:i2]]
            tts_raw = " ".join(w["word"] for w in scene_tts[j1:j2])
            t = scene_tts[j1]["start"] if j1 < len(scene_tts) else 0.0

            if (i2 - i1) == (j2 - j1):
                for k, tts_word in enumerate(scene_tts[j1:j2]):
                    tts_word["word"] = plan_span[k]
                log.info(
                    "Scene %s caption fix: %r → %r (at %.2fs)",
                    scene.get("index", "?"), tts_raw, " ".join(plan_span), t,
                )
            else:
                log.warning(
                    "Scene %s transcript mismatch (unfixable): plan=%r tts=%r (at %.2fs)",
                    scene.get("index", "?"), " ".join(plan_span), tts_raw, t,
                )


def align_scenes_obj(scenes: list[dict], words_payload: list[dict] | dict) -> list[dict]:
    """Assign start_s/end_s/duration_s so scenes form a contiguous cover of the audio.

    scenes: list of {index, vo_text, ...}
    words_payload: either [{word, start, end}, ...] or {"words": [...], "total_duration_s": float}.
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

    Returns updated scenes list.
    """
    payload = words_payload
    words: list[dict] = payload if isinstance(payload, list) else payload.get("words", [])

    # Pre-compute proportional word-count caps so _find_scene_end can't steal
    # words from an adjacent scene that shares vocabulary (Bug 2 fix).
    # Each scene gets a hard window_cap = proportional TTS word index + 2-word slack.
    scene_word_counts = []
    for scene in scenes:
        vo = scene.get("vo_text", "").strip()
        pw = re.sub(r'\[[^\]]*\]', '', vo).split() if vo else []
        scene_word_counts.append(len([w for w in pw if _norm_word(w)]))
    total_plan_words = sum(scene_word_counts)
    window_caps: list[int] = []
    if total_plan_words > 0:
        accum = 0
        for count in scene_word_counts:
            accum += count
            cap = round(accum / total_plan_words * len(words)) + 2
            window_caps.append(min(cap, len(words)))
    else:
        window_caps = [len(words)] * len(scenes)

    # Approximate avg word duration for short-scene warning.
    avg_word_dur = (float(words[-1]["end"]) / len(words)) if words else 1.0

    cursor = 0
    for scene, window_cap in zip(scenes, window_caps):
        vo_text = scene.get("vo_text", "").strip()
        if not vo_text:
            continue
        plan_words = re.sub(r'\[[^\]]*\]', '', vo_text).split()
        content_count = len([w for w in plan_words if _norm_word(w)])

        end_idx = _find_scene_end(words, cursor, plan_words, window_cap=window_cap)
        if end_idx is None:
            if cursor + content_count > len(words):
                log.warning("Scene %s: no match and only %d words remain; extending to end",
                            scene.get("index", "?"), len(words) - cursor)
                content_count = len(words) - cursor
            if content_count == 0:
                continue
            end_idx = cursor + content_count - 1

        word_start_s = words[cursor]["start"]
        scene["start_s"] = round(word_start_s, 3)
        scene["end_s"] = round(words[end_idx]["end"], 3)

        # Short-scene sanity warning (Bug 2 symptom detection).
        detected_dur = scene["end_s"] - scene["start_s"]
        expected_min_dur = content_count * avg_word_dur * 0.5
        if content_count > 1 and detected_dur < expected_min_dur:
            log.warning(
                "Scene %s: detected duration %.2fs may be too short for %d words "
                "(expected ~%.2fs+); check for shared-vocabulary adjacent-scene misalignment",
                scene.get("index", "?"), detected_dur, content_count, expected_min_dur,
            )

        # Bug 1 fix: when duration_s is pinned on this scene, advance the
        # cursor to the first word at/after the pinned end so the next scene's
        # word search starts at the correct audio position.
        pinned_dur = scene.get("duration_s")
        if pinned_dur is not None:
            pinned_end_s = word_start_s + float(pinned_dur)
            new_cursor = len(words)
            for j in range(end_idx + 1, len(words)):
                if words[j]["start"] >= pinned_end_s - 0.01:
                    new_cursor = j
                    break
            cursor = new_cursor
        else:
            cursor = end_idx + 1

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
            s["duration_s"] = round(s["end_s"] - s["start_s"], 2)

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
    _cross_check_transcript(scenes, words)
    return scenes


def align_scenes(scenes_json: str, words_json: str) -> str:
    """JSON-string wrapper around align_scenes_obj for callers that use serialized data.

    scenes_json: JSON list of {index, vo_text, ...}
    words_json: JSON of either [{word, start, end}, ...] or {"words": [...], "total_duration_s": float}.
    Returns updated scenes as a JSON string.
    """
    scenes: list[dict] = json.loads(scenes_json)
    payload = json.loads(words_json)
    result = align_scenes_obj(scenes, payload)
    return json.dumps(result)


_SUPPORTED_XFADE_TRANSITIONS = frozenset({
    "fade", "fadeblack", "fadewhite", "dissolve", "pixelize",
    "wipeleft", "wiperight", "wipeup", "wipedown",
    "hlslice", "hrslice", "vuslice", "vdslice",
})


def _xfade_filter_complex(
    clip_paths: list[str],
    transitions: list[str | None],
    durations: list[float],
    transition_duration_s: list[float],
) -> str:
    """Build an ffmpeg filter_complex xfade chain for N clips.

    clips: already-rendered scene clips (each normalized to same codec/fps/res).
    transitions[i]: xfade transition name for clip i's ENTRY (index 0 = no-op).
    durations[i]: duration in seconds of clip i.
    transition_duration_s[i]: xfade duration for the transition at clip i's entry.

    Returns a filter_complex string that produces [vout] containing the full
    xfade-chained video.
    """
    n = len(clip_paths)
    if n < 2:
        raise ValueError("xfade requires at least 2 clips")

    parts: list[str] = []
    # Cumulative offset tracker: accounts for each overlap already consumed.
    cumulative_dur = durations[0]
    prev_label = "[0:v]"

    for i in range(1, n):
        trans = transitions[i] or "fade"
        if trans not in _SUPPORTED_XFADE_TRANSITIONS:
            raise ValueError(
                f"Unknown xfade transition {trans!r}. "
                f"Supported: {sorted(_SUPPORTED_XFADE_TRANSITIONS)}"
            )
        tdur = transition_duration_s[i]
        # Clamp transition duration to half the shorter adjacent clip
        tdur = min(tdur, durations[i - 1] * 0.5, durations[i] * 0.5)
        offset = round(cumulative_dur - tdur, 6)
        out_label = f"[v{i:02d}]" if i < n - 1 else "[vout]"
        parts.append(
            f"{prev_label}[{i}:v]xfade=transition={trans}"
            f":duration={tdur}:offset={offset}{out_label}"
        )
        prev_label = out_label
        cumulative_dur += durations[i] - tdur

    return ";".join(parts)


def _resolve_auto_trim(scenes: list[dict]) -> list[dict]:
    """Resolve clip_trim_start_s="auto" to a concrete float.

    For each scene with "auto", the trim start = previous scene's
    clip_trim_start_s + previous scene's duration_s. Both scenes must
    share the same clip_path; raises RuntimeError otherwise.
    """
    result: list[dict] = []
    for i, scene in enumerate(scenes):
        scene = dict(scene)
        if scene.get("clip_trim_start_s") == "auto":
            if i == 0:
                raise RuntimeError(
                    f"Scene {scene.get('index', i)}: clip_trim_start_s='auto' "
                    "cannot be used on the first scene"
                )
            prev = result[i - 1]
            if prev.get("clip_path") != scene.get("clip_path"):
                raise RuntimeError(
                    f"Scene {scene.get('index', i)}: clip_trim_start_s='auto' "
                    f"requires same clip_path as previous scene "
                    f"(prev={prev.get('clip_path')!r}, this={scene.get('clip_path')!r})"
                )
            prev_start = float(prev.get("clip_trim_start_s") or 0.0)
            prev_dur = float(prev.get("duration_s") or 0.0)
            scene["clip_trim_start_s"] = round(prev_start + prev_dur, 6)
        result.append(scene)
    return result


def ken_burns_assemble(
    scenes_json: str,
    audio_path: str | None,
    output_path: str | None = None,
    resolution: str = "1080x1920",
    transitions: list[str | None] | None = None,
    transition_duration_s: list[float] | None = None,
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
    scenes = _resolve_auto_trim(scenes)

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
                trim_start = float(scene.get("clip_trim_start_s") or 0.0)
                trim_end = scene.get("clip_trim_end_s")
                if trim_start > 0.0 or trim_end is not None:
                    trimmed_path = str(Path(tmp_dir) / f"trimmed_{i:04d}.mp4")
                    trim_cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                                "-ss", str(trim_start), "-i", pre_animated]
                    if trim_end is not None:
                        trim_cmd += ["-t", str(float(trim_end) - trim_start)]
                    trim_cmd += ["-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", "-an", trimmed_path]
                    run_ffmpeg(trim_cmd, check=True)
                    pre_animated = trimmed_path
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

        # Resolve per-clip durations (needed for xfade offset math)
        clip_durations: list[float] = []
        for cp in clip_paths:
            probe = run_ffmpeg(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", cp],
                capture_output=True, text=True,
            )
            clip_durations.append(float(probe.stdout.strip() or "0"))

        no_audio = Path(tmp_dir) / "no_audio.mp4"
        use_xfade = (
            transitions is not None
            and len(clip_paths) >= 2
            and any(t is not None for t in transitions[1:])
        )

        if use_xfade:
            assert transitions is not None
            assert transition_duration_s is not None
            # Pad lists to clip count (scene 0 transition is always None/no-op)
            padded_transitions = list(transitions) + [None] * (len(clip_paths) - len(transitions))
            padded_durations = list(transition_duration_s) + [0.5] * (len(clip_paths) - len(transition_duration_s))
            # Clamp transition durations to half the clip
            clamped_dur = [
                min(d, clip_durations[i] * 0.5)
                for i, d in enumerate(padded_durations)
            ]
            filter_complex = _xfade_filter_complex(
                clip_paths, padded_transitions, clip_durations, clamped_dur,
            )
            cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
            for cp in clip_paths:
                cmd += ["-i", cp]
            cmd += [
                "-filter_complex", filter_complex,
                "-map", "[vout]",
                "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                "-an", str(no_audio),
            ]
            run_ffmpeg(cmd, check=True)
        else:
            # Hard-cut concat — stream copy is safe (all clips same codec/fps/res)
            list_file = Path(tmp_dir) / "clips.txt"
            list_file.write_text("\n".join(f"file '{p}'" for p in clip_paths))
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
                 "-c:v", "copy", "-c:a", "aac",
                 str(out)],
                check=True,
            )
        else:
            import shutil as _shutil
            _shutil.copy2(no_audio, out)

    log.info("ken_burns_assemble: wrote %s", out)
    return str(out)


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
        frame_bytes = img.tobytes()
        pipe_rawvideo_frames(
            output_path,
            width=out_w, height=out_h, fps=fps, total_frames=total_frames,
            frames=(frame_bytes for _ in range(total_frames)),
            source_label=image_path,
        )
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

    def _frames():
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
            yield frame.tobytes()

    pipe_rawvideo_frames(
        output_path,
        width=out_w, height=out_h, fps=fps, total_frames=total_frames,
        frames=_frames(),
        source_label=image_path,
    )


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
             "-c:v", "copy", "-c:a", "aac",
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


# --------------------------------------------------------------------------
# Object-oriented wrappers (avoid JSON-string roundtrips for internal callers)
# --------------------------------------------------------------------------

def ken_burns_assemble_obj(
    scenes: list[dict],
    audio_path: str | None,
    output_path: str | None = None,
    resolution: str = "1080x1920",
    transitions: list[str | None] | None = None,
    transition_duration_s: list[float] | None = None,
) -> str:
    """Object-level entry point for ken_burns_assemble (avoids JSON string serialization).

    Delegates to ken_burns_assemble with pre-serialized scenes.
    """
    return ken_burns_assemble(
        scenes_json=json.dumps(scenes),
        audio_path=audio_path,
        output_path=output_path,
        resolution=resolution,
        transitions=transitions,
        transition_duration_s=transition_duration_s,
    )


def assemble_clip_video_obj(
    scenes: list[dict],
    audio_path: str,
    output_path: str | None = None,
    resolution: str | None = None,
) -> str:
    """Object-level entry point for assemble_clip_video (avoids JSON string serialization).

    Delegates to assemble_clip_video with pre-serialized scenes.
    """
    return assemble_clip_video(
        scenes_json=json.dumps(scenes),
        audio_path=audio_path,
        output_path=output_path,
        resolution=resolution,
    )
