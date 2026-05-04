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

from . import runlog
from .assembly import align_scenes, ken_burns_assemble
from .avatar import burn_avatar, key_avatar_track
from .captions import burn_captions
from .headline import burn_headline, burn_titles
from .manifest import write_manifest
from .project import scan_project_folder
from .settings import Settings
from .voiceover import generate_voiceover


def _log(settings: Settings, msg: str) -> None:
    """Emit a human-readable progress line via the injected emitter.

    Mirrors the same `msg` into the active runlog as a `stage.log` event so
    stage-level activity is visible to tools that read `<output_dir>/run.log`
    (verify-suite `run_log.must_contain`, `parallax log`). The stdout stream
    via `settings.events` is unchanged — runlog is an additional, structured
    sink. Mirrored at `_log()` rather than inside `Settings.events` so the
    behavior is independent of which emitter callers inject.
    """
    settings.events("log", {"msg": msg})
    runlog.event("stage.log", msg=msg)


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
    # Append the last-6-hex of run_id so the artifact mp4 is traceable back
    # to the run from filename alone. run_id is required at this point —
    # `produce.run_plan` calls `runlog.start_run()` before invoking stages.
    if not settings.run_id:
        raise RuntimeError(
            "stage_scan: settings.run_id is unset. produce.run_plan must call "
            "runlog.start_run() and thread the id through settings before stages run."
        )
    short = runlog.short_id(settings.run_id)
    rt["short_id"] = short
    rt["convention_name"] = f"{settings.folder.name}-v{scan['version']}-{short}.mp4"
    rt["stills_dir"] = str(Path(rt["out_dir"]) / "stills")
    rt["audio_dir"] = str(Path(rt["out_dir"]) / "audio")
    rt["video_dir"] = str(Path(rt["out_dir"]) / "video")
    _log(settings, f"output_dir: {rt['out_dir']} (v{rt['version']})")
    # Bind the runlog file to <output_dir>/run.log and flush any buffered events.
    runlog.bind_output_dir(rt["out_dir"])
    runlog.record_run_meta(output_dir=rt["out_dir"], scene_count=len(plan.get("scenes", []) or []))
    return plan


# --------------------------------------------------------------------------
# stage_stills helpers
# --------------------------------------------------------------------------

def _resolve_scene_reference_images(s: dict[str, Any], settings: Settings) -> list[str] | None:
    """Return the reference image list for a scene that needs a new still.

    Priority:
    1. Explicit ``reference_images`` on the scene — resolved relative to project folder.
    2. All original (non-derivative) images in ``media/`` — capped at 4.
    3. ``character_image`` from settings when ``reference`` or ``stills_only`` flags are set.
    4. ``None`` — no references attached.
    """
    if "reference_images" in s:
        return [
            str(Path(r) if Path(r).is_absolute() else settings.folder / r)
            for r in s["reference_images"]
        ]

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
        return [str(p) for p in candidates[:4]] or None

    if s.get("reference") and settings.character_image:
        return [settings.character_image]
    if settings.stills_only and settings.character_image:
        return [settings.character_image]
    return None


