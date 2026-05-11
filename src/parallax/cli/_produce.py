from __future__ import annotations

import re
import sys
from typing import Optional

import typer


_RESOLUTION_RE = re.compile(r"^\d+x\d+$")


def _validate_resolution(resolution: Optional[str]) -> None:
    """Raise ValueError if resolution is non-None and not in WxH format."""
    if resolution is None:
        return
    if not _RESOLUTION_RE.match(resolution):
        raise ValueError(
            f"invalid resolution '{resolution}' — expected WxH format, e.g. 480x854"
        )


def register_produce(app: typer.Typer) -> None:
    app.command("produce")(_produce_cmd)
    app.command("plan")(_plan_cmd)
    app.command("ingest")(_ingest_cmd)


def _produce_cmd(
    folder: str = typer.Option(..., "--folder", help="Path to the project folder."),
    plan: Optional[str] = typer.Option(None, "--plan", help="Path to a plan YAML file with scenes, prompts, voice, and model settings."),
    brief: Optional[str] = typer.Option(None, "--brief", help="Path to a brief.yaml. Materialized into plan.yaml via the planner first."),
    aspect: str = typer.Option("9:16", "--aspect", help="Output aspect ratio. Overrides plan.aspect when set. Default: 9:16."),
    scene: Optional[int] = typer.Option(None, "--scene", help="If set, render only this scene index (no full pipeline)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the pre-flight confirmation prompt (non-interactive mode)."),
    hq: bool = typer.Option(False, "--hq", help="Use premium-tier models."),
    debug: int = typer.Option(0, "--debug", help="Burn debug overlay on every scene: 1=scene index, 2=+prompt, 3=+refs. Default: 0 (off)."),
    resolution: Optional[str] = typer.Option(None, "--resolution", help="Override output resolution, e.g. 480x854 or 1080x1920."),
) -> int:
    if plan is None and brief is None:
        typer.echo("Error: one of --plan or --brief is required", err=True)
        return 2
    if plan is not None and brief is not None:
        typer.echo("Error: --plan and --brief are mutually exclusive", err=True)
        return 2
    valid_aspects = ("9:16", "16:9", "1:1", "4:3", "3:4")
    if aspect not in valid_aspects:
        typer.echo(f"Error: invalid aspect ratio '{aspect}'. Choose from: {', '.join(valid_aspects)}", err=True)
        return 2
    if debug not in (0, 1, 2, 3):
        typer.echo("Error: --debug must be 0, 1, 2, or 3", err=True)
        return 2
    try:
        _validate_resolution(resolution)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        return 2
    return _run_produce(folder, plan, brief, aspect, scene, yes, hq, debug, resolution)


def _plan_cmd(
    folder: str = typer.Option(..., "--folder", help="Project root."),
    brief: Optional[str] = typer.Option(None, "--brief", help="Path to brief.yaml (default: <folder>/brief.yaml)."),
    out: Optional[str] = typer.Option(None, "--out", help="Override plan.yaml output path."),
    model: str = typer.Option("mid", "--model", help="Image model alias for the plan (default: mid)."),
    caption_style: str = typer.Option("anton", "--caption-style", help="Caption preset name written into the plan (default: anton)."),
    resolution: Optional[str] = typer.Option(None, "--resolution", help="Set output resolution in the generated plan, e.g. 480x854."),
) -> int:
    try:
        _validate_resolution(resolution)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        return 2
    return _run_plan_command(folder, brief, out, model, caption_style, resolution)


def _ingest_cmd(
    path: str = typer.Argument(..., help="Clip file or directory of clips."),
    out: Optional[str] = typer.Option(None, "--out", help="Override the index.json output path."),
    visual: bool = typer.Option(False, "--visual", help="Also tag sampled frames via vision (currently not implemented)."),
    estimate: bool = typer.Option(False, "--estimate", help="Dry-run: report duration + cost estimate, no transcription."),
    parallel: int = typer.Option(4, "--parallel", help="Max concurrent transcription workers (default: 4)."),
) -> int:
    return _run_ingest_command(path, out, visual, estimate, parallel)


def _inject_resolution(plan_path: str, resolution: str) -> None:
    """Load plan YAML, set resolution:, write back."""
    import yaml as _yaml
    from pathlib import Path as _Path

    p = _Path(plan_path)
    data = _yaml.safe_load(p.read_text()) or {}
    data["resolution"] = resolution
    p.write_text(_yaml.dump(data, sort_keys=False, allow_unicode=True))


