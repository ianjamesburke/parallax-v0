"""Pipeline stages — `(plan, settings, state) -> plan`.

Each stage owns one logical step of `produce`. Stages share state
through `PipelineState`, a typed dataclass threaded explicitly through
the stage loop by `produce.run_plan`. The plan dict carries only
durable, serialisable data (locked asset paths, settings fields); all
in-flight context lives on `state`.

Stages mutate disk (write images / mp4 / yaml) and return the plan.
The orchestrator (`run_plan`) walks the stage list in order; verify-
suite installs its own `settings.events` callback to capture per-stage
activity for assertions.
"""

from __future__ import annotations

import json
import re
import shutil
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import runlog
from .assembly import align_scenes, ken_burns_assemble
from .ffmpeg_utils import run_ffmpeg
from .avatar import burn_avatar, key_avatar_track
from .captions import burn_captions
from .headline import burn_headline, burn_titles
from .manifest import write_manifest
from .project import scan_project_folder
from .settings import ProductionMode, Settings
from .shim import is_mock_asset
from .voiceover import generate_voiceover_dict


# --------------------------------------------------------------------------
# Typed runtime state — replaces the untyped `plan["_runtime"]` blackboard
# --------------------------------------------------------------------------

@dataclass
class SceneRuntime:
    """In-flight per-scene state built by stage_stills and consumed by downstream stages."""
    index: int
    shot_type: str
    vo_text: str
    prompt: str
    still_path: str
    aspect: str
    animate: bool = False
    video_model: str | None = None
    motion_prompt: str | None = None
    animate_resolution: str | None = None
    end_frame_path: str | None = None
    clip_path: str | None = None
    clip_trim_start_s: float | str | None = None  # str to carry "auto" until assembly resolves it
    clip_trim_end_s: float | None = None
    zoom_direction: str | None = None
    zoom_amount: float | None = None
    video_references: list[str] | None = None


@dataclass
class PipelineState:
    """All in-flight state for a single produce run.

    Initialized empty in `produce.run_plan` and threaded through every
    stage. Stages read and write fields directly (attribute access, not
    dict key access) so typos and missing fields surface as AttributeError
    at the point of the mistake rather than KeyError at a later stage.
    """
    out_dir: str = ""
    assets_dir: str = ""
    version: int = 0
    short_id: str = ""
    convention_name: str = ""
    stills_dir: str = ""
    audio_dir: str = ""
    video_dir: str = ""
    scenes: list[SceneRuntime] = field(default_factory=list)
    audio_path: str | None = None
    words_path: str | None = None
    vo_result: dict[str, Any] | None = None
    aligned: list[dict[str, Any]] = field(default_factory=list)
    aligned_json: str = ""
    manifest_path: str | None = None
    current_video: str | None = None
    run_cost: float = 0.0


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

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


_plan_lock = threading.Lock()


def _is_clip_reusable(clip: str | None, mode: ProductionMode) -> bool:
    """Return True if an existing clip asset can be reused without re-generating.

    A clip is NOT reusable if:
    - clip is None
    - mode is REAL and the path points to a mock/dry-run placeholder
    - the file does not exist on disk
    """
    if clip is None:
        return False
    if mode == ProductionMode.REAL and is_mock_asset(clip):
        return False
    return Path(clip).exists()


def _lock_field_in_plan(plan_path: Path, plan: dict, scene_idx: int, field_name: str, value: str, folder: Path) -> None:
    """Write an asset path back into the plan YAML so the scene is locked on future runs.

    Reads the current on-disk YAML, patches only the specific scene/field, and writes
    back with yaml.safe_dump — preserving any user edits made during or between runs.
    """
    import yaml
    try:
        locked_path = str(Path(value).relative_to(folder))
    except ValueError:
        locked_path = value
    with _plan_lock:
        # Patch the in-memory dict so downstream stages in this run see the new value.
        for scene in plan.get("scenes", []):
            if scene.get("index") == scene_idx:
                scene[field_name] = locked_path
                break

        # Read current on-disk state to avoid overwriting concurrent user edits.
        try:
            with plan_path.open("r", encoding="utf-8") as f:
                disk_plan = yaml.safe_load(f) or {}
        except FileNotFoundError:
            disk_plan = {"scenes": []}

        patched = False
        for scene in disk_plan.get("scenes", []):
            if scene.get("index") == scene_idx:
                scene[field_name] = locked_path
                patched = True
                break

        if not patched:
            runlog.event("plan.lock.warn", level="WARN", scene=scene_idx, field=field_name,
                         msg="scene not found in on-disk plan — field not written")

        try:
            with plan_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(disk_plan, f, default_flow_style=False, allow_unicode=True,
                               sort_keys=False, width=10000)
        except Exception as exc:
            runlog.event("plan.lock.error", level="ERROR", scene=scene_idx, field=field_name, error=str(exc))
            raise RuntimeError(f"plan lock failed: could not write {plan_path}: {exc}") from exc