def _generate_and_normalize_still(
    s: dict[str, Any],
    settings: Settings,
    stills_dir: str,
    refs: list[str] | None,
) -> tuple[str, str]:
    """Generate a still image with up to 2 attempts and normalize its aspect.

    Returns ``(still_path, raw_still_path)`` — ``still_path`` is the final
    (possibly micro-trimmed) PNG; ``raw_still_path`` is the original output from
    the model. They are equal when no trimming was needed.
    Raises ``AspectMismatchError`` if both attempts produce the wrong shape.
    """
    from .openrouter import generate_image
    from .stills import AspectMismatchError, check_aspect, normalize_aspect

    idx = s["index"]
    prompt = s.get("prompt", "")
    scene_aspect: str = s["aspect"]  # already validated by caller
    scene_image_model = s.get("image_model") or settings.image_model

    # Two attempts max: if the model returns a wrong-aspect image on
    # first try, regenerate once with a sterner textual prompt prefix.
    # If it's still wrong, raise — silent center-crop is forbidden
    # because it discards subject content for non-centered compositions.
    attempts = 2
    raw_still_path = None
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        stern_prefix = "" if attempt == 1 else _build_stern_prefix(scene_aspect)
        raw_still_path = generate_image(
            prompt=stern_prefix + prompt,
            alias=scene_image_model,
            reference_images=refs,
            out_dir=Path(stills_dir),
            aspect_ratio=scene_aspect,
            size=settings.resolution,
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
            f"scene {idx}: model {scene_image_model!r} returned wrong-aspect "
            f"image {check.src_w}x{check.src_h} ({check.mismatch_pct*100:.1f}% off). "
            f"Already retried {attempt - 1}x with sterner prompts. "
            f"This run is aborting — switch to a different image model or "
            f"rewrite the prompt to mention vertical/portrait framing explicitly."
        )

    if raw_still_path is None or last_err is not None and not check_aspect(raw_still_path, settings.resolution).within_tolerance:
        raise last_err if last_err else RuntimeError("stage_stills: no still produced")

    normalized = normalize_aspect(raw_still_path, settings.resolution)
    return str(normalized), str(raw_still_path)


def _build_scene_runtime_entry(s: dict[str, Any], still_path: str) -> dict[str, Any]:
    """Construct the in-flight scene dict written to ``_runtime["scenes"]``.

    Centralises every field copied from the raw plan scene so that the
    set of carried-over fields is visible in one place.
    """
    entry: dict[str, Any] = {
        "index": s["index"],
        "shot_type": s.get("shot_type", "broll"),
        "vo_text": s.get("vo_text", ""),
        "prompt": s.get("prompt", ""),
        "still_path": still_path,
        "aspect": s["aspect"],  # already resolved by caller
    }
    if s.get("animate"):
        entry["animate"] = True
        if s.get("video_model"):
            entry["video_model"] = s["video_model"]
        if s.get("motion_prompt"):
            entry["motion_prompt"] = s["motion_prompt"]
        if s.get("animate_resolution"):
            entry["animate_resolution"] = s["animate_resolution"]
        if s.get("end_frame_path"):
            entry["end_frame_path"] = s["end_frame_path"]  # already resolved by caller
    if s.get("clip_path"):
        entry["clip_path"] = s["clip_path"]  # already resolved by caller
    if s.get("zoom_direction"):
        entry["zoom_direction"] = s["zoom_direction"]
    if s.get("zoom_amount") is not None:
        entry["zoom_amount"] = float(s["zoom_amount"])
    return entry


