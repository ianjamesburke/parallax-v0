"""Direct pipeline execution from a plan YAML — no agent, no replanning.

`parallax produce --folder <path> --plan <plan.yaml>` reads a pre-planned
scene manifest and runs: generate_image × N → generate_voiceover →
align_scenes → write_manifest → ken_burns_assemble → (optionally)
burn_captions → burn_headline.

Plan YAML schema:
  voice: bella          # ElevenLabs voice name (default: george)
  speed: 1.1            # TTS speed multiplier (default: 1.1)
  model: nano-banana    # image model alias (default: mid)
  resolution: 1080x1920 # output resolution (default: 1080x1920)
  caption_style: bangers
  captions: skip        # omit to enable captions
  headline: THE TITLE   # omit to skip headline
  character_image: .parallax/scratch/ref.png  # relative to --folder; used for reference: true scenes

  scenes:
    - index: 0
      shot_type: character   # or broll
      vo_text: "Words spoken here."
      prompt: "Image generation prompt."
      reference: true        # pass character_image as reference_images
      # reference_images: ["path1.png"]  # explicit paths (overrides reference: true)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from . import tools_video
from .context import current_backend, current_session_id
from .log import get_logger
from .tools import generate_image

log = get_logger("produce")


def run_plan(folder: str | Path, plan_path: str | Path) -> int:
    folder = Path(folder).expanduser().resolve()
    plan_path = Path(plan_path).expanduser().resolve()

    if not folder.is_dir():
        print(f"Error: folder not found: {folder}", file=sys.stderr)
        return 1
    if not plan_path.is_file():
        print(f"Error: plan file not found: {plan_path}", file=sys.stderr)
        return 1

    with plan_path.open() as f:
        plan: dict[str, Any] = yaml.safe_load(f)

    scenes_raw: list[dict[str, Any]] = plan.get("scenes", [])
    if not scenes_raw:
        print("Error: plan has no scenes", file=sys.stderr)
        return 1

    model = plan.get("model", "mid")
    voice = plan.get("voice", "george")
    speed = float(plan.get("speed", 1.1))
    resolution = plan.get("resolution", "1080x1920")
    res_scale = int(resolution.split("x")[0]) / 1080  # scale font sizes to output width
    caption_style = plan.get("caption_style", "anton")
    fontsize = max(12, int(plan.get("fontsize", 55) * res_scale))
    words_per_chunk = int(plan.get("words_per_chunk", 1))
    skip_captions = str(plan.get("captions", "")).lower() == "skip"
    headline = plan.get("headline")
    headline_fontsize = plan.get("headline_fontsize")
    headline_bg = plan.get("headline_bg")
    headline_color = plan.get("headline_color")

    # Resolve character_image relative to folder if it's a relative path
    char_image_raw = plan.get("character_image")
    character_image: str | None = None
    if char_image_raw:
        p = Path(char_image_raw)
        resolved = p if p.is_absolute() else (folder / p)
        if not resolved.is_file():
            print(f"Error: character_image not found: {resolved}", file=sys.stderr)
            return 1
        character_image = str(resolved)

    current_backend.set("produce")
    current_session_id.set(f"produce-{plan_path.stem}")

    # 1. Scan project folder → versioned output_dir
    _log("scan_project_folder")
    scan = json.loads(tools_video.scan_project_folder(str(folder)))
    out_dir = scan["output_dir"]
    version = scan["version"]
    _log(f"output_dir: {out_dir} (v{version})")

    # 2. Generate stills
    _log(f"generating {len(scenes_raw)} stills — model={model}")

    _warn_unknown_scene_fields(scenes_raw)

    scenes: list[dict[str, Any]] = []
    for s in scenes_raw:
        idx = s["index"]
        prompt = s.get("prompt", "")
        vo_text = s.get("vo_text", "")

        # If the plan already has a still_path, reuse it and skip generation
        if "still_path" in s:
            p = Path(s["still_path"])
            still_path = str(p if p.is_absolute() else (folder / p))
            _log(f"  [{idx:02d}] reusing {Path(still_path).name}")
        else:
            # Determine reference images
            if "reference_images" in s:
                refs = [str(Path(r) if Path(r).is_absolute() else folder / r) for r in s["reference_images"]]
            elif s.get("reference") and character_image:
                refs = [character_image]
            else:
                refs = None

            _log(f"  [{idx:02d}] {s.get('shot_type', 'broll')} — {vo_text[:55]}...")
            still_path = generate_image(
                prompt=prompt,
                model=model,
                reference_images=refs,
                out_dir=out_dir,
            )
            _log(f"       → {Path(still_path).name}")
        scene_entry: dict[str, Any] = {
            "index": idx,
            "shot_type": s.get("shot_type", "broll"),
            "vo_text": vo_text,
            "prompt": prompt,
            "still_path": still_path,
        }
        # Carry forward animation fields from plan
        if s.get("animate"):
            scene_entry["animate"] = True
            if s.get("motion_prompt"):
                scene_entry["motion_prompt"] = s["motion_prompt"]
        if s.get("clip_path"):
            cp = Path(s["clip_path"])
            scene_entry["clip_path"] = str(cp if cp.is_absolute() else folder / cp)
        if s.get("zoom_direction"):
            scene_entry["zoom_direction"] = s["zoom_direction"]
        if s.get("zoom_amount") is not None:
            scene_entry["zoom_amount"] = float(s["zoom_amount"])
        scenes.append(scene_entry)

    # 2b. Animate selected scenes (image-to-video)
    animate_model = plan.get("animate_model", "xai/grok-imagine-video/image-to-video")
    animated_count = sum(1 for s in scenes if s.get("animate") and not s.get("clip_path"))
    if animated_count:
        _log(f"animate_scenes — {animated_count} scenes via {animate_model}")
        scenes = json.loads(tools_video.animate_scenes(
            scenes_json=json.dumps(scenes),
            out_dir=out_dir,
            video_model=animate_model,
        ))
        for s in scenes:
            if s.get("clip_path"):
                _log(f"  [{s['index']:02d}] → {Path(s['clip_path']).name}")
    else:
        locked = sum(1 for s in scenes if s.get("clip_path"))
        if locked:
            _log(f"reusing {locked} animated clip(s)")

    # 3. Generate voiceover (skip if audio_path + words_path already set in plan)
    if plan.get("audio_path") and plan.get("words_path"):
        audio_path = str((folder / plan["audio_path"]) if not Path(plan["audio_path"]).is_absolute() else Path(plan["audio_path"]))
        words_path = str((folder / plan["words_path"]) if not Path(plan["words_path"]).is_absolute() else Path(plan["words_path"]))
        words_data = json.loads(Path(words_path).read_text())
        vo_result = {"words": words_data if isinstance(words_data, list) else words_data.get("words", []),
                     "audio_path": audio_path, "words_path": words_path}
        _log(f"reusing voiceover: {Path(audio_path).name}")
    else:
        full_script = " ".join(s.get("vo_text", "") for s in scenes_raw)
        _log(f"generate_voiceover — voice={voice} speed={speed}")
        vo_result = json.loads(tools_video.generate_voiceover(
            text=full_script, voice=voice, speed=speed, out_dir=out_dir,
        ))
        audio_path = vo_result["audio_path"]
        words_path = vo_result["words_path"]
        _log(f"  audio: {Path(audio_path).name}  ({vo_result['total_duration_s']:.1f}s)")

    # 4. Align scenes
    _log("align_scenes")
    aligned_json = tools_video.align_scenes(
        scenes_json=json.dumps(scenes),
        words_json=json.dumps(vo_result["words"]),
    )
    aligned = json.loads(aligned_json)
    for s in aligned:
        _log(f"  [{s['index']:02d}] {s.get('start_s', 0):.2f}s – {s.get('end_s', 0):.2f}s ({s.get('duration_s', 0):.2f}s)")

    # 5. Write manifest
    manifest_path = str(Path(out_dir) / "manifest.yaml")
    _log(f"write_manifest → {manifest_path}")
    tools_video.write_manifest(
        manifest_json=json.dumps({
            "version": version,
            "model": model,
            "voice": voice,
            "speed": speed,
            "resolution": resolution,
            "audio_path": audio_path,
            "words_path": words_path,
            "scenes": aligned,
        }),
        manifest_path=manifest_path,
    )

    # 6. Ken Burns assemble
    draft_path = str(Path(out_dir) / "ken_burns_draft.mp4")
    _log(f"ken_burns_assemble → {draft_path}")
    tools_video.ken_burns_assemble(
        scenes_json=aligned_json,
        audio_path=audio_path,
        output_path=draft_path,
        resolution=resolution,
    )
    current_video = draft_path

    # 7. Burn captions
    if not skip_captions:
        captioned_path = str(Path(out_dir) / "captioned.mp4")
        _log(f"burn_captions → {captioned_path}")
        tools_video.burn_captions(
            video_path=current_video,
            words_json=words_path,
            output_path=captioned_path,
            caption_style=caption_style,
            fontsize=fontsize,
            words_per_chunk=words_per_chunk,
        )
        current_video = captioned_path

    # 8. Burn section titles
    titles_cfg = plan.get("titles", [])
    if titles_cfg:
        scene_map = {s["index"]: s for s in aligned}
        resolved_titles: list[dict] = []
        for t in titles_cfg:
            if "scene" in t:
                sc = scene_map.get(t["scene"])
                if sc:
                    start = sc["start_s"]
                    end = start + float(t.get("duration_s", 2.5))
                    resolved_titles.append({"text": t["text"], "start_s": start, "end_s": end})
            elif "start_s" in t and "end_s" in t:
                resolved_titles.append({"text": t["text"], "start_s": t["start_s"], "end_s": t["end_s"]})
        if resolved_titles:
            titled_path = str(Path(out_dir) / "titled.mp4")
            _log(f"burn_titles → {titled_path}")
            tools_video.burn_titles(
                video_path=current_video,
                titles=resolved_titles,
                output_path=titled_path,
                fontsize=max(12, int(72 * res_scale)),
            )
            current_video = titled_path

    # 10. Burn headline
    if headline:
        final_path = str(Path(out_dir) / "final.mp4")
        _log(f"burn_headline → {final_path}")
        headline_kwargs: dict[str, Any] = {"video_path": current_video, "text": headline, "output_path": final_path}
        if headline_fontsize:
            headline_kwargs["fontsize"] = max(12, int(int(headline_fontsize) * res_scale))
        if headline_bg:
            headline_kwargs["bg_color"] = headline_bg
        if headline_color:
            headline_kwargs["text_color"] = headline_color
        if aligned:
            headline_kwargs["end_time_s"] = aligned[0].get("end_s")
        tools_video.burn_headline(**headline_kwargs)
        current_video = final_path

    # 11. Avatar overlay (optional)
    avatar_cfg = plan.get("avatar")
    if avatar_cfg:
        avatar_img_raw = avatar_cfg.get("image") or plan.get("character_image")
        if not avatar_img_raw:
            print("Error: avatar.image or character_image required for avatar overlay", file=sys.stderr)
            return 1
        av_img_p = Path(avatar_img_raw)
        avatar_img = str(av_img_p if av_img_p.is_absolute() else folder / av_img_p)

        av_scene_indices: list[int] = avatar_cfg.get("scenes", [])
        position = avatar_cfg.get("position", "bottom_left")
        size = float(avatar_cfg.get("size", 0.22))
        chroma_key = avatar_cfg.get("chroma_key")
        aurora_prompt = avatar_cfg.get("aurora_prompt")
        y_offset_pct = avatar_cfg.get("y_offset_pct")
        if y_offset_pct is not None:
            y_offset_pct = float(y_offset_pct)
        full_audio = bool(avatar_cfg.get("full_audio", False))

        # Resolve avatar_track_keyed (pre-keyed, has alpha — use directly, no chroma filter)
        avatar_track_keyed_raw = avatar_cfg.get("avatar_track_keyed")
        avatar_track_keyed: str | None = None
        if avatar_track_keyed_raw:
            p = Path(avatar_track_keyed_raw)
            avatar_track_keyed = str(p if p.is_absolute() else folder / p)
            _log(f"reusing avatar_track_keyed: {Path(avatar_track_keyed).name}")

        # Resolve or generate raw avatar_track
        avatar_track_raw = avatar_cfg.get("avatar_track")
        if avatar_track_raw:
            av_track_p = Path(avatar_track_raw)
            avatar_track = str(av_track_p if av_track_p.is_absolute() else folder / av_track_p)
            track_start_s = float(avatar_cfg.get("track_start_s", 0.0))
            _log(f"reusing avatar_track: {Path(avatar_track).name} (starts at {track_start_s:.2f}s)")
        else:
            mode_desc = "full-audio" if full_audio else f"{len(av_scene_indices)} scenes"
            _log(f"generate_avatar_clips — {mode_desc}")
            av_result = json.loads(tools_video.generate_avatar_clips(
                scenes_json=aligned_json,
                audio_path=audio_path,
                character_image=avatar_img,
                avatar_scene_indices=av_scene_indices,
                out_dir=out_dir,
                aurora_prompt=aurora_prompt,
                full_audio=full_audio,
            ))
            avatar_track = av_result["avatar_track"]
            track_start_s = av_result["track_start_s"]
            _log(f"  track: {Path(avatar_track).name} starts at {track_start_s:.2f}s")
            out_ver = Path(out_dir).name
            _log(
                f"  → lock in plan to skip future Aurora calls:\n"
                f"    avatar:\n"
                f"      avatar_track: .parallax/output/{out_ver}/{Path(avatar_track).name}\n"
                f"      track_start_s: {track_start_s:.1f}"
            )

        # Pre-key: if no keyed version exists yet and chroma_key is set, key it now and save
        if not avatar_track_keyed and chroma_key:
            keyed_path = str(Path(out_dir) / "avatar_track_keyed.mov")
            _log(f"key_avatar_track → {keyed_path}")
            avatar_track_keyed = tools_video.key_avatar_track(
                avatar_track=avatar_track,
                chroma_key=chroma_key,
                output_path=keyed_path,
            )
            out_ver = Path(out_dir).name
            _log(
                f"  → lock in plan to skip future chroma-key calls:\n"
                f"    avatar:\n"
                f"      avatar_track_keyed: .parallax/output/{out_ver}/avatar_track_keyed.mov"
            )

        # Composite — use pre-keyed track if available (no chroma filter needed)
        composite_track = avatar_track_keyed or avatar_track
        avatar_out = str(Path(out_dir) / "avatar.mp4")
        _log(f"burn_avatar ({position}) → {avatar_out}")
        kwargs: dict[str, Any] = dict(
            video_path=current_video,
            avatar_track=composite_track,
            track_start_s=track_start_s,
            output_path=avatar_out,
            position=position,
            size=size,
        )
        if not avatar_track_keyed and chroma_key:
            kwargs["chroma_key"] = chroma_key
        if y_offset_pct is not None:
            kwargs["y_offset_pct"] = y_offset_pct
        tools_video.burn_avatar(**kwargs)
        current_video = avatar_out

    print(f"\n✓ {current_video}", flush=True)
    subprocess.run(["open", current_video])
    return 0


def _log(msg: str) -> None:
    print(f"==> {msg}", flush=True)


_KNOWN_SCENE_FIELDS = {
    "index", "shot_type", "vo_text", "prompt",
    "still_path", "reference", "reference_images",
    "animate", "motion_prompt", "clip_path",
    "zoom_direction", "zoom_amount",
}


def _warn_unknown_scene_fields(scenes_raw: list[dict[str, Any]]) -> None:
    for s in scenes_raw:
        unknown = set(s.keys()) - _KNOWN_SCENE_FIELDS
        if unknown:
            print(
                f"  [WARNING] scene {s.get('index', '?')}: unrecognized fields "
                f"(will be silently ignored): {', '.join(sorted(unknown))}",
                flush=True,
            )


def test_scene(folder: str | Path, plan_path: str | Path, scene_index: int) -> int:
    """Apply the video filter for one scene and open the result — no full pipeline."""
    import subprocess

    folder = Path(folder).expanduser().resolve()
    plan_path = Path(plan_path).expanduser().resolve()

    if not plan_path.is_file():
        print(f"Error: plan not found: {plan_path}", file=sys.stderr)
        return 1

    with plan_path.open() as f:
        import yaml
        plan: dict[str, Any] = yaml.safe_load(f)

    resolution = plan.get("resolution", "1080x1920")
    scenes_raw = plan.get("scenes", [])
    scene = next((s for s in scenes_raw if s.get("index") == scene_index), None)
    if scene is None:
        print(f"Error: scene index {scene_index} not found in plan", file=sys.stderr)
        return 1

    clip_raw = scene.get("clip_path")
    still_raw = scene.get("still_path")
    zoom_dir = scene.get("zoom_direction")
    zoom_amount = float(scene.get("zoom_amount", 1.25))

    out_path = f"/tmp/parallax_test_scene_{scene_index:02d}.mp4"

    if clip_raw:
        src = Path(clip_raw) if Path(clip_raw).is_absolute() else folder / clip_raw
        if not src.exists():
            print(f"Error: clip_path not found: {src}", file=sys.stderr)
            return 1
        # Probe duration
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=duration", "-of", "csv=p=0", str(src)],
            capture_output=True, text=True,
        )
        duration = float(probe.stdout.strip() or "5.0")
        w, h = resolution.split("x")
        from .tools_video import _zoom_filter, _get_ffmpeg
        ffmpeg = _get_ffmpeg()
        vf = _zoom_filter(zoom_dir, zoom_amount, duration, w, h)
        subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(src), "-t", str(duration),
             "-vf", vf,
             "-an", "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
             out_path],
            check=True,
        )
    elif still_raw:
        src = Path(still_raw) if Path(still_raw).is_absolute() else folder / still_raw
        if not src.exists():
            print(f"Error: still_path not found: {src}", file=sys.stderr)
            return 1
        duration = 4.0
        from .tools_video import _make_kb_clip
        _make_kb_clip(str(src), duration, out_path, resolution=resolution,
                      scene_index=scene_index, zoom_direction=zoom_dir, zoom_amount=zoom_amount)
    else:
        print(f"Error: scene {scene_index} has no clip_path or still_path", file=sys.stderr)
        return 1

    print(f"✓ {out_path}", flush=True)
    subprocess.run(["open", out_path])
    return 0