# --------------------------------------------------------------------------
# Stage callables
# --------------------------------------------------------------------------

def stage_scan(plan: dict[str, Any], settings: Settings, state: PipelineState) -> dict[str, Any]:
    """Scan the project folder, derive the versioned output dir.

    Breaks if: `state.out_dir` / `state.version` / `state.convention_name`
    are not populated after this stage runs.
    """
    _log(settings, "scan_project_folder")
    scan = json.loads(scan_project_folder(str(settings.folder)))
    state.out_dir = scan["output_dir"]
    state.assets_dir = scan["assets_dir"]
    state.version = scan["version"]
    # Append the last-6-hex of run_id so the artifact mp4 is traceable back
    # to the run from filename alone. run_id is required at this point —
    # `produce.run_plan` calls `runlog.start_run()` before invoking stages.
    if not settings.run_id:
        raise RuntimeError(
            "stage_scan: settings.run_id is unset. produce.run_plan must call "
            "runlog.start_run() and thread the id through settings before stages run."
        )
    short = runlog.short_id(settings.run_id)
    state.short_id = short
    state.convention_name = f"{settings.folder.name}-v{scan['version']}-{short}.mp4"
    state.stills_dir = str(Path(state.out_dir) / "stills")
    state.audio_dir = str(Path(state.out_dir) / "audio")
    state.video_dir = str(Path(state.out_dir) / "video")
    _log(settings, f"output_dir: {state.out_dir} (v{state.version})")
    # Bind the runlog file to <output_dir>/run.log and flush any buffered events.
    runlog.bind_output_dir(state.out_dir)
    runlog.record_run_meta(output_dir=state.out_dir, scene_count=len(plan.get("scenes", []) or []))
    return plan


_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm"})


def _extract_character_image_frame(char_image: str, folder: Path) -> str:
    """Return a path suitable for use as an image-gen reference.

    If ``char_image`` is already an image file, return it unchanged.
    If it's a video, extract the frame at 1 s into a cached PNG under
    ``<folder>/__parallax_cache__/character_frame.png`` and return that.
    The cache PNG is reused across calls — ffmpeg is not re-invoked when the
    file already exists.

    Raises RuntimeError naming the video path when ffmpeg exits non-zero.
    """
    if Path(char_image).suffix.lower() not in _VIDEO_SUFFIXES:
        return char_image

    cache_png = folder / "__parallax_cache__" / "character_frame.png"
    if cache_png.exists():
        return str(cache_png)

    cache_png.parent.mkdir(parents=True, exist_ok=True)
    result = run_ffmpeg(
        ["ffmpeg", "-y", "-ss", "1", "-i", char_image, "-frames:v", "1", str(cache_png)],
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"_extract_character_image_frame: ffmpeg failed to extract frame from "
            f"{char_image}: {result.stderr}"
        )
    return str(cache_png)


def _resolve_still_refs(s: dict[str, Any], settings: Settings) -> list[str] | None:
    """Return the reference image list for a scene still.

    Additive: scene `reference_images` and settings `character_image` (when
    `reference: true` or `stills_only`) are merged, not mutually exclusive.
    Falls back to media/ images when no explicit refs are set.
    """
    import re as _re

    refs: list[str] | None = None

    if "reference_images" in s:
        refs = [str(Path(r) if Path(r).is_absolute() else settings.folder / r) for r in s["reference_images"]]

    if s.get("reference") and settings.character_image:
        char_frame = _extract_character_image_frame(settings.character_image, settings.folder)
        refs = (refs or []) + [char_frame]
    elif settings.stills_only and settings.character_image:
        char_frame = _extract_character_image_frame(settings.character_image, settings.folder)
        refs = (refs or []) + [char_frame]

    if settings.product_image and s.get("shot_type", "broll") == "broll":
        refs = [settings.product_image] + (refs or [])

    if refs is None:
        media_dir = settings.folder / "media"
        if media_dir.is_dir():
            derivative_pat = _re.compile(r"_[an]\d+x\d+$")
            candidates = sorted(
                p for p in media_dir.iterdir()
                if p.is_file()
                and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
                and not derivative_pat.search(p.stem)
            )
            refs = [str(p) for p in candidates[:4]] or None

    if refs is None:
        scene_idx = s.get("index", "?")
        print(
            f"  [WARNING] scene {scene_idx}: no reference images found — "
            "stills may not match your hero. "
            "Add images to media/ or set reference_images in the plan.",
            flush=True,
        )

    return refs