def stage_stills(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Generate or reuse stills for every scene; lock new ones in the plan.

    Breaks if: any scene without a pre-existing still ends up missing a
    `still_path` on its in-flight scene entry, or normalize_aspect is
    skipped on freshly-generated PNGs.
    """
    from .settings import VALID_ASPECTS

    rt = _runtime(plan)
    scenes_raw: list[dict[str, Any]] = plan.get("scenes", [])
    _log(settings, f"generating {len(scenes_raw)} stills — image_model={settings.image_model} aspect={settings.aspect}")
    Path(rt["stills_dir"]).mkdir(exist_ok=True)

    scenes: list[dict[str, Any]] = []
    for s in scenes_raw:
        idx = s["index"]
        vo_text = s.get("vo_text", "")

        # Per-scene aspect override (validated against the same set as Settings).
        scene_aspect = s.get("aspect", settings.aspect)
        if scene_aspect not in VALID_ASPECTS:
            raise ValueError(
                f"scene {idx}: aspect={scene_aspect!r} is not a supported aspect "
                f"ratio. Choices: {sorted(VALID_ASPECTS)}"
            )
        # Stamp the resolved aspect onto a working copy so helpers can read it.
        s = {**s, "aspect": scene_aspect}

        if "still_path" in s:
            p = Path(s["still_path"])
            still_path = str(p if p.is_absolute() else (settings.folder / p))
            _log(settings, f"  [{idx:02d}] reusing {Path(still_path).name}")
        else:
            refs = _resolve_scene_reference_images(s, settings)
            _log(settings, f"  [{idx:02d}] {s.get('shot_type', 'broll')} — {vo_text[:55]}...")
            still_path, raw_still_path = _generate_and_normalize_still(
                s, settings, rt["stills_dir"], refs
            )
            if still_path != raw_still_path:
                _log(settings, f"       → {Path(still_path).name}  (micro-trimmed from {Path(raw_still_path).name})")
            else:
                _log(settings, f"       → {Path(still_path).name}")
            _lock_field_in_plan(settings.plan_path, plan, idx, "still_path", still_path, settings.folder)

        # Resolve optional path fields before building the runtime entry.
        s_resolved = dict(s)
        if s.get("end_frame_path"):
            ep = Path(s["end_frame_path"])
            s_resolved["end_frame_path"] = str(ep if ep.is_absolute() else settings.folder / ep)
        if s.get("clip_path"):
            cp = Path(s["clip_path"])
            s_resolved["clip_path"] = str(cp if cp.is_absolute() else settings.folder / cp)

        scenes.append(_build_scene_runtime_entry(s_resolved, still_path))

    rt["scenes"] = scenes
    return plan


# --------------------------------------------------------------------------
# stage_animate helpers
# --------------------------------------------------------------------------

def _resolve_animate_model(s: dict[str, Any], plan_video_model: str) -> str:
    """Return the video model alias for this scene, with per-scene override support."""
    return s.get("video_model") or plan_video_model


def _resolve_animate_references(s: dict[str, Any], settings: Settings) -> list[Path] | None:
    """Resolve ``video_references`` paths for text-to-video scenes.

    Only effective when no ``still_path`` drives ``frame_images``; when an
    image is present the model ignores ``input_references`` anyway.
    """
    video_refs_raw = s.get("video_references")
    if not video_refs_raw:
        return None
    return [
        (Path(r) if Path(r).is_absolute() else settings.folder / r)
        for r in video_refs_raw
    ]


def _animate_one_scene(
    s: dict[str, Any],
    settings: Settings,
    plan_video_model: str,
    video_dir: str,
    openrouter,
) -> str:
    """Run image-to-video for a single scene and return the clip path.

    Validates the model alias, resolves end_frame and input_references, then
    calls ``openrouter.generate_video``. Callers are responsible for the
    guard checks (animate flag, existing clip, valid still).
    """
    from .models import VIDEO_MODELS

    scene_model = _resolve_animate_model(s, plan_video_model)

    if scene_model not in VIDEO_MODELS:
        raise RuntimeError(
            f"video_model={scene_model!r} is not a known video alias. "
            f"Use one of: {', '.join(sorted(VIDEO_MODELS))}."
        )

    # Aspect comes from the scene entry (populated by stage_stills with
    # the per-scene override already resolved); fall back to the
    # settings aspect when the scene predates that field.
    aspect_ratio: str = s.get("aspect", settings.aspect)
    motion_prompt = s.get("motion_prompt") or s.get("prompt") or (
        "Subtle cinematic motion, gentle camera drift. Keep the scene stable and beautiful."
    )

    end_frame = s.get("end_frame_path")
    if end_frame and not Path(end_frame).exists():
        end_frame = None  # caller already logged the warning

    input_references = _resolve_animate_references(s, settings)

    # Per-scene animate_resolution wins; fall back to settings default.
    # This is the resolution sent to the video-gen model — cheaper than
    # the output resolution and upscaled by ffmpeg during assembly.
    scene_animate_res = s.get("animate_resolution") or settings.animate_resolution

    clip_path = openrouter.generate_video(
        prompt=motion_prompt,
        alias=scene_model,
        image_path=Path(s["still_path"]),
        end_image_path=Path(end_frame) if end_frame else None,
        input_references=input_references,
        out_dir=Path(video_dir),
        aspect_ratio=aspect_ratio,
        size=scene_animate_res,
    )
    return str(clip_path)


def stage_animate(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Run image-to-video for any scene flagged `animate: true` without a clip.

    Every scene routes through `openrouter.generate_video` using the alias
    in `animate_model` (e.g. 'mid', 'kling', 'seedance'). Supports
    `end_frame_path` on scenes for last-frame conditioning, and
    `video_references` for character/style consistency in text-to-video
    scenes (passed as input_references; ignored when a still drives
    frame_images).

    Model note: Seedance 2.0 (alias: seedance) is the recommended model for
    reference-to-video — it has the strongest documented input_references
    character consistency support per OpenRouter docs.

    Breaks if: animated scenes are not augmented with `clip_path` after
    this stage, or pre-locked clips aren't reported as reused.
    """
    from . import openrouter as _openrouter

    rt = _runtime(plan)
    scenes = rt["scenes"]
    plan_video_model = plan.get("video_model", "mid")

    animated_count = sum(1 for s in scenes if s.get("animate") and not s.get("clip_path"))
    if animated_count:
        Path(rt["video_dir"]).mkdir(exist_ok=True)
        _log(settings, f"animate_scenes — {animated_count} scene(s) to animate")

        for s in scenes:
            if not s.get("animate") or s.get("clip_path"):
                continue

            idx = s["index"]
            still = s.get("still_path")
            if not still or not Path(still).exists():
                _log(settings, f"  [{idx:02d}] WARNING: no valid still, skipping animation")
                continue

            scene_model = _resolve_animate_model(s, plan_video_model)
            scene_animate_res = s.get("animate_resolution") or settings.animate_resolution
            input_references = _resolve_animate_references(s, settings)

            end_frame = s.get("end_frame_path")
            if end_frame and not Path(end_frame).exists():
                _log(settings, f"  [{idx:02d}] WARNING: end_frame_path not found ({end_frame}), ignoring")
                end_frame = None
                s: dict[str, Any] = {**s, "end_frame_path": None}

            _log(settings, f"  [{idx:02d}] openrouter {scene_model} "
                 f"gen={scene_animate_res} → output={settings.resolution}"
                 + (f" + end_frame" if end_frame else "")
                 + (f" + {len(input_references)} ref(s)" if input_references else ""))

            s["clip_path"] = _animate_one_scene(
                s, settings, plan_video_model, rt["video_dir"], _openrouter
            )

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


# --------------------------------------------------------------------------
# stage_voiceover helpers
# --------------------------------------------------------------------------

def _reuse_voiceover(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Load a fully-locked voiceover (audio + words already on disk).

    Returns a ``vo_result`` dict ready to assign to ``_runtime``.
    """
    audio_path = str(
        (settings.folder / plan["audio_path"])
        if not Path(plan["audio_path"]).is_absolute()
        else Path(plan["audio_path"])
    )
    words_path = str(
        (settings.folder / plan["words_path"])
        if not Path(plan["words_path"]).is_absolute()
        else Path(plan["words_path"])
    )
    words_data = json.loads(Path(words_path).read_text())
    words_list = words_data if isinstance(words_data, list) else words_data.get("words", [])
    if isinstance(words_data, dict) and words_data.get("total_duration_s") is not None:
        total_dur = float(words_data["total_duration_s"])
    else:
        import wave as _wave
        with _wave.open(audio_path, "rb") as _w:
            total_dur = _w.getnframes() / float(_w.getframerate())
    return {
        "words": words_list,
        "audio_path": audio_path,
        "words_path": words_path,
        "total_duration_s": total_dur,
    }


def _align_voiceover(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Force-align a locked audio file that has no words file yet.

    Runs WhisperX via ``forced_align``, writes ``vo_words.json`` next to
    the audio, and returns a ``vo_result`` dict.
    """
    from . import forced_align

    audio_path = str(
        (settings.folder / plan["audio_path"])
        if not Path(plan["audio_path"]).is_absolute()
        else Path(plan["audio_path"])
    )
    words_path = str(Path(audio_path).with_name("vo_words.json"))
    words = forced_align.align_words(audio_path)
    import wave as _wave
    with _wave.open(audio_path, "rb") as _w:
        total = _w.getnframes() / float(_w.getframerate())
    Path(words_path).write_text(json.dumps(
        {"words": words, "total_duration_s": round(total, 3)}, indent=2,
    ))
    return {
        "words": words,
        "audio_path": audio_path,
        "words_path": words_path,
        "total_duration_s": total,
    }


def _synthesize_voiceover(plan: dict[str, Any], settings: Settings, audio_dir: str) -> dict[str, Any]:
    """Resolve voice model, build full script, and call TTS.

    Returns the parsed ``vo_result`` dict from ``generate_voiceover``.
    Raises if per-scene ``voice_model`` overrides disagree.
    """
    scenes_raw: list[dict[str, Any]] = plan.get("scenes", [])
    full_script = " ".join(s.get("vo_text", "") for s in scenes_raw).strip()
    if not full_script:
        return {}  # empty — caller must check

    # Per-scene voice_model override: if any scene declares one, all
    # scenes must agree (no per-scene synthesis split yet — the script
    # is TTS'd as one chunk to keep prosody coherent across scenes).
    scene_voice_models = {s["voice_model"] for s in scenes_raw if s.get("voice_model")}
    if len(scene_voice_models) > 1:
        raise RuntimeError(
            f"per-scene voice_model overrides disagree: {sorted(scene_voice_models)}. "
            f"Mixed-model synthesis is not supported (one TTS call per run). "
            f"Set the same `voice_model:` on every scene that overrides, or move "
            f"the value to the plan-level `voice_model:`."
        )
    voice_model = scene_voice_models.pop() if scene_voice_models else settings.voice_model

    return {
        "_voice_model": voice_model,
        "_full_script": full_script,
        "_audio_dir": audio_dir,
    }


def stage_voiceover(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Synthesize or reuse the voiceover; produce per-word alignment.

    Breaks if: `audio_path`, `words_path`, and `vo_result` are not on
    `plan["_runtime"]` after this stage.
    """
    rt = _runtime(plan)
    Path(rt["audio_dir"]).mkdir(exist_ok=True)

    if plan.get("audio_path") and plan.get("words_path"):
        vo_result = _reuse_voiceover(plan, settings)
        audio_path = vo_result["audio_path"]
        words_path = vo_result["words_path"]
        _log(settings, f"reusing voiceover: {Path(audio_path).name}")

    elif plan.get("audio_path") and not plan.get("words_path"):
        _log(settings, f"forced_align → {Path(plan['audio_path']).name} (whisperx)")
        vo_result = _align_voiceover(plan, settings)
        audio_path = vo_result["audio_path"]
        words_path = vo_result["words_path"]
        words = vo_result["words"]
        _log(settings, f"  aligned {len(words)} words ({words[0]['start']:.2f}s – {words[-1]['end']:.2f}s)")

    else:
        synthesis_info = _synthesize_voiceover(plan, settings, rt["audio_dir"])
        if not synthesis_info:
            _log(settings, "generate_voiceover — no vo_text on any scene, skipping")
            return plan

        voice_model = synthesis_info["_voice_model"]
        full_script = synthesis_info["_full_script"]
        _log(settings,
             f"generate_voiceover — voice={settings.voice} voice_model={voice_model} "
             f"style={settings.style or settings.style_hint or '<default>'}")
        vo_result = json.loads(generate_voiceover(
            text=full_script, voice=settings.voice,
            out_dir=rt["audio_dir"], style=settings.style, style_hint=settings.style_hint,
            voice_model=voice_model,
        ))
        audio_path = vo_result["audio_path"]
        words_path = vo_result["words_path"]
        _log(settings, f"  audio: {Path(audio_path).name}  ({vo_result['total_duration_s']:.1f}s)")

    rt["audio_path"] = audio_path
    rt["words_path"] = words_path
    rt["vo_result"] = vo_result
    return plan


# --------------------------------------------------------------------------
# stage_speed_adjust helpers
# --------------------------------------------------------------------------

def _resolve_voice_speed(plan: dict[str, Any]) -> float:
    """Return the effective playback rate, checking for per-scene agreement.

    Uses the plan-level ``voice_speed`` as the base, then allows a uniform
    per-scene override. Raises if per-scene values disagree (voiceover is
    synthesised as one chunk — a split would silently break prosody).
    """
    plan_speed = float(plan.get("voice_speed", 1.0))
    scenes_raw: list[dict[str, Any]] = plan.get("scenes", [])
    scene_speeds = {float(s["voice_speed"]) for s in scenes_raw if s.get("voice_speed") is not None}
    if len(scene_speeds) > 1:
        raise RuntimeError(
            f"per-scene voice_speed overrides disagree: {sorted(scene_speeds)}. "
            f"Mixed-speed adjustment is not supported (voiceover is one TTS call). "
            f"Set the same `voice_speed:` on every scene that overrides, or move "
            f"the value to the plan-level `voice_speed:`."
        )
    return scene_speeds.pop() if scene_speeds else plan_speed


def _apply_word_timestamp_scaling(vo_result: dict[str, Any], scale: float) -> tuple[list[dict], float]:
    """Scale word timestamps and total duration by *scale* (= 1 / rate).

    Returns ``(sped_words, sped_dur)`` — the caller writes these back into
    ``vo_result`` and the words JSON on disk.
    """
    sped_words = [
        {
            "word": w["word"],
            "start": round(w["start"] * scale, 3),
            "end": round(w["end"] * scale, 3),
        }
        for w in vo_result.get("words", [])
    ]
    sped_dur = sped_words[-1]["end"] if sped_words else round(
        float(vo_result.get("total_duration_s", 0.0)) * scale, 3
    )
    return sped_words, sped_dur


def stage_speed_adjust(plan: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Apply `audio.speedup` to the voiceover when `voice_speed` != 1.0.

    Driven by `plan["voice_speed"]` (top-level), with a uniform per-scene
    override allowed if every overriding scene agrees. Mismatched per-
    scene values raise — voiceover is synthesized as a single TTS chunk
    so a per-scene split would silently break prosody.

    Skipped (no-op) when:
      - rate ≈ 1.0
      - the voiceover stage produced no audio (empty script path)
      - the run is in PARALLAX_TEST_MODE (mock voiceover writes a
        deterministic silence track and we don't bother re-encoding it
        through atempo for the synthetic words; the synthetic word table
        is what downstream consumes)

    Idempotent: re-running on already-sped audio uses the same source as
    last time (the canonical voiceover.<ext> is overwritten in place via
    a temporary file).

    Breaks if: a plan with `voice_speed: 1.5` produces a voiceover whose
    duration matches the `voice_speed: 1.0` baseline.
    """
    from . import audio

    rt = _runtime(plan)
    if "vo_result" not in rt or not rt.get("audio_path"):
        return plan

    # Locked voiceover paths in the plan are taken as-is — the user is
    # locking the post-speed artifact, not the raw TTS. Re-applying
    # atempo on every run would double-speed the locked file.
    if plan.get("audio_path"):
        return plan

    rate = _resolve_voice_speed(plan)

    if abs(rate - 1.0) <= 1e-3:
        return plan

    audio_path = Path(rt["audio_path"])
    words_path = Path(rt["words_path"])
    tmp_out = audio_path.with_name(f"{audio_path.stem}_sped{audio_path.suffix}")
    _log(settings, f"speed_adjust — rate={rate:.3f} ({audio_path.name})")
    audio.speedup(audio_path, tmp_out, rate)
    tmp_out.replace(audio_path)

    scale = 1.0 / rate
    vo_result = rt["vo_result"]
    sped_words, sped_dur = _apply_word_timestamp_scaling(vo_result, scale)

    words_path.write_text(json.dumps(
        {"words": sped_words, "total_duration_s": sped_dur}, indent=2,
    ))

    vo_result["words"] = sped_words
    vo_result["total_duration_s"] = sped_dur
    rt["vo_result"] = vo_result
    runlog.event(
        "audio.speed_adjust",
        level="DEBUG",
        rate=rate,
        new_duration_s=sped_dur,
    )
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
            "image_model": settings.image_model,
            "video_model": settings.video_model,
            "voice": settings.voice,
            "voice_model": settings.voice_model,
            "voice_speed": settings.voice_speed,
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
    rt = _runtime(plan)

    position = avatar_cfg.get("position", "bottom_left")
    size = float(avatar_cfg.get("size", 0.40))
    chroma_key = avatar_cfg.get("chroma_key")
    chroma_similarity = float(avatar_cfg.get("chroma_similarity", 0.30))
    chroma_blend = float(avatar_cfg.get("chroma_blend", 0.03))
    y_offset_pct = avatar_cfg.get("y_offset_pct")
    if y_offset_pct is not None:
        y_offset_pct = float(y_offset_pct)
    crop_px = int(avatar_cfg.get("crop_px", 0))

    avatar_track_keyed_raw = avatar_cfg.get("avatar_track_keyed")
    avatar_track_keyed: str | None = None
    if avatar_track_keyed_raw:
        p = Path(avatar_track_keyed_raw)
        avatar_track_keyed = str(p if p.is_absolute() else settings.folder / p)
        _log(settings, f"reusing avatar_track_keyed: {Path(avatar_track_keyed).name}")

    avatar_track_raw = avatar_cfg.get("avatar_track")
    if not avatar_track_raw:
        # Avatar generation is no longer hosted by Parallax — the user must
        # supply a pre-recorded avatar clip path.
        raise RuntimeError(
            "avatar.avatar_track is required. Avatar generation (fal-ai/creatify/aurora) "
            "was removed in Phase 1.2; supply a pre-recorded clip via avatar.avatar_track "
            "and the chromakey + burn stages will run."
        )
    av_track_p = Path(avatar_track_raw)
    avatar_track = str(av_track_p if av_track_p.is_absolute() else settings.folder / av_track_p)
    track_start_s = float(avatar_cfg.get("track_start_s", 0.0))
    _log(settings, f"reusing avatar_track: {Path(avatar_track).name} (starts at {track_start_s:.2f}s)")

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

# Human-readable orientation descriptor by aspect — feeds the stern retry
# prefix when an image model returns the wrong aspect on the first try.
_ASPECT_STERN_DESCRIPTOR: dict[str, str] = {
    "9:16": "vertical portrait orientation, taller than wide",
    "16:9": "horizontal widescreen orientation, wider than tall",
    "1:1":  "square orientation, equal width and height",
    "4:3":  "landscape 4:3 orientation, wider than tall",
    "3:4":  "portrait 3:4 orientation, taller than wide",
}


def _build_stern_prefix(aspect: str) -> str:
    """Build the regenerate-with-sterner-prompt prefix for the chosen aspect.

    The first-attempt prompt has no prefix; this is only used on attempt 2
    when the model returned the wrong shape. Frames the directive in terms
    of the *requested* aspect rather than the legacy hardcoded "9:16".
    """
    descriptor = _ASPECT_STERN_DESCRIPTOR.get(aspect, f"{aspect} orientation")
    return (
        f"CRITICAL: Output MUST be {aspect} {descriptor}. Previous attempt "
        f"returned the wrong aspect and was REJECTED. Frame the subject for "
        f"{aspect}. "
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

def _wrap_stage(fn):
    """Wrap a stage callable with DEBUG `stage.<name>.start` / `.end` events.

    Display layers filter by level — full-fidelity timing always lands in
    the run.log file regardless of console verbosity.
    """
    name = fn.__name__.removeprefix("stage_")

    def wrapped(plan, settings):
        import time as _time
        scenes = plan.get("scenes", []) or []
        runlog.event(
            f"stage.{name}.start",
            level="DEBUG",
            scene_count=len(scenes),
            plan_keys=sorted(plan.keys()),
        )
        t0 = _time.monotonic()
        try:
            result = fn(plan, settings)
        finally:
            duration_ms = int((_time.monotonic() - t0) * 1000)
            runlog.event(
                f"stage.{name}.end",
                level="DEBUG",
                duration_ms=duration_ms,
                scene_count=len(plan.get("scenes", []) or []),
            )
        return result

    wrapped.__name__ = fn.__name__
    return wrapped


STAGES = [_wrap_stage(s) for s in (
    stage_scan,
    stage_stills,
    stage_animate,
    stage_voiceover,
    stage_speed_adjust,
    stage_align,
    stage_manifest,
    stage_assemble,
    stage_captions,
    stage_titles,
    stage_headline,
    stage_avatar,
    stage_finalize,
)]
