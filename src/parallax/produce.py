"""Direct pipeline execution from a plan YAML — no agent, no replanning.

`parallax produce --folder <path> --plan <plan.yaml>` reads a pre-planned
scene manifest and runs: generate_image × N → generate_voiceover →
align_scenes → write_manifest → ken_burns_assemble → (optionally)
burn_captions → burn_headline.

Plan YAML schema:
  voice: nova                # TTS voice (default: nova). See
                             # `parallax models show tts-mini`
                             # for the full list of prebuilt voices.
  voice_model: tts-mini      # TTS model alias (default: tts-mini).
  style: rapid_fire          # TTS pacing preset. Default for ads.
                             # Options: rapid_fire | fast | calm | natural.
  style_hint: "..."          # Freeform TTS directive (overrides `style`).
  voice_speed: 1.0           # ffmpeg atempo multiplier applied AFTER synthesis
                             # (default: 1.0). Use sparingly — prefer `style`
                             # for natural speed.
  image_model: nano-banana   # image model alias (default: mid)
  video_model: kling         # video model alias (default: mid)
  resolution: 1080x1920      # final output resolution (default: 1080x1920)
  animate_resolution: 480x854  # resolution sent to the video-gen model (default: 480x854
                               # for 9:16). Clips are upscaled to `resolution:` by ffmpeg
                               # during assembly. Lower = cheaper.
                               # Seedance 2.0 Fast: 480p=$0.054/s, 720p=$0.121/s, 1080p=$0.272/s
                               # Set per-scene to override a single clip.
  caption_style: bangers
  captions: skip             # omit to enable captions
  headline: THE TITLE        # omit to skip headline
  character_image: parallax/scratch/ref.png  # relative to --folder

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
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import runlog
from .context import current_session_id
from .ffmpeg_utils import parse_resolution, probe_duration, run_ffmpeg
from .log import get_logger
from .plan import Plan
from .settings import ProductionMode, _infer_project_resolution, resolve_settings, with_run_id
from .stages import STAGES, PipelineState, _wrap_stage, stage_scan, stage_stills

log = get_logger("produce")


@dataclass
class ProductionResult:
    status: str  # "ok" | "error"
    run_id: str | None
    output_dir: Path | None
    final_video: Path | None
    stills_dir: Path | None
    cost_usd: float
    error: str | None


def _apply_regenerate_flags(plan: dict, plan_path: Path) -> None:
    """Clear all asset locks on scenes marked `regenerate: true`, then remove the flag.

    Writes the cleaned plan back to disk so the next run starts with no stale locks.
    Only per-scene asset lock fields are cleared: still_path, clip_path, end_frame_path.
    Top-level audio_path/words_path are NOT cleared — those are plan-wide locks.
    """
    _SCENE_LOCK_FIELDS = ("still_path", "clip_path", "end_frame_path")
    changed = False
    for scene in plan.get("scenes", []):
        if scene.get("regenerate"):
            for f in _SCENE_LOCK_FIELDS:
                if f in scene:
                    del scene[f]
                    changed = True
            del scene["regenerate"]
            changed = True

    if not changed:
        return

    # Write the cleaned plan back to disk so it persists across runs.
    try:
        with plan_path.open("r", encoding="utf-8") as f:
            disk_plan = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return

    for disk_scene in disk_plan.get("scenes", []):
        if disk_scene.get("regenerate"):
            for f in _SCENE_LOCK_FIELDS:
                disk_scene.pop(f, None)
            disk_scene.pop("regenerate", None)

    with plan_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(disk_plan, f, default_flow_style=False, allow_unicode=True,
                       sort_keys=False, width=10000)


def _init_vo_text_hashes(plan: dict, plan_path: Path) -> None:
    """Store vo_text hashes when audio_path is first locked.

    Called once per run before preflight. If audio_path is set and
    vo_text_hashes is absent, compute and write hashes to disk so subsequent
    runs can detect vo_text drift and warn.
    """
    import hashlib as _hashlib

    if not plan.get("audio_path"):
        return
    if plan.get("vo_text_hashes"):
        return

    hashes = {
        str(s["index"]): _hashlib.sha256(s.get("vo_text", "").encode()).hexdigest()[:16]
        for s in plan.get("scenes", [])
        if "index" in s
    }
    plan["vo_text_hashes"] = hashes

    try:
        with plan_path.open("r", encoding="utf-8") as f:
            disk_plan = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return

    disk_plan["vo_text_hashes"] = hashes
    with plan_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(disk_plan, f, default_flow_style=False, allow_unicode=True,
                       sort_keys=False, width=10000)


def run_plan(
    folder: str | Path,
    plan_path: str | Path,
    aspect: str | None = None,
    mode: "ProductionMode | None" = None,
    yes: bool = False,
) -> ProductionResult:
    """Run the full plan-driven production pipeline.

    `aspect`, when provided, overrides `plan.aspect` before settings are
    resolved. None means "use plan.aspect, fall back to 9:16".
    """
    folder = Path(folder).expanduser().resolve()
    plan_path = Path(plan_path).expanduser().resolve()

    if not folder.is_dir():
        return ProductionResult(status="error", run_id=None, output_dir=None,
                                final_video=None, stills_dir=None, cost_usd=0.0,
                                error=f"folder not found: {folder}")
    if not plan_path.is_file():
        return ProductionResult(status="error", run_id=None, output_dir=None,
                                final_video=None, stills_dir=None, cost_usd=0.0,
                                error=f"plan file not found: {plan_path}")

    try:
        typed_plan: Plan = Plan.from_yaml(plan_path)
    except Exception as e:
        return ProductionResult(status="error", run_id=None, output_dir=None,
                                final_video=None, stills_dir=None, cost_usd=0.0,
                                error=f"invalid plan {plan_path}:\n{e}")

    # The mutable stage blackboard stays dict-shaped so stages can write
    # _runtime without touching the frozen Pydantic model.
    plan: dict[str, Any] = typed_plan.to_dict()

    _apply_regenerate_flags(plan, plan_path)
    _init_vo_text_hashes(plan, plan_path)

    if aspect is not None:
        plan["aspect"] = aspect
        # CLI override means "render at this aspect" — drop any plan-pinned
        # `resolution` so it gets re-derived from the new aspect. Otherwise
        # a 16:9 override against a `resolution: 1080x1920` plan would mix
        # the new aspect into a portrait frame.
        plan.pop("resolution", None)

    scenes_raw: list[dict[str, Any]] = plan.get("scenes", [])
    if not scenes_raw:
        return ProductionResult(status="error", run_id=None, output_dir=None,
                                final_video=None, stills_dir=None, cost_usd=0.0,
                                error="plan has no scenes")

    # Pass the typed Plan to resolve_settings so it reads validated fields
    # directly. When an aspect CLI override is present, fall back to the dict
    # path (aspect has been mutated and resolution dropped on the dict above).
    settings_input: Plan | dict[str, Any] = plan if aspect is not None else typed_plan
    try:
        settings = resolve_settings(settings_input, folder, plan_path, mode=mode)
    except FileNotFoundError as e:
        return ProductionResult(status="error", run_id=None, output_dir=None,
                                final_video=None, stills_dir=None, cost_usd=0.0,
                                error=str(e))
    settings.events("log", {"msg": f"project resolution: {settings.resolution}"})

    from .shim import _test_mode_override as _tmo
    _tmo_token = _tmo.set(settings.mode == ProductionMode.TEST)

    try:
        # Pre-flight: in real-mode, check OpenRouter credits before any work runs.
        # 402 mid-pipeline is brutal (partial spend on stills, then dies on i2v);
        # checking once at the top fails loud, fails early.
        _credits_balance: float | None = None
        if settings.mode == ProductionMode.REAL:
            from . import openrouter
            try:
                balance = openrouter.check_credits(min_balance_usd=0.50)
                _credits_balance = balance.remaining
                settings.events("log", {
                    "msg": f"openrouter credits: ${balance.remaining:.2f} remaining "
                           f"(${balance.total:.2f} total, ${balance.used:.2f} used)"
                })
            except openrouter.InsufficientCreditsError as e:
                return ProductionResult(status="error", run_id=None, output_dir=None,
                                        final_video=None, stills_dir=None, cost_usd=0.0,
                                        error=str(e))
            except Exception as e:
                settings.events("log", {
                    "msg": f"WARNING: credits pre-flight check failed ({type(e).__name__}: {e}); "
                           f"continuing — first OpenRouter call will surface the real error"
                })

        from .preflight import compute_preflight, prompt_proceed as _prompt_proceed
        _pf_result = compute_preflight(plan, balance_usd=_credits_balance, folder=folder, output_resolution=settings.resolution)
        if not _prompt_proceed(_pf_result, yes=yes):
            return ProductionResult(
                status="cancelled", run_id=None, output_dir=None,
                final_video=None, stills_dir=None, cost_usd=0.0, error=None,
            )

        import uuid as _uuid
        current_session_id.set(f"produce-{_uuid.uuid4().hex[:8]}")
        run_id = runlog.start_run()
        settings.usage.bind(run_id)
        # Settings is frozen — swap the whole struct so stages see the run_id.
        settings = with_run_id(settings, run_id)
        runlog.record_run_meta(plan_path=str(settings.plan_path), scene_count=len(scenes_raw))
        runlog.event(
            "plan.loaded",
            folder=str(settings.folder), plan_path=str(settings.plan_path),
            scene_count=len(scenes_raw),
            image_model=settings.image_model, video_model=settings.video_model,
            voice=settings.voice, voice_model=settings.voice_model,
            resolution=settings.resolution,
            test_mode=settings.mode == ProductionMode.TEST,
        )
        runlog.event(
            "run.preflight",
            scenes=[
                {
                    "index": s.index,
                    "kind": s.kind,
                    "model": s.model_alias,
                    "locked": s.locked,
                    "cost_usd": s.cost_usd,
                    **({"duration_s": s.duration_s} if s.kind == "clip" else {}),
                }
                for s in _pf_result.scenes
            ],
            voiceover_model=_pf_result.voiceover_model,
            voiceover_locked=_pf_result.voiceover_locked,
            estimated_cost_usd=_pf_result.estimated_total_usd,
            balance_usd=_pf_result.balance_usd,
        )
        settings.events("log", {"msg": f"run_id: {run_id}  →  parallax log {run_id}"})

        # `stills_only` short-circuits after stage_stills with its own end-of-run
        # path — no audio/video stages, no convention rename, no full mp4.
        if settings.stills_only:
            state = PipelineState()
            plan = _wrap_stage(stage_scan)(plan, settings, state)
            plan = _wrap_stage(stage_stills)(plan, settings, state)
            settings.events("log", {"msg": "stills_only — skipping audio, video, and assembly stages"})
            run_cost = settings.usage.total_cost_usd
            cost_data = {
                "run_id": run_id,
                "session_id": current_session_id.get(),
                "cost_usd": run_cost,
                "version": state.version,
            }
            (Path(state.out_dir) / "cost.json").write_text(json.dumps(cost_data, indent=2) + "\n")
            runlog.end_run(status="ok", final_video=state.stills_dir)
            return ProductionResult(
                status="ok",
                run_id=run_id,
                output_dir=Path(state.out_dir),
                final_video=None,
                stills_dir=Path(state.stills_dir),
                cost_usd=run_cost,
                error=None,
            )

        state = PipelineState()
        for stage in STAGES:
            plan = stage(plan, settings, state)

        runlog.end_run(status="ok", final_video=str(state.current_video))
        return ProductionResult(
            status="ok",
            run_id=run_id,
            output_dir=Path(state.out_dir),
            final_video=Path(state.current_video) if state.current_video else None,
            stills_dir=None,
            cost_usd=settings.usage.total_cost_usd,
            error=None,
        )
    finally:
        _tmo.reset(_tmo_token)


def test_scene(folder: str | Path, plan_path: str | Path, scene_index: int, aspect: str | None = None) -> int:
    """Apply the video filter for one scene and open the result — no full pipeline.

    `aspect`, when provided, overrides `plan.aspect` for the duration of this call.
    """
    folder = Path(folder).expanduser().resolve()
    plan_path = Path(plan_path).expanduser().resolve()

    if not plan_path.is_file():
        print(f"Error: plan not found: {plan_path}", file=sys.stderr)
        return 1

    with plan_path.open() as f:
        plan: dict[str, Any] = yaml.safe_load(f)

    if aspect is not None:
        plan["aspect"] = aspect

    plan_aspect = plan.get("aspect", "9:16")
    resolution = plan.get("resolution") or _infer_project_resolution(plan, folder, plan_aspect)
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
        duration = probe_duration(src) or 5.0
        w_i, h_i = parse_resolution(resolution)
        w, h = str(w_i), str(h_i)
        from .assembly import _zoom_filter
        from .ffmpeg_utils import _get_ffmpeg
        ffmpeg = _get_ffmpeg()
        vf = _zoom_filter(zoom_dir, zoom_amount, duration, w, h)
        run_ffmpeg(
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
        from .assembly import _make_kb_clip
        _make_kb_clip(str(src), duration, out_path, resolution=resolution,
                      scene_index=scene_index, zoom_direction=zoom_dir, zoom_amount=zoom_amount)
    else:
        print(f"Error: scene {scene_index} has no clip_path or still_path", file=sys.stderr)
        return 1

    print(f"✓ {out_path}", flush=True)
    return 0