def _generate_one_still(s: dict[str, Any], settings: Settings, state: PipelineState, plan: dict[str, Any]) -> str:
    """Generate (with retry) one still for scene `s`. Thread-safe. Returns normalized still path."""
    from .openrouter import generate_image
    from .stills import AspectMismatchError, check_aspect, normalize_aspect

    idx = s["index"]
    prompt = s.get("prompt", "")
    scene_aspect = s.get("aspect", settings.aspect)
    scene_image_model = s.get("image_model") or settings.image_model

    refs = _resolve_still_refs(s, settings)

    attempts = 2
    raw_still_path = None
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        stern_prefix = "" if attempt == 1 else _build_stern_prefix(scene_aspect)
        raw_still_path = generate_image(
            prompt=stern_prefix + prompt,
            alias=scene_image_model,
            reference_images=refs,
            out_dir=Path(state.stills_dir),
            aspect_ratio=scene_aspect,
            size=settings.resolution,
        )
        check = check_aspect(raw_still_path, settings.resolution)
        if check.within_tolerance:
            break
        last_err = AspectMismatchError(
            f"scene {idx}: model {scene_image_model!r} returned wrong-aspect "
            f"image {check.src_w}x{check.src_h} ({check.mismatch_pct*100:.1f}% off). "
            f"Already retried {attempt - 1}x with sterner prompts. "
            f"This run is aborting — switch to a different image model or "
            f"rewrite the prompt to mention vertical/portrait framing explicitly."
        )

    if raw_still_path is None or (last_err is not None and not check_aspect(raw_still_path, settings.resolution).within_tolerance):
        raise last_err if last_err else RuntimeError("stage_stills: no still produced")

    normalized = normalize_aspect(raw_still_path, settings.resolution)
    canonical = Path(state.assets_dir) / f"scene_{idx:02d}_still.png"
    Path(normalized).rename(canonical)
    if str(normalized) != str(raw_still_path):
        Path(raw_still_path).unlink(missing_ok=True)
    still_path = str(canonical)
    _lock_field_in_plan(settings.plan_path, plan, idx, "still_path", still_path, settings.folder)
    return still_path


def stage_stills(plan: dict[str, Any], settings: Settings, state: PipelineState) -> dict[str, Any]:
    """Generate or reuse stills for every scene; lock new ones in the plan.

    Breaks if: any scene without a pre-existing still ends up missing a
    `still_path` on its in-flight SceneRuntime, or normalize_aspect is
    skipped on freshly-generated PNGs.
    """
    from .settings import VALID_ASPECTS

    scenes_raw: list[dict[str, Any]] = plan.get("scenes", [])
    _log(settings, f"generating {len(scenes_raw)} stills — image_model={settings.image_model} aspect={settings.aspect}")
    _warn_unknown_scene_fields(scenes_raw)
    Path(state.stills_dir).mkdir(exist_ok=True)

    # Phase 1: categorize — reuse vs. generate
    still_paths: dict[int, str] = {}
    to_generate: list[dict[str, Any]] = []
    for s in scenes_raw:
        idx = s["index"]
        scene_aspect = s.get("aspect", settings.aspect)
        if scene_aspect not in VALID_ASPECTS:
            raise ValueError(
                f"scene {idx}: aspect={scene_aspect!r} is not a supported aspect "
                f"ratio. Choices: {sorted(VALID_ASPECTS)}"
            )
        _locked_still: str | None = s.get("still_path")
        if _locked_still is not None and not (
            settings.mode == ProductionMode.REAL and is_mock_asset(_locked_still)
        ):
            p = Path(_locked_still)
            still_paths[idx] = str(p if p.is_absolute() else (settings.folder / p))
            _log(settings, f"  [{idx:02d}] reusing {Path(still_paths[idx]).name}")
        else:
            if _locked_still:
                _log(settings, f"  [{idx:02d}] dry-run mock detected — regenerating")
            to_generate.append(s)

    # Phase 2: fire all generations concurrently
    n_gen = len(to_generate)
    if n_gen:
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=n_gen) as pool:
            futures: dict = {}
            for s in to_generate:
                idx = s["index"]
                vo_text = s.get("vo_text", "")
                display_text = re.sub(r'\[[^\]]*\]', '', vo_text).strip()
                _log(settings, f"  [{idx:02d}] {s.get('shot_type', 'broll')} — {display_text[:55]}... submitting")
                fut = pool.submit(_generate_one_still, s, settings, state, plan)
                futures[fut] = idx
            for fut in as_completed(futures):
                idx = futures[fut]
                still_path = fut.result()  # propagates any generation error
                still_paths[idx] = still_path
                _log(settings, f"  [{idx:02d}] → {Path(still_path).name}")
        elapsed = time.monotonic() - t0
        _log(settings, f"generated {n_gen} stills in {elapsed:.1f}s ({n_gen} concurrent)")

    # Phase 3: build SceneRuntime in original order
    scenes: list[SceneRuntime] = []
    for s in scenes_raw:
        idx = s["index"]
        scene_rt = SceneRuntime(
            index=idx,
            shot_type=s.get("shot_type", "broll"),
            vo_text=s.get("vo_text", ""),
            prompt=s.get("prompt", ""),
            still_path=still_paths[idx],
            aspect=s.get("aspect", settings.aspect),
        )
        if s.get("animate"):
            scene_rt.animate = True
            if s.get("video_model"):
                scene_rt.video_model = s["video_model"]
            if s.get("motion_prompt"):
                scene_rt.motion_prompt = s["motion_prompt"]
            if s.get("animate_resolution"):
                scene_rt.animate_resolution = s["animate_resolution"]
            if s.get("end_frame_path"):
                ep = Path(s["end_frame_path"])
                scene_rt.end_frame_path = str(ep if ep.is_absolute() else settings.folder / ep)
        if s.get("clip_path"):
            cp = Path(s["clip_path"])
            scene_rt.clip_path = str(cp if cp.is_absolute() else settings.folder / cp)
        if s.get("clip_trim_start_s") is not None:
            scene_rt.clip_trim_start_s = s["clip_trim_start_s"]  # may be "auto" or float
        if s.get("clip_trim_end_s") is not None:
            scene_rt.clip_trim_end_s = float(s["clip_trim_end_s"])
        if s.get("zoom_direction"):
            scene_rt.zoom_direction = s["zoom_direction"]
        if s.get("zoom_amount") is not None:
            scene_rt.zoom_amount = float(s["zoom_amount"])
        if s.get("video_references"):
            scene_rt.video_references = s["video_references"]
        scenes.append(scene_rt)

    state.scenes = scenes
    return plan


