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
    app.command("storyboard")(_storyboard_cmd)
    app.command("ingest")(_ingest_cmd)


def _produce_cmd(
    folder: str = typer.Option(..., "--folder", help="Path to the project folder."),
    plan: Optional[str] = typer.Option(None, "--plan", help="Path to a plan YAML file with scenes, prompts, voice, and model settings."),
    brief: Optional[str] = typer.Option(None, "--brief", help="Path to a brief.yaml. Materialized into plan.yaml via the planner first."),
    aspect: str = typer.Option("9:16", "--aspect", help="Output aspect ratio. Overrides plan.aspect when set. Default: 9:16."),
    scene: Optional[int] = typer.Option(None, "--scene", help="If set, render only this scene index (no full pipeline)."),
    hq: bool = typer.Option(False, "--hq", help="Use premium-tier models."),
    debug: int = typer.Option(0, "--debug", help="Burn debug overlay on every scene: 1=scene index, 2=+prompt, 3=+script and motion. Default: 0 (off)."),
    resolution: Optional[str] = typer.Option(None, "--resolution", help="Override output resolution, e.g. 480x854 or 1080x1920."),
    stills_only: bool = typer.Option(False, "--stills-only", help="Generate stills only (writes stills_only: true into plan); skips voiceover, animation, and assembly."),
) -> int:
    """Generate stills, narration, and video from a plan or brief. Spends money; prints the estimated cost first; refuses while reference gates are open."""
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
    return _run_produce(folder, plan, brief, aspect, scene, hq, debug, resolution, stills_only=stills_only)


def _plan_cmd(
    folder: str = typer.Option(..., "--folder", help="Project root."),
    brief: Optional[str] = typer.Option(None, "--brief", help="Path to brief.yaml (default: <folder>/brief.yaml)."),
    out: Optional[str] = typer.Option(None, "--out", help="Override plan.yaml output path."),
    model: str = typer.Option("mid", "--model", help="Image model alias for the plan (default: mid)."),
    caption_style: str = typer.Option("anton", "--caption-style", help="Caption preset name written into the plan (default: anton)."),
    resolution: Optional[str] = typer.Option(None, "--resolution", help="Set output resolution in the generated plan, e.g. 480x854."),
    stills_only: bool = typer.Option(False, "--stills-only", help="Generate stills only (writes stills_only: true into plan); skips voiceover, animation, and assembly."),
) -> int:
    """Materialize brief.yaml into plan.yaml. No provider calls."""
    try:
        _validate_resolution(resolution)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        return 2
    return _run_plan_command(folder, brief, out, model, caption_style, resolution, stills_only=stills_only)