def _run_produce(
    folder: str,
    plan: Optional[str],
    brief: Optional[str],
    aspect: str,
    scene: Optional[int],
    yes: bool,
    hq: bool,
    debug: int,
    resolution: Optional[str] = None,
) -> int:
    from pathlib import Path
    from ..produce import run_plan, test_scene

    if brief is not None:
        from ..planner import plan_from_brief

        brief_path = Path(brief).expanduser()
        if not brief_path.is_file():
            print(f"Error: brief not found: {brief_path}", file=sys.stderr)
            return 1
        try:
            result = plan_from_brief(brief_path, folder=folder)
        except Exception as e:
            print(f"Error: failed to plan from brief: {e}", file=sys.stderr)
            return 1
        if not result.ok:
            print(
                f"✗ {len(result.missing_assets)} required asset(s) missing — "
                f"see {result.questions_path}",
                file=sys.stderr,
            )
            for p in result.missing_assets:
                print(f"  - {p}", file=sys.stderr)
            return 1
        plan_path = str(result.plan_path)
    else:
        assert plan is not None  # validated: brief/plan mutex, at least one is required
        plan_path = plan

    if resolution is not None:
        _inject_resolution(plan_path, resolution)

    if scene is not None:
        return test_scene(
            folder=folder,
            plan_path=plan_path,
            scene_index=scene,
            aspect=aspect,
        )
    try:
        result = run_plan(
            folder=folder, plan_path=plan_path,
            aspect=aspect, yes=yes,
            hq=hq,
            debug_level=debug,
        )
    except Exception as e:
        print(f"\nError: {type(e).__name__}: {e}\n", file=sys.stderr)
        return 1
    if result.status == "cancelled":
        print("produce cancelled.", flush=True)
        return 0
    if result.status != "ok":
        print(f"\nError: {result.error}\n", file=sys.stderr)
        return 1
    if result.stills_dir:
        print(f"\n✓ stills → {result.stills_dir}", flush=True)
    else:
        print(f"\n✓ {result.final_video}", flush=True)
    return 0


def _run_plan_command(
    folder: str,
    brief: Optional[str],
    out: Optional[str],
    model: str,
    caption_style: str,
    resolution: Optional[str] = None,
) -> int:
    from pathlib import Path
    from ..planner import plan_from_brief

    folder_path = Path(folder).expanduser()
    brief_path = (
        Path(brief).expanduser()
        if brief is not None
        else folder_path / "brief.yaml"
    )
    if not brief_path.is_file():
        print(f"Error: brief not found: {brief_path}", file=sys.stderr)
        return 1

    try:
        result = plan_from_brief(
            brief_path,
            folder=folder_path,
            out_path=out,
            image_model=model,
            caption_style=caption_style,
        )
    except Exception as e:
        print(f"Error: failed to plan from brief: {e}", file=sys.stderr)
        return 1

    if not result.ok:
        print(
            f"✗ {len(result.missing_assets)} required asset(s) missing — "
            f"see {result.questions_path}",
            file=sys.stderr,
        )
        for p in result.missing_assets:
            print(f"  - {p}", file=sys.stderr)
        return 1

    if resolution is not None:
        _inject_resolution(str(result.plan_path), resolution)

    print(f"✓ Wrote plan.yaml ({result.scene_count} scenes) → {result.plan_path}")
    return 0


def _run_ingest_command(
    path: str,
    out: Optional[str],
    visual: bool,
    estimate: bool,
    parallel: int,
) -> int:
    from ..ingest import ingest

    try:
        result = ingest(
            path,
            out_path=out,
            visual=visual,
            estimate=estimate,
            parallel=parallel,
        )
    except NotImplementedError:
        print("Error: --visual is not implemented yet; rerun without it.", file=sys.stderr)
        return 1
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if estimate:
        print(
            f"{len(result.clips)} clips, {result.total_duration_s:.1f}s total, "
            f"est cost ${result.estimated_cost_usd:.2f}"
        )
        return 0
    print(
        f"✓ Indexed {len(result.clips)} clips ({result.total_duration_s:.1f}s) → "
        f"{result.index_path}"
    )
    return 0