def _animate_one_scene(
    s: SceneRuntime,
    settings: Settings,
    state: PipelineState,
    plan_video_model: str,
) -> str:
    """Animate one scene. Thread-safe. Returns clip_path string."""
    from .models import VIDEO_MODELS
    from . import openrouter as _openrouter

    scene_model = s.video_model or plan_video_model
    if scene_model not in VIDEO_MODELS:
        raise RuntimeError(
            f"video_model={scene_model!r} is not a known video alias. "
            f"Use one of: {', '.join(sorted(VIDEO_MODELS))}."
        )

    still = s.still_path
    if not still or not Path(still).exists():
        raise RuntimeError(f"scene {s.index}: no valid still for animation")

    motion_prompt = s.motion_prompt or s.prompt or (
        "Subtle cinematic motion, gentle camera drift. Keep the scene stable and beautiful."
    )
    end_frame = s.end_frame_path
    if end_frame and not Path(end_frame).exists():
        end_frame = None

    input_references: list[Path] | None = None
    if s.video_references:
        input_references = [
            (Path(r) if Path(r).is_absolute() else settings.folder / r)
            for r in s.video_references
        ]

    scene_animate_res = s.animate_resolution or settings.animate_resolution

    # Per-scene temp dir isolates hash-named downloads so concurrent scenes
    # with identical prompts don't race on the same filename.
    import shutil as _shutil
    scene_tmp = Path(state.assets_dir) / f"_anim_{s.index:02d}"
    scene_tmp.mkdir(exist_ok=True)
    raw_clip = _openrouter.generate_video(
        prompt=motion_prompt,
        alias=scene_model,
        image_path=Path(still),
        end_image_path=Path(end_frame) if end_frame else None,
        input_references=input_references,
        out_dir=scene_tmp,
        aspect_ratio=s.aspect,
        size=scene_animate_res,
    )
    canonical = Path(state.assets_dir) / f"scene_{s.index:02d}_animated.mp4"
    Path(raw_clip).rename(canonical)
    _shutil.rmtree(scene_tmp, ignore_errors=True)
    return str(canonical)


def stage_animate(plan: dict[str, Any], settings: Settings, state: PipelineState) -> dict[str, Any]:
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
    scenes = state.scenes
    plan_video_model = plan.get("video_model", "mid")

    def _clip_reusable(clip: str | None) -> bool:
        return _is_clip_reusable(clip, settings.mode)

    to_animate = [s for s in scenes if s.animate and not _clip_reusable(s.clip_path)]

    if to_animate:
        Path(state.video_dir).mkdir(exist_ok=True)
        _log(settings, f"animate_scenes — {len(to_animate)} scene(s) to animate")

        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=len(to_animate)) as pool:
            futures: dict = {}
            for s in to_animate:
                if s.clip_path:
                    _log(settings, f"  [{s.index:02d}] dry-run mock detected — re-animating")
                scene_model = s.video_model or plan_video_model
                scene_animate_res = s.animate_resolution or settings.animate_resolution
                _log(settings, f"  [{s.index:02d}] submitting {scene_model} gen"
                     f" gen={scene_animate_res} → output={settings.resolution}"
                     + (f" + end_frame" if s.end_frame_path and Path(s.end_frame_path).exists() else "")
                     + (f" + {len(s.video_references)} ref(s)" if s.video_references else ""))
                fut = pool.submit(_animate_one_scene, s, settings, state, plan_video_model)
                futures[fut] = s
            for fut in as_completed(futures):
                s = futures[fut]
                clip_path = fut.result()  # propagates any animation error
                s.clip_path = clip_path
                _log(settings, f"  [{s.index:02d}] → {Path(clip_path).name}")
                _lock_field_in_plan(settings.plan_path, plan, s.index, "clip_path", clip_path, settings.folder)
        elapsed = time.monotonic() - t0
        _log(settings, f"generated {len(to_animate)} clips in {elapsed:.1f}s ({len(to_animate)} concurrent)")
    else:
        locked = sum(1 for s in scenes if s.clip_path)
        if locked:
            _log(settings, f"reusing {locked} animated clip(s)")
    return plan