def _storyboard_cmd(
    folder: str = typer.Option(..., "--folder", help="Project root for resolving assets."),
    brief: str = typer.Option(..., "--brief", help="Path to the authored brief.yaml."),
    timing: Optional[str] = typer.Option(None, "--timing", help="Aligned manifest YAML/JSON with index, start_s, end_s for every scene; replaces reading estimates."),
    package_dir: Optional[str] = typer.Option(None, "--package-dir", help="Package output directory (default: <folder>/storyboard-package)."),
    check: bool = typer.Option(False, "--check", help="Print the status, open gates, and advisories as JSON without writing a package."),
    as_json: bool = typer.Option(False, "--json", help="Print the handoff manifest as JSON: status, open reference gates with choices and resolving commands, advisories."),
) -> int:
    """Write a portable storyboard package and its reference gates. No provider calls."""
    import json
    from pathlib import Path
    from ..storyboard_package import check_storyboard, write_package
    if check:
        try:
            typer.echo(json.dumps(check_storyboard(brief, folder, timing_path=timing), indent=2))
        except (ValueError, OSError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            return 2
        return 0
    destination = Path(package_dir) if package_dir else Path(folder) / "storyboard-package"
    try:
        manifest = write_package(brief, folder, destination, timing_path=timing)
    except (ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        return 2
    package = destination.expanduser().resolve()
    manifest = {"package_dir": str(package), "html": str(package / "storyboard.html"), **manifest}
    if as_json:
        typer.echo(json.dumps(manifest, indent=2))
        return 0
    typer.echo(f"Storyboard package: {manifest['package_dir']}")
    typer.echo(f"Open: {manifest['html']}")
    if manifest["gates"]:
        typer.echo(f"{len(manifest['gates'])} reference gate(s) open; run with --json for choices and commands.")
    typer.echo("Review scene types and direction before generating assets. No provider calls made.")
    return 0


def _ingest_cmd(
    path: str = typer.Argument(..., help="Clip file or directory of clips."),
    out: Optional[str] = typer.Option(None, "--out", help="Override the index.json output path."),
    visual: bool = typer.Option(False, "--visual", help="Also tag sampled frames via vision (currently not implemented)."),
    estimate: bool = typer.Option(False, "--estimate", help="Dry-run: report duration + cost estimate, no transcription."),
    parallel: int = typer.Option(4, "--parallel", help="Max concurrent transcription workers (default: 4)."),
) -> int:
    """Index existing footage into a searchable JSON with word timestamps."""
    return _run_ingest_command(path, out, visual, estimate, parallel)


def _inject_resolution(plan_path: str, resolution: str) -> None:
    """Load plan YAML, set resolution:, write back."""
    import yaml as _yaml
    from pathlib import Path as _Path

    p = _Path(plan_path)
    data = _yaml.safe_load(p.read_text()) or {}
    data["resolution"] = resolution
    p.write_text(_yaml.dump(data, sort_keys=False, allow_unicode=True))


def _inject_stills_only(plan_path: str) -> None:
    """Load plan YAML, set stills_only: true, write back."""
    import yaml as _yaml
    from pathlib import Path as _Path

    p = _Path(plan_path)
    data = _yaml.safe_load(p.read_text()) or {}
    data["stills_only"] = True
    p.write_text(_yaml.dump(data, sort_keys=False, allow_unicode=True))


def _refuse_open_gates(brief_path, folder: str) -> int | None:
    """Return exit code 1 when the brief has open reference gates; nothing may spend until a human answers them."""
    from ..brief import Brief
    from ..reference_gates import open_gates

    try:
        gates = open_gates(Brief.from_yaml(brief_path), folder, brief_path)
    except Exception as e:
        print(f"Error: failed to read brief {brief_path}: {e}", file=sys.stderr)
        return 1
    if gates:
        lines = []
        for gate in gates:
            if gate["state"] == "authorized_pending_generation":
                attach = next(c["command"] for c in gate["choices"] if c["id"] == "generated_anchor")
                lines.append(f"  {gate['id']} (scenes {', '.join(f'{i:02d}' for i in gate['scenes'])}): anchor authorized ({'; '.join(sorted(set(gate['authorized_scenes'].values())))}) but not generated or attached. "
                             f"Generate it, then run: {attach}")
            else:
                lines.append(f"  {gate['id']} (scenes {', '.join(f'{i:02d}' for i in gate['scenes'])}): a human must answer it")
        print(
            f"Error: {len(gates)} reference gate(s) block generation:\n" + "\n".join(lines) +
            f"\nSee: parallax storyboard --folder {folder} --brief {brief_path} --json",
            file=sys.stderr,
        )
        return 1
    return None


def _refuse_plan_open_gates(plan_path: str, folder: str) -> int | None:
    """A plan materialized from a brief carries `source_brief`; that brief's gates still apply."""
    from pathlib import Path
    import yaml

    try:
        source = (yaml.safe_load(Path(plan_path).expanduser().read_text(encoding="utf-8")) or {}).get("source_brief")
    except (OSError, yaml.YAMLError, AttributeError) as e:
        print(f"Error: failed to read plan {plan_path}: {e}", file=sys.stderr)
        return 1
    if source is None:
        return None
    brief_path = Path(source).expanduser()
    if not brief_path.is_absolute():
        brief_path = Path(folder).expanduser() / brief_path
    if not brief_path.is_file():
        print(f"Error: plan {plan_path} was made from brief {brief_path}, which no longer exists; "
              "restore it or re-plan from the current brief so its reference gates can be checked.", file=sys.stderr)
        return 1
    return _refuse_open_gates(str(brief_path), folder)


def _run_produce(
    folder: str,
    plan: Optional[str],
    brief: Optional[str],
    aspect: str,
    scene: Optional[int],
    hq: bool,
    debug: int,
    resolution: Optional[str] = None,
    *,
    stills_only: bool = False,
) -> int:
    from pathlib import Path
    from ..produce import run_plan, test_scene

    if brief is not None:
        from ..planner import plan_from_brief

        brief_path = Path(brief).expanduser()
        if not brief_path.is_file():
            print(f"Error: brief not found: {brief_path}", file=sys.stderr)
            return 1
        if (code := _refuse_open_gates(brief_path, folder)) is not None:
            return code
        try:
            result = plan_from_brief(brief_path, folder=folder, stills_only=stills_only)
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
        if (code := _refuse_plan_open_gates(plan_path, folder)) is not None:
            return code
        if stills_only:
            _inject_stills_only(plan_path)

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
            aspect=aspect,
            hq=hq,
            debug_level=debug,
        )
    except Exception as e:
        print(f"\nError: {type(e).__name__}: {e}\n", file=sys.stderr)
        return 1
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
    *,
    stills_only: bool = False,
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
            stills_only=stills_only,
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
