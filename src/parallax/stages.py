"""Pipeline stages — `(plan, settings) -> updated_plan`.

Each stage owns one logical step of `produce`. Stages share state
through:
  - the `plan` dict (locked asset paths: still_path, clip_path,
    audio_path, words_path) — the durable artifact
  - the `plan["_runtime"]` sub-dict — in-flight context that is NOT
    saved to disk (out_dir, video_dir, stills_dir, audio_dir, scenes,
    aligned, current_video, version, run_id, manifest_path)

Stages mutate disk (write images / mp4 / yaml) and return the plan.
The orchestrator (`run_plan`) walks the stage list in order; verify-
suite installs its own `settings.events` callback to capture per-stage
activity for assertions.
"""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path
from typing import Any

from .assembly import align_scenes, ken_burns_assemble
from .avatar import burn_avatar, generate_avatar_clips, key_avatar_track
from .captions import burn_captions
from .headline import burn_headline, burn_titles
from .manifest import write_manifest
from .project import animate_scenes, scan_project_folder
from .settings import Settings
from .voiceover import generate_voiceover


def _log(settings: Settings, msg: str) -> None:
    """Emit a human-readable progress line via the injected emitter."""
    settings.events("log", {"msg": msg})


def _runtime(plan: dict[str, Any]) -> dict[str, Any]:
    return plan.setdefault("_runtime", {})


def _lock_field_in_plan(plan_path: Path, plan: dict, scene_idx: int, field: str, value: str, folder: Path) -> None:
    """Write an asset path back into the plan YAML so the scene is locked on future runs."""
    import yaml
    try:
        try:
            locked_path = str(Path(value).relative_to(folder))
        except ValueError:
            locked_path = value
        for scene in plan.get("scenes", []):
            if scene.get("index") == scene_idx:
                scene[field] = locked_path
                break
        # Snapshot without `_runtime` blackboard
        snapshot = {k: v for k, v in plan.items() if k != "_runtime"}
        with plan_path.open("w") as f:
            yaml.dump(snapshot, f, default_flow_style=False, allow_unicode=True, sort_keys=False, width=10000)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Stage callables
# --------------------------------------------------------------------------