def stage_voiceover(plan: dict[str, Any], settings: Settings, state: PipelineState) -> dict[str, Any]:
    """Synthesize or reuse the voiceover; produce per-word alignment.

    Breaks if: `state.audio_path`, `state.words_path`, and `state.vo_result`
    are not set after this stage.
    """
    Path(state.audio_dir).mkdir(exist_ok=True)

    if plan.get("audio_path") and plan.get("words_path"):
        audio_path = str((settings.folder / plan["audio_path"]) if not Path(plan["audio_path"]).is_absolute() else Path(plan["audio_path"]))
        words_path = str((settings.folder / plan["words_path"]) if not Path(plan["words_path"]).is_absolute() else Path(plan["words_path"]))
        words_data = json.loads(Path(words_path).read_text())
        words_list = words_data if isinstance(words_data, list) else words_data.get("words", [])
        # Always probe the actual file — JSON total_duration_s can be set to
        # last-word-end (missing trailing silence) from older runs.
        _probe = run_ffmpeg(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, text=True,
        )
        total_dur = (
            float(_probe.stdout.strip())
            if _probe.stdout.strip()
            else float(words_data.get("total_duration_s", words_list[-1]["end"] if words_list else 0.0))
            if isinstance(words_data, dict)
            else float(words_list[-1]["end"]) if words_list else 0.0
        )
        vo_result = {"words": words_list, "audio_path": audio_path,
                     "words_path": words_path, "total_duration_s": total_dur}
        _log(settings, f"reusing voiceover: {Path(audio_path).name}")
    elif plan.get("audio_path") and not plan.get("words_path"):
        audio_path = str((settings.folder / plan["audio_path"]) if not Path(plan["audio_path"]).is_absolute() else Path(plan["audio_path"]))
        words_path = str(Path(audio_path).with_name("vo_words.json"))
        if Path(words_path).exists():
            _log(settings, f"reusing cached words: {Path(words_path).name}")
            words_data = json.loads(Path(words_path).read_text())
            words = words_data if isinstance(words_data, list) else words_data.get("words", [])
            total = (
                float(words_data.get("total_duration_s", words[-1]["end"])) if isinstance(words_data, dict)
                else float(words[-1]["end"]) if words else 0.0
            )
        else:
            from . import forced_align
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
        voice_model = (scene_voice_models.pop() if scene_voice_models else settings.voice_model)

        _log(settings,
             f"generate_voiceover — voice={settings.voice} voice_model={voice_model} "
             f"style={settings.style or settings.style_hint or '<default>'}")
        pronunciations = plan.get("pronunciations") or {}
        vo_result = generate_voiceover_dict(
            text=full_script, voice=settings.voice,
            out_dir=state.audio_dir, style=settings.style, style_hint=settings.style_hint,
            voice_model=voice_model,
            pronunciations=pronunciations or None,
            trim_pauses=settings.trim_pauses,
        )
        audio_path = vo_result["audio_path"]
        words_path = vo_result["words_path"]
        _log(settings, f"  audio: {Path(audio_path).name}  ({vo_result['total_duration_s']:.1f}s)")

    state.audio_path = audio_path
    state.words_path = words_path
    state.vo_result = vo_result
    return plan


def stage_speed_adjust(plan: dict[str, Any], settings: Settings, state: PipelineState) -> dict[str, Any]:
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

    if state.vo_result is None or not state.audio_path:
        return plan

    # Locked voiceover paths in the plan are taken as-is — the user is
    # locking the post-speed artifact, not the raw TTS. Re-applying
    # atempo on every run would double-speed the locked file.
    if plan.get("audio_path"):
        return plan

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
    rate = scene_speeds.pop() if scene_speeds else plan_speed

    if abs(rate - 1.0) <= 1e-3:
        return plan

    assert state.words_path is not None
    audio_path = Path(state.audio_path)
    words_path = Path(state.words_path)
    tmp_out = audio_path.with_name(f"{audio_path.stem}_sped{audio_path.suffix}")
    _log(settings, f"speed_adjust — rate={rate:.3f} ({audio_path.name})")
    audio.speedup(audio_path, tmp_out, rate)
    tmp_out.replace(audio_path)

    scale = 1.0 / rate
    vo_result = state.vo_result
    sped_words = [
        {"word": w["word"],
         "start": round(w["start"] * scale, 3),
         "end": round(w["end"] * scale, 3)}
        for w in vo_result.get("words", [])
    ]
    _sped_probe = run_ffmpeg(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True,
    )
    sped_dur = (
        float(_sped_probe.stdout.strip())
        if _sped_probe.stdout.strip()
        else (sped_words[-1]["end"] if sped_words else round(
            float(vo_result.get("total_duration_s", 0.0)) * scale, 3
        ))
    )

    words_path.write_text(json.dumps(
        {"words": sped_words, "total_duration_s": sped_dur}, indent=2,
    ))

    vo_result["words"] = sped_words
    vo_result["total_duration_s"] = sped_dur
    state.vo_result = vo_result
    runlog.event(
        "audio.speed_adjust",
        level="DEBUG",
        rate=rate,
        new_duration_s=sped_dur,
    )
    return plan


