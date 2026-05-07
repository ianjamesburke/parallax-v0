from __future__ import annotations

import argparse
import sys


def register_parser(sub: argparse._SubParsersAction) -> None:
    produce_p = sub.add_parser(
        "produce",
        help="Run a plan.yaml or brief.yaml end-to-end.",
        description=(
            "Run from a plan.yaml (--plan) OR a brief.yaml (--brief). "
            "Briefs are materialized into a plan first, then produced."
        ),
    )
    produce_p.add_argument("--folder", required=True, help="Path to the project folder.")
    produce_src = produce_p.add_mutually_exclusive_group(required=True)
    produce_src.add_argument(
        "--plan",
        help="Path to a plan YAML file with scenes, prompts, voice, and model settings.",
    )
    produce_src.add_argument(
        "--brief",
        help="Path to a brief.yaml. Materialized into plan.yaml via the planner first.",
    )
    produce_p.add_argument(
        "--aspect",
        choices=("9:16", "16:9", "1:1", "4:3", "3:4"),
        default="9:16",
        help="Output aspect ratio. Overrides plan.aspect when set. Default: 9:16.",
    )
    produce_p.add_argument(
        "--scene", type=int, default=None,
        help="If set, render only this scene index (no full pipeline). "
             "Scene must have clip_path or still_path in the plan.",
    )
    produce_p.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip the pre-flight confirmation prompt (non-interactive mode).",
    )
    produce_p.add_argument(
        "--hq", action="store_true",
        help="Use premium-tier models: image=premium (Gemini 3 Pro), video=mid (Kling). "
             "Per-scene model overrides in the plan still take precedence.",
    )
    produce_p.add_argument(
        "--debug", type=int, choices=(0, 1, 2, 3), default=0, metavar="N",
        help="Burn debug overlay on every scene: 1=scene index, 2=+prompt, 3=+refs. Default: 0 (off).",
    )

    plan_p = sub.add_parser("plan", help="Translate a brief.yaml into a plan.yaml.")
    plan_p.add_argument("--folder", required=True, help="Project root.")
    plan_p.add_argument(
        "--brief", default=None,
        help="Path to brief.yaml (default: <folder>/brief.yaml).",
    )
    plan_p.add_argument(
        "--out", default=None,
        help="Override plan.yaml output path (default: <folder>/parallax/scratch/plan.yaml).",
    )
    plan_p.add_argument("--model", default="mid", help="Image model alias for the plan (default: mid).")
    plan_p.add_argument(
        "--caption-style", default="anton",
        help="Caption preset name written into the plan (default: anton).",
    )

    ingest_p = sub.add_parser("ingest", help="Index footage into a searchable JSON.")
    ingest_p.add_argument("path", help="Clip file or directory of clips.")
    ingest_p.add_argument("--out", default=None, help="Override the index.json output path.")
    ingest_p.add_argument(
        "--visual", action="store_true",
        help="Also tag sampled frames via vision (currently not implemented).",
    )
    ingest_p.add_argument(
        "--estimate", action="store_true",
        help="Dry-run: report duration + cost estimate, no transcription.",
    )
    ingest_p.add_argument(
        "--parallel", type=int, default=4,
        help="Max concurrent transcription workers (default: 4).",
    )


def run(args) -> int:
    if args.command == "produce":
        return _run_produce(args)
    if args.command == "plan":
        return _run_plan_command(args)
    if args.command == "ingest":
        return _run_ingest_command(args)
    return 2


def _run_produce(args) -> int:
    from pathlib import Path
    from ..produce import run_plan, test_scene

    if args.brief is not None:
        from ..planner import plan_from_brief

        brief_path = Path(args.brief).expanduser()
        if not brief_path.is_file():
            print(f"Error: brief not found: {brief_path}", file=sys.stderr)
            return 1
        try:
            result = plan_from_brief(brief_path, folder=args.folder)
        except Exception as e:
            print(f"Error: failed to plan from brief: {e}", file=sys.stderr)
            return 1
        if not result.ok:
            print(
                f"✗ {len(result.missing_assets)} required asset(s) missing — "
                f"see {result.questions_path}",
                file=sys.stderr,
            )
            for path in result.missing_assets:
                print(f"  - {path}", file=sys.stderr)
            return 1
        plan_path = str(result.plan_path)
    else:
        plan_path = args.plan

    if args.scene is not None:
        return test_scene(
            folder=args.folder,
            plan_path=plan_path,
            scene_index=args.scene,
            aspect=args.aspect,
        )
    try:
        result = run_plan(
            folder=args.folder, plan_path=plan_path,
            aspect=args.aspect, yes=getattr(args, "yes", False),
            hq=getattr(args, "hq", False),
            debug_level=getattr(args, "debug", 0),
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


def _run_plan_command(args) -> int:
    from pathlib import Path
    from ..planner import plan_from_brief

    folder = Path(args.folder).expanduser()
    brief_path = (
        Path(args.brief).expanduser()
        if args.brief is not None
        else folder / "brief.yaml"
    )
    if not brief_path.is_file():
        print(f"Error: brief not found: {brief_path}", file=sys.stderr)
        return 1

    try:
        result = plan_from_brief(
            brief_path,
            folder=folder,
            out_path=args.out,
            image_model=args.model,
            caption_style=args.caption_style,
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
        for path in result.missing_assets:
            print(f"  - {path}", file=sys.stderr)
        return 1

    print(f"✓ Wrote plan.yaml ({result.scene_count} scenes) → {result.plan_path}")
    return 0


def _run_ingest_command(args) -> int:
    from ..ingest import ingest

    try:
        result = ingest(
            args.path,
            out_path=args.out,
            visual=args.visual,
            estimate=args.estimate,
            parallel=args.parallel,
        )
    except NotImplementedError:
        print("Error: --visual is not implemented yet; rerun without it.", file=sys.stderr)
        return 1
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.estimate:
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
