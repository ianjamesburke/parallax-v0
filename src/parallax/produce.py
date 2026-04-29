"""Direct pipeline execution from a plan YAML — no agent, no replanning.

`parallax produce --folder <path> --plan <plan.yaml>` reads a pre-planned
scene manifest and runs: generate_image × N → generate_voiceover →
align_scenes → write_manifest → ken_burns_assemble → (optionally)
burn_captions → burn_headline.

Plan YAML schema:
  voice: Kore                # Gemini voice (default: Kore). See
                             # `parallax models show gemini-flash-tts`
                             # for the full list of prebuilt voices.
  style: rapid_fire          # Gemini TTS pacing preset. Default for ads.
                             # Options: rapid_fire | fast | calm | natural.
  style_hint: "..."          # Freeform Gemini directive (overrides `style`).
  speed: 1.0                 # ffmpeg atempo multiplier applied AFTER synthesis
                             # (default: 1.0). Use sparingly — prefer `style`
                             # for natural speed.
  model: nano-banana         # image model alias (default: mid)
  resolution: 1080x1920      # output resolution (default: 1080x1920)
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
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from . import runlog
from .context import current_session_id
from .ffmpeg_utils import parse_resolution
from .log import get_logger
from .settings import ProductionMode, _infer_project_resolution, resolve_settings
from .stages import STAGES, stage_scan, stage_stills

log = get_logger("produce")


def run_plan(folder: str | Path, plan_path: str | Path, aspect: str | None = None) -> int:
    """Run the full plan-driven production pipeline.

    `aspect`, when provided, overrides `plan.aspect` before settings are
    resolved. None means "use plan.aspect, fall back to 9:16".
    """
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

    if aspect is not None:
        plan["aspect"] = aspect
        # CLI override means "render at this aspect" — drop any plan-pinned
        # `resolution` so it gets re-derived from the new aspect. Otherwise
        # a 16:9 override against a `resolution: 1080x1920` plan would mix
        # the new aspect into a portrait frame.
        plan.pop("resolution", None)

    scenes_raw: list[dict[str, Any]] = plan.get("scenes", [])
    if not scenes_raw:
        print("Error: plan has no scenes", file=sys.stderr)
        return 1

    try:
        settings = resolve_settings(plan, folder, plan_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    settings.events("log", {"msg": f"project resolution: {settings.resolution}"})

    # Pre-flight: in real-mode, check OpenRouter credits before any work runs.
    # 402 mid-pipeline is brutal (partial spend on stills, then dies on i2v);
    # checking once at the top fails loud, fails early.
    if settings.mode == ProductionMode.REAL:
        try:
            from . import openrouter
            balance = openrouter.check_credits(min_balance_usd=0.50)
            settings.events("log", {
                "msg": f"openrouter credits: ${balance.remaining:.2f} remaining "
                       f"(${balance.total:.2f} total, ${balance.used:.2f} used)"
            })
        except openrouter.InsufficientCreditsError as e:
            print(f"\nError: {e}\n", file=sys.stderr)
            return 1
        except Exception as e:
            settings.events("log", {
                "msg": f"WARNING: credits pre-flight check failed ({type(e).__name__}: {e}); "
                       f"continuing — first OpenRouter call will surface the real error"
            })

    import uuid as _uuid
    current_session_id.set(f"produce-{_uuid.uuid4().hex[:8]}")
    run_id = runlog.start_run()
    settings.usage.bind(run_id)
    runlog.event(
        "plan.loaded",
        folder=str(settings.folder), plan_path=str(settings.plan_path),
        scene_count=len(scenes_raw), model=settings.model, voice=settings.voice,
        resolution=settings.resolution,
        test_mode=settings.mode == ProductionMode.TEST,
    )
    settings.events("log", {"msg": f"run_id: {run_id}  →  parallax tail {run_id}"})

    # `stills_only` short-circuits after stage_stills with its own end-of-run
    # path — no audio/video stages, no convention rename, no full mp4.
    if settings.stills_only:
        plan = stage_scan(plan, settings)
        plan = stage_stills(plan, settings)
        rt = plan["_runtime"]
        settings.events("log", {"msg": "stills_only — skipping audio, video, and assembly stages"})
        run_cost = settings.usage.total_cost_usd
        cost_data = {
            "run_id": run_id,
            "session_id": current_session_id.get(),
            "cost_usd": run_cost,
            "version": rt["version"],
        }
        (Path(rt["out_dir"]) / "cost.json").write_text(json.dumps(cost_data, indent=2) + "\n")
        print(f"\n✓ stills → {rt['stills_dir']}", flush=True)
        runlog.end_run(status="ok", final_video=rt["stills_dir"], cost_usd=run_cost)
        return 0

    for stage in STAGES:
        plan = stage(plan, settings)

    rt = plan["_runtime"]
    print(f"\n✓ {rt['current_video']}", flush=True)
    runlog.end_run(status="ok", final_video=str(rt["current_video"]), cost_usd=rt["run_cost"])
    return 0


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
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=duration", "-of", "csv=p=0", str(src)],
            capture_output=True, text=True,
        )
        duration = float(probe.stdout.strip() or "5.0")
        w_i, h_i = parse_resolution(resolution)
        w, h = str(w_i), str(h_i)
        from .assembly import _zoom_filter
        from .ffmpeg_utils import _get_ffmpeg
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
        from .assembly import _make_kb_clip
        _make_kb_clip(str(src), duration, out_path, resolution=resolution,
                      scene_index=scene_index, zoom_direction=zoom_dir, zoom_amount=zoom_amount)
    else:
        print(f"Error: scene {scene_index} has no clip_path or still_path", file=sys.stderr)
        return 1

    print(f"✓ {out_path}", flush=True)
    return 0