def stage_align(plan: dict[str, Any], settings: Settings, state: PipelineState) -> dict[str, Any]:
    """Assign per-scene start/end/duration from the aligned words.

    Breaks if: `state.aligned` (list with start_s/end_s/duration_s per scene)
    is not set after this stage.
    """
    _log(settings, "align_scenes")
    scenes = state.scenes

    # When there's no VO (stage_voiceover skipped due to empty script), fall
    # back to per-scene duration_s overrides or a default clip length.
    if state.vo_result is None:
        scenes_raw = plan.get("scenes", [])
        raw_map = {s["index"]: s for s in scenes_raw}
        t = 0.0
        aligned = []
        for s in scenes:
            dur = float(raw_map.get(s.index, {}).get("duration_s", 5.0))
            aligned.append({**_scene_to_dict(s), "start_s": t, "end_s": t + dur, "duration_s": dur})
            t += dur
        aligned = _apply_timing_overrides(aligned, scenes_raw)
        for s in aligned:
            _log(settings, f"  [{s['index']:02d}] {s.get('start_s', 0):.2f}s – {s.get('end_s', 0):.2f}s ({s.get('duration_s', 0):.2f}s)")
        state.aligned = aligned
        state.aligned_json = json.dumps(aligned)
        return plan

    vo_result = state.vo_result
    words_payload = {
        "words": vo_result["words"],
        "total_duration_s": vo_result.get("total_duration_s",
                                          vo_result["words"][-1]["end"] if vo_result["words"] else 0.0),
    }
    # Merge plan-level duration_s pins into scene dicts so align_scenes_obj
    # can anchor the next scene's word-search cursor correctly (Bug 1 fix).
    raw_by_idx = {s.get("index"): s for s in plan.get("scenes", [])}
    scenes_for_align = []
    for s in scenes:
        sd = _scene_to_dict(s)
        raw_dur = raw_by_idx.get(sd.get("index"), {}).get("duration_s")
        if raw_dur is not None:
            sd["duration_s"] = raw_dur
        scenes_for_align.append(sd)

    aligned_json = align_scenes(
        scenes_json=json.dumps(scenes_for_align),
        words_json=json.dumps(words_payload),
    )
    aligned = json.loads(aligned_json)
    aligned = _apply_timing_overrides(aligned, plan.get("scenes", []))
    for s in aligned:
        _log(settings, f"  [{s['index']:02d}] {s.get('start_s', 0):.2f}s – {s.get('end_s', 0):.2f}s ({s.get('duration_s', 0):.2f}s)")
    state.aligned = aligned
    state.aligned_json = json.dumps(aligned)

    # Write corrected words back — cross-check may have patched TTS
    # hallucinations (e.g. "sweatsh" → "switch") in the words list.
    # Captions read from the file, so the fixes must be persisted.
    if state.words_path:
        words_path = Path(state.words_path)
        words_path.write_text(json.dumps({
            "words": vo_result["words"],
            "total_duration_s": words_payload["total_duration_s"],
        }, indent=2))
    return plan


def stage_manifest(plan: dict[str, Any], settings: Settings, state: PipelineState) -> dict[str, Any]:
    """Write the per-run manifest.yaml to the output dir.

    Breaks if: `manifest.yaml` is missing in `state.out_dir` or its `scenes`
    array doesn't carry start_s/end_s/duration_s for every scene.
    """
    manifest_path = str(Path(state.out_dir) / "manifest.yaml")
    _log(settings, f"write_manifest → {manifest_path}")
    write_manifest(
        manifest_json=json.dumps({
            "version": state.version,
            "image_model": settings.image_model,
            "video_model": settings.video_model,
            "voice": settings.voice,
            "voice_model": settings.voice_model,
            "voice_speed": settings.voice_speed,
            "resolution": settings.resolution,
            "audio_path": state.audio_path,
            "words_path": state.words_path,
            "scenes": state.aligned,
        }),
        manifest_path=manifest_path,
    )
    state.manifest_path = manifest_path
    return plan