def stage_scan(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Scan the project folder, derive the versioned output dir.

    Breaks if: `out_dir` / `version` / `convention_name` are not present
    on `plan["_runtime"]` after this stage runs.
    """
    _log(settings, "scan_project_folder")
    scan = json.loads(scan_project_folder(str(settings.folder)))
    rt = _runtime(plan)
    rt["out_dir"] = scan["output_dir"]
    rt["version"] = scan["version"]
    rt["convention_name"] = f"{settings.folder.name}-v{scan['version']}.mp4"
    rt["stills_dir"] = str(Path(rt["out_dir"]) / "stills")
    rt["audio_dir"] = str(Path(rt["out_dir"]) / "audio")
    rt["video_dir"] = str(Path(rt["out_dir"]) / "video")
    _log(settings, f"output_dir: {rt['out_dir']} (v{rt['version']})")
    return plan


def stage_stills(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Generate or reuse stills for every scene; lock new ones in the plan.

    Breaks if: any scene without a pre-existing still ends up missing a
    `still_path` on its in-flight scene entry, or normalize_aspect is
    skipped on freshly-generated PNGs.
    """
    from .tools import generate_image

    rt = _runtime(plan)
    scenes_raw: list[dict[str, Any]] = plan.get("scenes", [])
    _log(settings, f"generating {len(scenes_raw)} stills — model={settings.model}")
    _warn_unknown_scene_fields(scenes_raw)
    Path(rt["stills_dir"]).mkdir(exist_ok=True)

    scenes: list[dict[str, Any]] = []
    for s in scenes_raw:
        idx = s["index"]
        prompt = s.get("prompt", "")
        vo_text = s.get("vo_text", "")

        if "still_path" in s:
            p = Path(s["still_path"])
            still_path = str(p if p.is_absolute() else (settings.folder / p))
            _log(settings, f"  [{idx:02d}] reusing {Path(still_path).name}")
        else:
            if "reference_images" in s:
                refs = [str(Path(r) if Path(r).is_absolute() else settings.folder / r) for r in s["reference_images"]]
            else:
                # Default: pass EVERY image in media/ as a reference. The
                # planner agent has historically been sloppy about picking
                # the right `character_image` per scene (verified live: it
                # picked the product photo as character for a stylized
                # scene, then the broll scenes attached no reference at
                # all and produced photoreal output). Until the agent's
                # reasoning is reliable, force-attach all media/ files so
                # Gemini always has the character + product visible.
                # `reference_images` on the scene still wins as an explicit
                # opt-in/opt-out.
                media_dir = settings.folder / "media"
                if media_dir.is_dir():
                    # Skip cropped/normalized derivatives — those have an
                    # `_a<W>x<H>` or `_n<W>x<H>` suffix added by stills
                    # post-processing. Only the original extracted images
                    # (image1.png, image2.png, etc.) are real references.
                    import re as _re
                    derivative_pat = _re.compile(r"_[an]\d+x\d+$")
                    candidates = sorted(
                        p for p in media_dir.iterdir()
                        if p.is_file()
                        and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
                        and not derivative_pat.search(p.stem)
                    )
                    # Cap at 4 — character + product + a couple alts is
                    # plenty; Gemini's max_refs is 8 but more refs degrade
                    # the per-image attention each one gets.
                    refs = [str(p) for p in candidates[:4]] or None
                elif s.get("reference") and settings.character_image:
                    refs = [settings.character_image]
                elif settings.stills_only and settings.character_image:
                    refs = [settings.character_image]
                else:
                    refs = None

            _log(settings, f"  [{idx:02d}] {s.get('shot_type', 'broll')} — {vo_text[:55]}...")
            from .stills import (
                AspectMismatchError,
                check_aspect,
                normalize_aspect,
            )

            # Two attempts max: if the model returns a wrong-aspect image on
            # first try, regenerate once with a sterner textual prompt prefix.
            # If it's still wrong, raise — silent center-crop is forbidden
            # because it discards subject content for non-centered compositions.
            attempts = 2
            raw_still_path = None
            last_err: Exception | None = None
            for attempt in range(1, attempts + 1):
                stern_prefix = "" if attempt == 1 else (
                    "CRITICAL: Output MUST be 9:16 vertical portrait orientation, "
                    "taller than wide. Previous attempt returned the wrong aspect "
                    "and was REJECTED. Frame the subject for portrait. "
                )
                raw_still_path = generate_image(
                    prompt=stern_prefix + prompt,
                    model=settings.model,
                    reference_images=refs,
                    out_dir=rt["stills_dir"],
                )
                check = check_aspect(raw_still_path, settings.resolution)
                if check.within_tolerance:
                    break
                _log(settings,
                     f"       ✗ attempt {attempt}/{attempts}: {Path(raw_still_path).name} "
                     f"is {check.src_w}x{check.src_h} "
                     f"({check.mismatch_pct*100:.1f}% off target {settings.resolution}). "
                     f"{'Retrying with sterner prompt.' if attempt < attempts else 'GIVING UP.'}")
                last_err = AspectMismatchError(
                    f"scene {idx}: model {settings.model!r} returned wrong-aspect "
                    f"image {check.src_w}x{check.src_h} ({check.mismatch_pct*100:.1f}% off). "
                    f"Already retried {attempt - 1}x with sterner prompts. "
                    f"This run is aborting — switch to a different image model or "
                    f"rewrite the prompt to mention vertical/portrait framing explicitly."
                )

            if raw_still_path is None or last_err is not None and not check_aspect(raw_still_path, settings.resolution).within_tolerance:
                raise last_err if last_err else RuntimeError("stage_stills: no still produced")

            normalized = normalize_aspect(raw_still_path, settings.resolution)
            still_path = str(normalized)
            if normalized != Path(raw_still_path):
                _log(settings, f"       → {Path(still_path).name}  (micro-trimmed from {Path(raw_still_path).name})")
            else:
                _log(settings, f"       → {Path(still_path).name}")
            _lock_field_in_plan(settings.plan_path, plan, idx, "still_path", still_path, settings.folder)

        scene_entry: dict[str, Any] = {
            "index": idx,
            "shot_type": s.get("shot_type", "broll"),
            "vo_text": vo_text,
            "prompt": prompt,
            "still_path": still_path,
        }
        if s.get("animate"):
            scene_entry["animate"] = True
            if s.get("animate_model"):
                scene_entry["animate_model"] = s["animate_model"]
            if s.get("motion_prompt"):
                scene_entry["motion_prompt"] = s["motion_prompt"]
            if s.get("animate_resolution"):
                scene_entry["animate_resolution"] = s["animate_resolution"]
            if s.get("end_frame_path"):
                ep = Path(s["end_frame_path"])
                scene_entry["end_frame_path"] = str(ep if ep.is_absolute() else settings.folder / ep)
        if s.get("clip_path"):
            cp = Path(s["clip_path"])
            scene_entry["clip_path"] = str(cp if cp.is_absolute() else settings.folder / cp)
        if s.get("zoom_direction"):
            scene_entry["zoom_direction"] = s["zoom_direction"]
        if s.get("zoom_amount") is not None:
            scene_entry["zoom_amount"] = float(s["zoom_amount"])
        scenes.append(scene_entry)

    rt["scenes"] = scenes
    return plan


def stage_animate(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Run image-to-video for any scene flagged `animate: true` without a clip.

    Routes to OpenRouter when `animate_model` is a registered video alias
    (e.g. 'kling', 'seedance', 'wan', 'veo', 'sora'); otherwise delegates to
    project.animate_scenes which uses the FAL grok-imagine-video model.

    OpenRouter routing supports `end_frame_path` on scenes for last-frame
    conditioning (model interpolates between start and end image), and
    `video_references` for character/style consistency in text-to-video scenes
    (passed as input_references; ignored when a still drives frame_images).

    Model note: Seedance 2.0 (alias: seedance) is the recommended model for
    reference-to-video — it has the strongest documented input_references
    character consistency support per OpenRouter docs.

    Breaks if: animated scenes are not augmented with `clip_path` after
    this stage, or pre-locked clips aren't reported as reused.
    """
    from .pricing import VIDEO_MODELS
    from . import openrouter as _openrouter
    import time as _time

    rt = _runtime(plan)
    scenes = rt["scenes"]
    plan_animate_model = plan.get("animate_model", "xai/grok-imagine-video/image-to-video")
    animate_resolution = "720p" if plan.get("hq") else "480p"

    animated_count = sum(1 for s in scenes if s.get("animate") and not s.get("clip_path"))
    if animated_count:
        Path(rt["video_dir"]).mkdir(exist_ok=True)
        _log(settings, f"animate_scenes — {animated_count} scene(s) to animate")

        # FAL scenes collected for batching (grouped by model after OpenRouter pass).
        fal_pending: list[tuple[dict[str, Any], str]] = []

        for s in scenes:
            if not s.get("animate") or s.get("clip_path"):
                continue

            # Per-scene model override wins over plan-level default.
            scene_model = s.get("animate_model") or plan_animate_model
            idx = s["index"]
            still = s.get("still_path")
            if not still or not Path(still).exists():
                _log(settings, f"  [{idx:02d}] WARNING: no valid still, skipping animation")
                continue

            if scene_model in VIDEO_MODELS:
                # OpenRouter path — handles each scene individually (supports end_frame_path).
                spec = VIDEO_MODELS[scene_model]
                _portrait = spec.portrait_args or {}
                aspect_ratio: str | None = str(_portrait["aspect_ratio"]) if "aspect_ratio" in _portrait else None
                motion_prompt = s.get("motion_prompt") or s.get("prompt") or (
                    "Subtle cinematic motion, gentle camera drift. Keep the scene stable and beautiful."
                )
                end_frame = s.get("end_frame_path")
                if end_frame and not Path(end_frame).exists():
                    _log(settings, f"  [{idx:02d}] WARNING: end_frame_path not found ({end_frame}), ignoring")
                    end_frame = None

                # video_references: resolve paths relative to project folder.
                # Only effective for text-to-video (no still); when image_path is
                # set, frame_images takes precedence and input_references is ignored.
                video_refs_raw = s.get("video_references")
                input_references: list[Path] | None = None
                if video_refs_raw:
                    input_references = [
                        (Path(r) if Path(r).is_absolute() else settings.folder / r)
                        for r in video_refs_raw
                    ]

                _log(settings, f"  [{idx:02d}] openrouter {scene_model}"
                     + (f" + end_frame" if end_frame else "")
                     + (f" + {len(input_references)} ref(s)" if input_references else ""))
                clip_path = _openrouter.generate_video(
                    prompt=motion_prompt,
                    alias=scene_model,
                    image_path=Path(still),
                    end_image_path=Path(end_frame) if end_frame else None,
                    input_references=input_references,
                    out_dir=Path(rt["video_dir"]),
                    aspect_ratio=aspect_ratio,
                )
                s["clip_path"] = str(clip_path)
            else:
                # FAL/Grok path — batch after OpenRouter pass.
                fal_pending.append((s, scene_model))

        # Process FAL scenes in batches grouped by model.
        if fal_pending:
            from itertools import groupby as _groupby
            fal_pending.sort(key=lambda x: x[1])
            for fal_model, grp in _groupby(fal_pending, key=lambda x: x[1]):
                batch = [item[0] for item in grp]
                _log(settings, f"  FAL batch: {len(batch)} scene(s) via {fal_model} @ {animate_resolution}")
                updated = json.loads(animate_scenes(
                    scenes_json=json.dumps(batch),
                    out_dir=rt["video_dir"],
                    video_model=fal_model,
                    resolution=animate_resolution,
                ))
                updated_map = {u["index"]: u for u in updated}
                for s in scenes:
                    if s["index"] in updated_map and updated_map[s["index"]].get("clip_path"):
                        s["clip_path"] = updated_map[s["index"]]["clip_path"]

        for s in scenes:
            if s.get("clip_path"):
                _log(settings, f"  [{s['index']:02d}] → {Path(s['clip_path']).name}")
                _lock_field_in_plan(settings.plan_path, plan, s["index"], "clip_path", s["clip_path"], settings.folder)
        rt["scenes"] = scenes
    else:
        locked = sum(1 for s in scenes if s.get("clip_path"))
        if locked:
            _log(settings, f"reusing {locked} animated clip(s)")
    return plan


def stage_voiceover(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Synthesize or reuse the voiceover; produce per-word alignment.

    Breaks if: `audio_path`, `words_path`, and `vo_result` are not on
    `plan["_runtime"]` after this stage.
    """
    rt = _runtime(plan)
    Path(rt["audio_dir"]).mkdir(exist_ok=True)

    if plan.get("audio_path") and plan.get("words_path"):
        audio_path = str((settings.folder / plan["audio_path"]) if not Path(plan["audio_path"]).is_absolute() else Path(plan["audio_path"]))
        words_path = str((settings.folder / plan["words_path"]) if not Path(plan["words_path"]).is_absolute() else Path(plan["words_path"]))
        words_data = json.loads(Path(words_path).read_text())
        words_list = words_data if isinstance(words_data, list) else words_data.get("words", [])
        if isinstance(words_data, dict) and words_data.get("total_duration_s") is not None:
            total_dur = float(words_data["total_duration_s"])
        else:
            import wave as _wave
            with _wave.open(audio_path, "rb") as _w:
                total_dur = _w.getnframes() / float(_w.getframerate())
        vo_result = {"words": words_list, "audio_path": audio_path,
                     "words_path": words_path, "total_duration_s": total_dur}
        _log(settings, f"reusing voiceover: {Path(audio_path).name}")
    elif plan.get("audio_path") and not plan.get("words_path"):
        from . import forced_align
        audio_path = str((settings.folder / plan["audio_path"]) if not Path(plan["audio_path"]).is_absolute() else Path(plan["audio_path"]))
        words_path = str(Path(audio_path).with_name("vo_words.json"))
        _log(settings, f"forced_align → {Path(audio_path).name} (whisperx)")
        words = forced_align.align_words(audio_path)
        import wave as _wave
        with _wave.open(audio_path, "rb") as _w:
            total = _w.getnframes() / float(_w.getframerate())
        Path(words_path).write_text(json.dumps(
            {"words": words, "total_duration_s": round(total, 3)}, indent=2,
        ))
        vo_result = {"words": words, "audio_path": audio_path, "words_path": words_path,
                     "total_duration_s": total}
        _log(settings, f"  aligned {len(words)} words ({words[0]['start']:.2f}s – {words[-1]['end']:.2f}s)")
    else:
        scenes_raw: list[dict[str, Any]] = plan.get("scenes", [])
        full_script = " ".join(s.get("vo_text", "") for s in scenes_raw).strip()
        if not full_script:
            _log(settings, "generate_voiceover — no vo_text on any scene, skipping")
            return plan
        _log(settings, f"generate_voiceover — voice={settings.voice} speed={settings.speed} style={settings.style or settings.style_hint or '<gemini default>'}")
        vo_result = json.loads(generate_voiceover(
            text=full_script, voice=settings.voice, speed=settings.speed, out_dir=rt["audio_dir"],
            style=settings.style, style_hint=settings.style_hint,
        ))
        audio_path = vo_result["audio_path"]
        words_path = vo_result["words_path"]
        _log(settings, f"  audio: {Path(audio_path).name}  ({vo_result['total_duration_s']:.1f}s)")

    rt["audio_path"] = audio_path
    rt["words_path"] = words_path
    rt["vo_result"] = vo_result
    return plan


def stage_align(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Assign per-scene start/end/duration from the aligned words.

    Breaks if: `aligned` (list with start_s/end_s/duration_s per scene)
    is not on `plan["_runtime"]` after this stage.
    """
    rt = _runtime(plan)
    _log(settings, "align_scenes")
    scenes = rt["scenes"]

    # When there's no VO (stage_voiceover skipped due to empty script), fall
    # back to per-scene duration_s overrides or a default clip length.
    if "vo_result" not in rt:
        scenes_raw = plan.get("scenes", [])
        raw_map = {s["index"]: s for s in scenes_raw}
        t = 0.0
        aligned = []
        for s in scenes:
            dur = float(raw_map.get(s["index"], {}).get("duration_s", 5.0))
            aligned.append({**s, "start_s": t, "end_s": t + dur, "duration_s": dur})
            t += dur
        aligned = _apply_timing_overrides(aligned, scenes_raw)
        for s in aligned:
            _log(settings, f"  [{s['index']:02d}] {s.get('start_s', 0):.2f}s – {s.get('end_s', 0):.2f}s ({s.get('duration_s', 0):.2f}s)")
        rt["aligned"] = aligned
        rt["aligned_json"] = json.dumps(aligned)
        return plan

    vo_result = rt["vo_result"]
    words_payload = {
        "words": vo_result["words"],
        "total_duration_s": vo_result.get("total_duration_s",
                                          vo_result["words"][-1]["end"] if vo_result["words"] else 0.0),
    }
    aligned_json = align_scenes(
        scenes_json=json.dumps(scenes),
        words_json=json.dumps(words_payload),
    )
    aligned = json.loads(aligned_json)
    aligned = _apply_timing_overrides(aligned, plan.get("scenes", []))
    for s in aligned:
        _log(settings, f"  [{s['index']:02d}] {s.get('start_s', 0):.2f}s – {s.get('end_s', 0):.2f}s ({s.get('duration_s', 0):.2f}s)")
    rt["aligned"] = aligned
    rt["aligned_json"] = json.dumps(aligned)
    return plan


def stage_manifest(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Write the per-run manifest.yaml to the output dir.

    Breaks if: `manifest.yaml` is missing in `out_dir` or its `scenes`
    array doesn't carry start_s/end_s/duration_s for every scene.
    """
    rt = _runtime(plan)
    manifest_path = str(Path(rt["out_dir"]) / "manifest.yaml")
    _log(settings, f"write_manifest → {manifest_path}")
    write_manifest(
        manifest_json=json.dumps({
            "version": rt["version"],
            "model": settings.model,
            "voice": settings.voice,
            "speed": settings.speed,
            "resolution": settings.resolution,
            "audio_path": rt.get("audio_path"),
            "words_path": rt.get("words_path"),
            "scenes": rt["aligned"],
        }),
        manifest_path=manifest_path,
    )
    rt["manifest_path"] = manifest_path
    return plan


def stage_assemble(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Build the Ken Burns draft mp4 (stills + voiceover).

    Breaks if: the draft mp4 is missing from `out_dir/video/` or
    `current_video` is not set on `plan["_runtime"]`.
    """
    rt = _runtime(plan)
    Path(rt["video_dir"]).mkdir(exist_ok=True)
    draft_path = str(Path(rt["video_dir"]) / f"{settings.concept_prefix}ken_burns_draft.mp4")
    _log(settings, f"ken_burns_assemble → {draft_path}")
    ken_burns_assemble(
        scenes_json=rt["aligned_json"],
        audio_path=rt.get("audio_path"),
        output_path=draft_path,
        resolution=settings.resolution,
    )
    rt["current_video"] = draft_path
    return plan


def stage_captions(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Burn word-aligned captions over the current video.

    Breaks if: `current_video` doesn't advance to `*_captioned.mp4`
    when captions are enabled (i.e. plan.captions != 'skip').
    """
    if settings.skip_captions:
        return plan
    rt = _runtime(plan)
    captioned_path = str(Path(rt["video_dir"]) / f"{settings.concept_prefix}captioned.mp4")
    _log(settings, f"burn_captions → {captioned_path}")
    burn_captions(
        video_path=rt["current_video"],
        words_json=rt["words_path"],
        output_path=captioned_path,
        caption_style=settings.caption_style,
        fontsize=settings.fontsize,
        words_per_chunk=settings.words_per_chunk,
        animation_override=settings.caption_animation_override,
        shift_s=settings.caption_shift_s,
    )
    rt["current_video"] = captioned_path
    return plan


def stage_titles(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Optionally burn section titles for any plan-declared `titles` cfg.

    Breaks if: a configured title with a valid `scene` index doesn't
    advance `current_video` to `*_titled.mp4`.
    """
    if not settings.titles_cfg:
        return plan
    rt = _runtime(plan)
    aligned = rt["aligned"]
    scene_map = {s["index"]: s for s in aligned}
    resolved_titles: list[dict] = []
    for t in settings.titles_cfg:
        if "scene" in t:
            sc = scene_map.get(t["scene"])
            if sc:
                start = sc["start_s"]
                end = start + float(t.get("duration_s", 2.5))
                resolved_titles.append({"text": t["text"], "start_s": start, "end_s": end})
        elif "start_s" in t and "end_s" in t:
            resolved_titles.append({"text": t["text"], "start_s": t["start_s"], "end_s": t["end_s"]})
    if resolved_titles:
        titled_path = str(Path(rt["video_dir"]) / f"{settings.concept_prefix}titled.mp4")
        _log(settings, f"burn_titles → {titled_path}")
        burn_titles(
            video_path=rt["current_video"],
            titles=resolved_titles,
            output_path=titled_path,
            fontsize=max(12, int(72 * settings.res_scale)),
        )
        rt["current_video"] = titled_path
    return plan


def stage_headline(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Optionally burn the headline (front-loaded title text).

    Breaks if: a non-empty `headline` setting doesn't advance
    `current_video` to `*_final.mp4`.
    """
    if not settings.headline:
        return plan
    rt = _runtime(plan)
    aligned = rt["aligned"]
    final_path = str(Path(rt["video_dir"]) / f"{settings.concept_prefix}final.mp4")
    _log(settings, f"burn_headline → {final_path}")
    h_fontsize = max(12, int(int(settings.headline_fontsize or 64) * settings.res_scale))
    max_chars = max(10, int(settings.video_width / (h_fontsize * 0.60)))
    headline_text = "\n".join(textwrap.wrap(settings.headline, width=max_chars))
    headline_kwargs: dict[str, Any] = {
        "video_path": rt["current_video"],
        "text": headline_text,
        "output_path": final_path,
        "fontsize": h_fontsize,
    }
    if settings.headline_bg:
        headline_kwargs["bg_color"] = settings.headline_bg
    if settings.headline_color:
        headline_kwargs["text_color"] = settings.headline_color
    if aligned:
        headline_kwargs["end_time_s"] = aligned[0].get("end_s")
    burn_headline(**headline_kwargs)
    rt["current_video"] = final_path
    return plan


def stage_avatar(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Optional avatar overlay (Aurora face track + chroma key composite).

    Breaks if: a configured avatar without a pre-keyed track skips
    chroma keying, or `current_video` doesn't advance to `avatar.mp4`
    when an avatar block is present in the plan.
    """
    avatar_cfg = settings.avatar_cfg
    if not avatar_cfg:
        return plan
    import sys
    rt = _runtime(plan)

    av_scene_indices: list[int] = avatar_cfg.get("scenes", [])
    position = avatar_cfg.get("position", "bottom_left")
    size = float(avatar_cfg.get("size", 0.40))
    chroma_key = avatar_cfg.get("chroma_key")
    chroma_similarity = float(avatar_cfg.get("chroma_similarity", 0.30))
    chroma_blend = float(avatar_cfg.get("chroma_blend", 0.03))
    aurora_prompt = avatar_cfg.get("aurora_prompt")
    y_offset_pct = avatar_cfg.get("y_offset_pct")
    if y_offset_pct is not None:
        y_offset_pct = float(y_offset_pct)
    crop_px = int(avatar_cfg.get("crop_px", 0))
    full_audio = bool(avatar_cfg.get("full_audio", False))

    avatar_track_keyed_raw = avatar_cfg.get("avatar_track_keyed")
    avatar_track_keyed: str | None = None
    if avatar_track_keyed_raw:
        p = Path(avatar_track_keyed_raw)
        avatar_track_keyed = str(p if p.is_absolute() else settings.folder / p)
        _log(settings, f"reusing avatar_track_keyed: {Path(avatar_track_keyed).name}")

    avatar_track_raw = avatar_cfg.get("avatar_track")
    if avatar_track_raw:
        av_track_p = Path(avatar_track_raw)
        avatar_track = str(av_track_p if av_track_p.is_absolute() else settings.folder / av_track_p)
        track_start_s = float(avatar_cfg.get("track_start_s", 0.0))
        _log(settings, f"reusing avatar_track: {Path(avatar_track).name} (starts at {track_start_s:.2f}s)")
    else:
        avatar_img_raw = avatar_cfg.get("image") or plan.get("character_image")
        if not avatar_img_raw:
            print("Error: avatar.image or character_image required to generate avatar track", file=sys.stderr)
            raise RuntimeError("avatar.image or character_image required")
        av_img_p = Path(avatar_img_raw)
        avatar_img = str(av_img_p if av_img_p.is_absolute() else settings.folder / av_img_p)

        mode_desc = "full-audio" if full_audio else f"{len(av_scene_indices)} scenes"
        _log(settings, f"generate_avatar_clips — {mode_desc}")
        av_result = json.loads(generate_avatar_clips(
            scenes_json=rt["aligned_json"],
            audio_path=rt["audio_path"],
            character_image=avatar_img,
            avatar_scene_indices=av_scene_indices,
            out_dir=rt["video_dir"],
            aurora_prompt=aurora_prompt,
            full_audio=full_audio,
        ))
        avatar_track = av_result["avatar_track"]
        track_start_s = av_result["track_start_s"]
        _log(settings, f"  track: {Path(avatar_track).name} starts at {track_start_s:.2f}s")
        out_ver = Path(rt["out_dir"]).name
        _log(
            settings,
            f"  → lock in plan to skip future Aurora calls:\n"
            f"    avatar:\n"
            f"      avatar_track: parallax/output/{out_ver}/video/{Path(avatar_track).name}\n"
            f"      track_start_s: {track_start_s:.1f}",
        )

    if not avatar_track_keyed and chroma_key:
        keyed_path = str(Path(rt["video_dir"]) / "avatar_track_keyed.mov")
        _log(settings, f"key_avatar_track → {keyed_path}")
        avatar_track_keyed = key_avatar_track(
            avatar_track=avatar_track,
            chroma_key=chroma_key,
            output_path=keyed_path,
            similarity=chroma_similarity,
            blend=chroma_blend,
        )
        out_ver = Path(rt["out_dir"]).name
        _log(
            settings,
            f"  → lock in plan to skip future chroma-key calls:\n"
            f"    avatar:\n"
            f"      avatar_track_keyed: parallax/output/{out_ver}/video/avatar_track_keyed.mov",
        )

    composite_track = avatar_track_keyed or avatar_track
    avatar_out = str(Path(rt["video_dir"]) / "avatar.mp4")
    _log(settings, f"burn_avatar ({position}) → {avatar_out}")
    kwargs: dict[str, Any] = dict(
        video_path=rt["current_video"],
        avatar_track=composite_track,
        track_start_s=track_start_s,
        output_path=avatar_out,
        position=position,
        size=size,
        out_width=settings.video_width,
    )
    if not avatar_track_keyed and chroma_key:
        kwargs["chroma_key"] = chroma_key
        kwargs["chroma_similarity"] = chroma_similarity
        kwargs["chroma_blend"] = chroma_blend
    if y_offset_pct is not None:
        kwargs["y_offset_pct"] = y_offset_pct
    if crop_px:
        kwargs["crop_px"] = crop_px
    burn_avatar(**kwargs)
    rt["current_video"] = avatar_out
    return plan


def stage_finalize(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Rename the in-flight mp4 to `{folder.name}-vN.mp4` + snapshot the plan.

    Breaks if: `out_dir` doesn't end up containing both
    `{folder.name}-vN.mp4` and a `plan.yaml` snapshot, or `cost.json`
    is missing.
    """
    from .context import current_session_id

    rt = _runtime(plan)
    final_out = str(Path(rt["out_dir"]) / rt["convention_name"])
    if rt["current_video"] != final_out:
        Path(rt["current_video"]).rename(final_out)
        rt["current_video"] = final_out

    # Snapshot the on-disk plan into the output dir — _lock_field_in_plan
    # has already written every locked path back to plan_path during the run.
    shutil.copy2(str(settings.plan_path), str(Path(rt["out_dir"]) / "plan.yaml"))

    run_cost = settings.usage.total_cost_usd
    cost_data = {
        "run_id": settings.usage.run_id,
        "session_id": current_session_id.get(),
        "cost_usd": run_cost,
        "version": rt["version"],
    }
    (Path(rt["out_dir"]) / "cost.json").write_text(json.dumps(cost_data, indent=2) + "\n")
    rt["run_cost"] = run_cost
    return plan


# --------------------------------------------------------------------------
# Helpers used by stage_stills + stage_align (lifted from produce.py)
# --------------------------------------------------------------------------

_KNOWN_SCENE_FIELDS = {
    "index", "shot_type", "vo_text", "prompt",
    "still_path", "reference", "reference_images",
    "animate", "animate_model", "motion_prompt", "clip_path", "animate_resolution",
    "end_frame_path",
    # video_references: character/style reference images passed to OpenRouter as
    # input_references for text-to-video consistency. Only effective when there is
    # no still_path driving frame_images (i.e. pure text-to-video scenes). Distinct
    # from reference_images, which is the image-gen still-frame reference field.
    "video_references",
    "zoom_direction", "zoom_amount",
    # Timing overrides — null/absent = derive from VO. Future graphical editor writes here.
    "duration_s", "start_offset_s", "fade_in_s", "fade_out_s",
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


def _apply_timing_overrides(aligned: list[dict[str, Any]], scenes_raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply optional per-scene timing overrides from the plan.

    Fields honored (null/absent = no-op):
      - duration_s: trim/pad scene to exact duration; cascades to following start_s
      - start_offset_s: shift scene start by this delta; cascades
      - fade_in_s / fade_out_s: passed through as scene metadata (renderer reads these)

    Cascade rule: when an override changes a scene's end_s, every subsequent
    scene shifts by the same delta so the timeline stays gap-free.
    """
    raw_by_idx = {s.get("index"): s for s in scenes_raw}
    delta = 0.0
    for sc in aligned:
        idx = sc.get("index")
        raw = raw_by_idx.get(idx, {})
        if delta:
            sc["start_s"] = round(sc.get("start_s", 0.0) + delta, 3)
            sc["end_s"] = round(sc.get("end_s", 0.0) + delta, 3)

        offset = raw.get("start_offset_s")
        if offset is not None:
            sc["start_s"] = round(sc.get("start_s", 0.0) + float(offset), 3)
            sc["end_s"] = round(sc.get("end_s", 0.0) + float(offset), 3)
            delta += float(offset)

        dur = raw.get("duration_s")
        if dur is not None:
            new_end = round(sc.get("start_s", 0.0) + float(dur), 3)
            old_end = sc.get("end_s", new_end)
            sc["end_s"] = new_end
            delta += new_end - old_end

        sc["duration_s"] = round(sc.get("end_s", 0.0) - sc.get("start_s", 0.0), 3)

        for f in ("fade_in_s", "fade_out_s"):
            if raw.get(f) is not None:
                sc[f] = float(raw[f])
    return aligned


# --------------------------------------------------------------------------
# Stage list — order matters
# --------------------------------------------------------------------------

STAGES = [
    stage_scan,
    stage_stills,
    stage_animate,
    stage_voiceover,
    stage_align,
    stage_manifest,
    stage_assemble,
    stage_captions,
    stage_titles,
    stage_headline,
    stage_avatar,
    stage_finalize,
]