def stage_assemble(plan: dict[str, Any], settings: Settings, state: PipelineState) -> dict[str, Any]:
    """Build the Ken Burns draft mp4 (stills + voiceover).

    Breaks if: the draft mp4 is missing from `state.video_dir` or
    `state.current_video` is not set.
    """
    from .assembly import _SUPPORTED_XFADE_TRANSITIONS

    Path(state.video_dir).mkdir(exist_ok=True)
    draft_path = str(Path(state.video_dir) / f"{settings.concept_prefix}ken_burns_draft.mp4")

    # Resolve per-scene transition lists from plan defaults + per-scene overrides.
    default_transition = plan.get("default_transition")
    default_transition_dur = float(plan.get("default_transition_duration_s", 0.5))
    scenes_raw: list[dict[str, Any]] = plan.get("scenes", [])

    resolved_transitions: list[str | None] = []
    resolved_durations: list[float] = []
    for i, s in enumerate(scenes_raw):
        # Scene 0 never has an entry transition (no prior scene to fade from).
        if i == 0:
            resolved_transitions.append(None)
            resolved_durations.append(default_transition_dur)
            continue
        trans = s.get("transition", default_transition)
        if trans is not None and trans not in _SUPPORTED_XFADE_TRANSITIONS:
            raise ValueError(
                f"scene {s.get('index', i)}: unsupported transition {trans!r}. "
                f"Supported: {sorted(_SUPPORTED_XFADE_TRANSITIONS)}"
            )
        resolved_transitions.append(trans)
        resolved_durations.append(
            float(s["transition_duration_s"]) if s.get("transition_duration_s") is not None
            else default_transition_dur
        )

    any_transition = any(t is not None for t in resolved_transitions)
    _log(settings, f"ken_burns_assemble → {draft_path}"
         + (f" (transitions: {[t for t in resolved_transitions if t]})" if any_transition else ""))

    ken_burns_assemble(
        scenes_json=state.aligned_json,
        audio_path=state.audio_path,
        output_path=draft_path,
        resolution=settings.resolution,
        transitions=resolved_transitions if any_transition else None,
        transition_duration_s=resolved_durations if any_transition else None,
    )
    state.current_video = draft_path
    return plan


def stage_captions(plan: dict[str, Any], settings: Settings, state: PipelineState) -> dict[str, Any]:
    """Burn word-aligned captions over the current video.

    Breaks if: `state.current_video` doesn't advance to `*_captioned.mp4`
    when captions are enabled (i.e. plan.captions != 'skip').
    """
    if settings.skip_captions:
        return plan
    assert state.current_video is not None and state.words_path is not None
    captioned_path = str(Path(state.video_dir) / f"{settings.concept_prefix}captioned.mp4")
    _log(settings, f"burn_captions → {captioned_path}")
    burn_captions(
        video_path=state.current_video,
        words_json=state.words_path,
        output_path=captioned_path,
        caption_style=settings.caption_style,
        fontsize=settings.fontsize,
        words_per_chunk=settings.words_per_chunk,
        animation_override=settings.caption_animation_override,
        shift_s=settings.caption_shift_s,
    )
    state.current_video = captioned_path
    return plan


def stage_titles(plan: dict[str, Any], settings: Settings, state: PipelineState) -> dict[str, Any]:
    """Optionally burn section titles for any plan-declared `titles` cfg.

    Breaks if: a configured title with a valid `scene` index doesn't
    advance `state.current_video` to `*_titled.mp4`.
    """
    if not settings.titles_cfg:
        return plan
    aligned = state.aligned
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
        assert state.current_video is not None
        titled_path = str(Path(state.video_dir) / f"{settings.concept_prefix}titled.mp4")
        _log(settings, f"burn_titles → {titled_path}")
        burn_titles(
            video_path=state.current_video,
            titles=resolved_titles,
            output_path=titled_path,
            fontsize=max(12, int(72 * settings.res_scale)),
        )
        state.current_video = titled_path
    return plan


def stage_headline(plan: dict[str, Any], settings: Settings, state: PipelineState) -> dict[str, Any]:
    """Optionally burn the headline (front-loaded title text).

    Breaks if: a non-empty `headline` setting doesn't advance
    `state.current_video` to `*_final.mp4`.
    """
    if not settings.headline:
        return plan
    aligned = state.aligned
    final_path = str(Path(state.video_dir) / f"{settings.concept_prefix}final.mp4")
    _log(settings, f"burn_headline → {final_path}")
    h_fontsize = max(12, int(int(settings.headline_fontsize or 64) * settings.res_scale))
    max_chars = max(10, int(settings.video_width / (h_fontsize * 0.60)))
    headline_text = "\n".join(textwrap.wrap(settings.headline, width=max_chars))
    headline_kwargs: dict[str, Any] = {
        "video_path": state.current_video,
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
    state.current_video = final_path
    return plan


def stage_avatar(plan: dict[str, Any], settings: Settings, state: PipelineState) -> dict[str, Any]:
    """Optional avatar overlay (Aurora face track + chroma key composite).

    Breaks if: a configured avatar without a pre-keyed track skips
    chroma keying, or `state.current_video` doesn't advance to `avatar.mp4`
    when an avatar block is present in the plan.
    """
    avatar_cfg = settings.avatar_cfg
    if not avatar_cfg:
        return plan

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
        keyed_path = str(Path(state.video_dir) / "avatar_track_keyed.mov")
        _log(settings, f"key_avatar_track → {keyed_path}")
        avatar_track_keyed = key_avatar_track(
            avatar_track=avatar_track,
            chroma_key=chroma_key,
            output_path=keyed_path,
            similarity=chroma_similarity,
            blend=chroma_blend,
        )
        out_ver = Path(state.out_dir).name
        _log(
            settings,
            f"  → lock in plan to skip future chroma-key calls:\n"
            f"    avatar:\n"
            f"      avatar_track_keyed: parallax/output/{out_ver}/video/avatar_track_keyed.mov",
        )

    composite_track = avatar_track_keyed or avatar_track
    avatar_out = str(Path(state.video_dir) / "avatar.mp4")
    _log(settings, f"burn_avatar ({position}) → {avatar_out}")
    kwargs: dict[str, Any] = dict(
        video_path=state.current_video,
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
    state.current_video = avatar_out
    return plan


def stage_finalize(plan: dict[str, Any], settings: Settings, state: PipelineState) -> dict[str, Any]:
    """Rename the in-flight mp4 to `{folder.name}-vN.mp4` + snapshot the plan.

    Breaks if: `state.out_dir` doesn't end up containing both
    `{folder.name}-vN.mp4` and a `plan.yaml` snapshot, or `cost.json`
    is missing.
    """
    from .context import current_session_id

    final_out = str(Path(state.out_dir) / state.convention_name)
    if state.current_video != final_out:
        assert state.current_video is not None
        Path(state.current_video).rename(final_out)
        state.current_video = final_out

    # Snapshot the on-disk plan into the output dir — _lock_field_in_plan
    # has already written every locked path back to plan_path during the run.
    shutil.copy2(str(settings.plan_path), str(Path(state.out_dir) / "plan.yaml"))

    run_cost = settings.usage.total_cost_usd
    cost_data = {
        "run_id": settings.usage.run_id,
        "session_id": current_session_id.get(),
        "cost_usd": run_cost,
        "version": state.version,
    }
    (Path(state.out_dir) / "cost.json").write_text(json.dumps(cost_data, indent=2) + "\n")
    state.run_cost = run_cost
    return plan


# --------------------------------------------------------------------------
# Helpers used by stage_stills + stage_align (lifted from produce.py)
# --------------------------------------------------------------------------

_KNOWN_SCENE_FIELDS = {
    "index", "shot_type", "vo_text", "prompt",
    "still_path", "reference", "reference_images",
    "animate", "motion_prompt", "clip_path", "animate_resolution",
    "end_frame_path",
    # Per-scene model overrides (image_model / video_model / voice_model
    # win over plan-level defaults).
    "image_model", "video_model", "voice_model",
    # Per-scene speed override — applied uniformly across the run. See
    # stage_speed_adjust for how mixed values are rejected.
    "voice_speed",
    # video_references: character/style reference images passed to OpenRouter as
    # input_references for text-to-video consistency. Only effective when there is
    # no still_path driving frame_images (i.e. pure text-to-video scenes). Distinct
    # from reference_images, which is the image-gen still-frame reference field.
    "video_references",
    "zoom_direction", "zoom_amount",
    # Per-scene aspect override — defaults to plan.aspect when absent.
    "aspect",
    # Transitions — per-scene entry transition (xfade). None = hard cut.
    "transition", "transition_duration_s",
    # Timing overrides — null/absent = derive from VO. Future graphical editor writes here.
    "duration_s", "start_offset_s", "fade_in_s", "fade_out_s",
    # Clip trim — seek into and/or limit the source clip window.
    "clip_trim_start_s", "clip_trim_end_s",
    # Explicit re-generation signal — cleared by produce before stages run.
    "regenerate",
}


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


def _scene_to_dict(s: SceneRuntime) -> dict[str, Any]:
    """Serialize SceneRuntime to a dict, omitting None values.

    Downstream consumers (align_scenes, ken_burns_assemble) expect the same
    sparse-dict format that the old dict-based scene_entry produced — only
    fields that were explicitly set. asdict() always includes every field, so
    None values must be filtered to preserve that contract.
    """
    return {k: v for k, v in asdict(s).items() if v is not None}


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

def _wrap_stage(fn):
    """Wrap a stage callable with DEBUG `stage.<name>.start` / `.end` events.

    Display layers filter by level — full-fidelity timing always lands in
    the run.log file regardless of console verbosity.
    """
    name = fn.__name__.removeprefix("stage_")

    def wrapped(plan, settings, state):
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
            result = fn(plan, settings, state)
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
