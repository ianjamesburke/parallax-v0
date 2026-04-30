from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys

from . import __version__, usage
from .log import configure as configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="parallax", description="Agentic creative production CLI.")
    parser.add_argument(
        "--version",
        action="version",
        version=f"parallax {__version__}",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity: -v=INFO, -vv=DEBUG. Overrides PARALLAX_LOG_LEVEL.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    produce_p = sub.add_parser(
        "produce",
        help="Run a plan.yaml or brief.yaml end-to-end.",
        description=(
            "Run from a plan.yaml (--plan) OR a brief.yaml (--brief). "
            "Briefs are materialized into a plan first, then produced."
        ),
    )
    produce_p.add_argument(
        "--folder", required=True,
        help="Path to the project folder.",
    )
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
        default=None,
        help="Output aspect ratio. Overrides plan.aspect when set. "
             "Falls back to plan.aspect, then 9:16.",
    )
    produce_p.add_argument(
        "--scene", type=int, default=None,
        help="If set, render only this scene index (no full pipeline). "
             "Scene must have clip_path or still_path in the plan.",
    )

    plan_p = sub.add_parser(
        "plan",
        help="Translate a brief.yaml into a plan.yaml.",
    )
    plan_p.add_argument("--folder", required=True, help="Project root.")
    plan_p.add_argument(
        "--brief", default=None,
        help="Path to brief.yaml (default: <folder>/brief.yaml).",
    )
    plan_p.add_argument(
        "--out", default=None,
        help="Override plan.yaml output path "
             "(default: <folder>/parallax/scratch/plan.yaml).",
    )
    plan_p.add_argument(
        "--model", default="mid",
        help="Image model alias for the plan (default: mid).",
    )
    plan_p.add_argument(
        "--caption-style", default="anton",
        help="Caption preset name written into the plan (default: anton).",
    )

    ingest_p = sub.add_parser(
        "ingest",
        help="Index footage into a searchable JSON.",
    )
    ingest_p.add_argument("path", help="Clip file or directory of clips.")
    ingest_p.add_argument(
        "--out", default=None,
        help="Override the index.json output path.",
    )
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

    # `models` group — browse the alias catalog.
    models_p = sub.add_parser(
        "models", help="Browse the model catalog (image / video / tts aliases)."
    )
    models_sub = models_p.add_subparsers(dest="models_command", required=True)
    models_list_p = models_sub.add_parser("list", help="List every alias grouped by kind.")
    models_list_p.add_argument(
        "--kind",
        choices=("image", "video", "tts"),
        default=None,
        help="Filter to a single kind.",
    )
    models_list_p.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON instead of a table."
    )
    models_show_p = models_sub.add_parser("show", help="Show capabilities for one alias.")
    models_show_p.add_argument("alias", help="Model alias (e.g. 'mid', 'kling', 'tts-mini').")
    models_show_p.add_argument(
        "--kind",
        choices=("image", "video", "tts"),
        default=None,
        help="Disambiguate when an alias exists in multiple kinds.",
    )

    # --- audio subcommands ---
    audio_p = sub.add_parser("audio", help="Audio utilities.")
    audio_sub = audio_p.add_subparsers(dest="audio_command", required=True)

    transcribe_p = audio_sub.add_parser(
        "transcribe",
        help="Transcribe audio or video to word-level timestamps JSON.",
    )
    transcribe_p.add_argument("input", help="Audio or video file to transcribe.")
    transcribe_p.add_argument("--out", required=True, help="Output path for words JSON.")

    detect_p = audio_sub.add_parser(
        "detect-silences",
        help="List silent sections in audio — use output to choose a range for trim.",
    )
    detect_p.add_argument("input", help="Audio or video file to analyze.")
    detect_p.add_argument("--min-silence", type=float, default=0.15,
                          help="Minimum silence duration in seconds to report (default: 0.15).")
    detect_p.add_argument("--noise-db", type=float, default=-40.0,
                          help="Noise floor in dB (default: -40).")

    trim_p = audio_sub.add_parser(
        "trim",
        help="Remove a specific time range from plan audio, avatar, and words. Updates plan.yaml in-place.",
    )
    trim_p.add_argument("--plan", required=True, help="Path to plan.yaml.")
    trim_p.add_argument("--folder", required=True, help="Project folder (paths in plan are relative to this).")
    trim_p.add_argument("--start", type=float, required=True, help="Start of range to remove (seconds).")
    trim_p.add_argument("--end", type=float, required=True, help="End of range to remove (seconds).")

    cap_p = audio_sub.add_parser(
        "cap-pauses",
        help=("Cap inter-word gaps to a max length using WhisperX word boundaries — "
              "trims long pauses without amplitude probing. Pure word-driven."),
    )
    cap_p.add_argument("--input", "-i", required=True, help="Audio (or m4a/mp3) file to trim.")
    cap_p.add_argument("--output", "-o", required=True, help="Output wav path.")
    cap_p.add_argument("--max-gap", type=float, default=0.75,
                       help="Max allowed gap between adjacent words, in seconds (default: 0.75). "
                            "Gaps longer than this are reduced to exactly this value, split half/half "
                            "across the joint so 0.75 → 0.375s tail of prev word + 0.375s lead-in of next.")
    cap_p.add_argument("--crossfade", type=float, default=0.05,
                       help="Crossfade duration at each cut joint, in seconds (default: 0.05).")

    speed_p = audio_sub.add_parser(
        "speed",
        help=("Apply ffmpeg atempo to retime an audio file. "
              "Use --rate <multiplier> or --by <pct%%>."),
    )
    speed_p.add_argument("--in", dest="in_path", required=True, help="Input audio file.")
    speed_p.add_argument("--out", dest="out_path", required=True, help="Output audio path.")
    rate_grp = speed_p.add_mutually_exclusive_group(required=True)
    rate_grp.add_argument("--rate", type=float, default=None,
                          help="atempo multiplier (e.g. 1.3 = 30%% faster).")
    rate_grp.add_argument("--by", type=str, default=None,
                          help="Percent change with trailing %% — e.g. '30%%' (=1.3) or '-20%%' (=0.8).")

    # --- video subcommands ---
    video_p = sub.add_parser("video", help="Video utilities.")
    video_sub = video_p.add_subparsers(dest="video_command", required=True)

    frame_p = video_sub.add_parser("frame", help="Extract a single frame from a video.")
    frame_p.add_argument("input", help="Video file.")
    frame_p.add_argument("time", type=float, help="Timestamp in seconds.")
    frame_p.add_argument("--out", default=None, help="Output image path (default: temp file).")

    color_p = video_sub.add_parser(
        "color", help="Sample a pixel color from a video or image. Prints 0xRRGGBB."
    )
    color_p.add_argument("input", help="Video or image file.")
    color_p.add_argument("--time", type=float, default=2.0, help="Timestamp for video frames (default: 2.0).")
    color_p.add_argument("--x", type=int, default=10, help="X pixel coordinate (default: 10).")
    color_p.add_argument("--y", type=int, default=10, help="Y pixel coordinate (default: 10).")

    usage_p = sub.add_parser("usage", help="Per-model / per-session cost summary.")
    usage_p.add_argument(
        "--include-test",
        action="store_true",
        help="Include PARALLAX_TEST_MODE records (excluded by default).",
    )

    sub.add_parser(
        "credits",
        help="OpenRouter balance.",
    )

    log_p = sub.add_parser(
        "log",
        help="Inspect run logs — summary view by default, or `log list` for all runs.",
        description=(
            "parallax log <spec>            view one run (spec: latest | <short> | <run_id>)\n"
            "parallax log list              tabulate recent runs from the index"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    log_p.add_argument(
        "spec", nargs="?", default="latest",
        help="Run spec: 'latest' (default), 6-hex short id, full run_id, or 'list'.",
    )
    log_p.add_argument(
        "--level", choices=("info", "debug"), default="info",
        help="Minimum level to include (default: info — drops DEBUG events).",
    )
    log_p.add_argument(
        "--summary", dest="summary", action="store_true", default=True,
        help="Operator-readable digest (default).",
    )
    log_p.add_argument(
        "--no-summary", dest="summary", action="store_false",
        help="Emit raw NDJSON (level-filtered).",
    )
    log_p.add_argument(
        "--follow", "-f", action="store_true",
        help="Stream new events live (forces --no-summary).",
    )
    log_p.add_argument("--limit", type=int, default=20, help="`log list` row cap (default: 20).")
    log_p.add_argument(
        "--since", default=None,
        help="`log list` time filter, e.g. '1d', '6h', '30m'.",
    )

    verify_p = sub.add_parser(
        "verify",
        help="Run or scaffold verify suite cases.",
    )
    verify_sub = verify_p.add_subparsers(dest="verify_command", required=True)

    verify_suite_p = verify_sub.add_parser(
        "suite",
        help="Run case folders against expected.yaml.",
        description=(
            "Each case subfolder must contain a plan.yaml + expected.yaml. "
            "expected.yaml schema (every block optional): "
            "final.{resolution,duration_s,audio_video_diff_s_max,scene_count}, "
            "stages.<name>.{files_must_exist,resolution,contiguous_cover}, "
            "manifest.{keys_required,scene_keys_required}, "
            "run_log.{must_not_contain,must_contain}, cost_usd_max, paid."
        ),
    )
    verify_suite_p.add_argument("suite_dir", help="Directory containing one or more case subfolders.")
    verify_suite_p.add_argument(
        "--paid", action="store_true",
        help="Run cases marked paid: true (default skips them).",
    )
    verify_suite_p.add_argument(
        "--case", default=None,
        help="Run only a single case subfolder by name (default: all).",
    )

    verify_init_p = verify_sub.add_parser(
        "init",
        help="Scaffold a new verify suite case.",
        description=(
            "Creates a new case folder at <target>. With --from <existing>, "
            "copies that case verbatim and optionally rewrites the resolution. "
            "Without --from, writes a minimal one-scene starter that points "
            "at the canonical reference case for the full schema."
        ),
    )
    verify_init_p.add_argument("target", help="Path to the new case folder.")
    verify_init_p.add_argument(
        "--from", dest="from_dir", default=None,
        help="Copy from an existing case folder (must contain plan.yaml + expected.yaml).",
    )
    verify_init_p.add_argument(
        "--resolution", default=None,
        help="WxH (e.g. 480x854). Rewrites plan.yaml's resolution and expected.final.resolution.",
    )
    verify_init_p.add_argument(
        "--force", action="store_true",
        help="Overwrite the target if it already exists (default: refuse).",
    )

    sub.add_parser("update", help="Upgrade parallax via uv.")

    completions_p = sub.add_parser(
        "completions",
        help="Manage shell tab completion.",
    )
    completions_sub = completions_p.add_subparsers(dest="completions_command", required=True)
    completions_install_p = completions_sub.add_parser(
        "install",
        help="Write the completion stub to a cache file and print the line to add to your shell config.",
    )
    completions_install_p.add_argument(
        "--shell",
        choices=["zsh", "bash"],
        default=None,
        help="Target shell (default: detect from $SHELL).",
    )
    completions_install_p.add_argument(
        "--path",
        default=None,
        help="Output path (default: ~/.cache/<shell>/parallax-completion.<shell>).",
    )
    completions_print_p = completions_sub.add_parser(
        "print",
        help="Print the completion stub to stdout (escape hatch — prefer `install`).",
    )
    completions_print_p.add_argument("shell", choices=["zsh", "bash"])

    _enable_help_on_empty(parser)

    try:
        import argcomplete

        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    args = parser.parse_args(argv)

    if getattr(args, "_help_on_empty", None) is not None:
        args._help_on_empty()
        return 0

    level: int | None = None
    if args.verbose >= 2:
        level = logging.DEBUG
    elif args.verbose == 1:
        level = logging.INFO
    configure_logging(level)

    if args.command == "produce":
        return _run_produce(args)

    if args.command == "plan":
        return _run_plan_command(args)

    if args.command == "ingest":
        return _run_ingest_command(args)

    if args.command == "usage":
        _print_usage(usage.summarize(include_test=args.include_test))
        return 0

    if args.command == "update":
        return _run_update()

    if args.command == "completions":
        if args.completions_command == "install":
            return _run_completions_install(args.shell, args.path)
        if args.completions_command == "print":
            return _run_completions_print(args.shell)

    if args.command == "credits":
        from .openrouter import InsufficientCreditsError, check_credits
        try:
            balance = check_credits(min_balance_usd=0.0)  # informational; never raise
            print(
                f"OpenRouter credits — total ${balance.total:.2f}, "
                f"used ${balance.used:.2f}, remaining ${balance.remaining:.2f}"
            )
            if balance.remaining < 0.50:
                print(
                    f"  ⚠ Low. Top up at https://openrouter.ai/settings/credits",
                    file=sys.stderr,
                )
                return 1
            return 0
        except InsufficientCreditsError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"Error: could not fetch credits ({type(e).__name__}: {e})", file=sys.stderr)
            return 1

    if args.command == "models":
        from . import models as _models_pkg
        if args.models_command == "list":
            return _print_models_list(_models_pkg, kind=args.kind, as_json=args.json)
        if args.models_command == "show":
            return _print_model_show(_models_pkg, alias=args.alias, kind=args.kind)
        return 1

    if args.command == "verify":
        if args.verify_command == "suite":
            from .verify_suite import cli_run
            return cli_run(args.suite_dir, paid=args.paid, case=args.case)
        if args.verify_command == "init":
            from .verify_suite import cli_init
            return cli_init(
                args.target,
                from_dir=args.from_dir,
                resolution=args.resolution,
                force=args.force,
            )
        return 1

    if args.command == "log":
        return _run_log_command(args)

    if args.command == "audio":
        if args.audio_command == "transcribe":
            from .audio import transcribe_words
            try:
                words = transcribe_words(args.input, args.out)
            except RuntimeError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            print(f"{len(words)} words → {args.out}")
            if words:
                last = words[-1]
                print(f"  duration: {last['end']:.2f}s  last word: '{last['word']}' @ {last['end']:.2f}s")
            return 0

        if args.audio_command == "detect-silences":
            from .audio import detect_silences
            silences = detect_silences(args.input, noise_db=args.noise_db, min_silence_s=args.min_silence)
            if not silences:
                print("No silences detected.")
                return 0
            print(f"{'#':<4} {'start':>8} {'end':>8} {'duration':>10}")
            print("-" * 36)
            for i, s in enumerate(silences):
                print(f"{i:<4} {s['start']:>8.3f} {s['end']:>8.3f} {s['duration']:>10.3f}s")
            print(f"\nTo remove silence #{0}: parallax audio trim --plan <plan.yaml> --folder <folder> --start {silences[0]['start']} --end {silences[0]['end']}")
            return 0

        if args.audio_command == "trim":
            from .audio import trim_silence
            result = trim_silence(
                plan_path=args.plan,
                folder=args.folder,
                cut_start=args.start,
                cut_end=args.end,
            )
            removed = result["seconds_removed"]
            print(f"Removed {removed:.3f}s ({args.start:.3f}s–{args.end:.3f}s)")
            print(f"  audio  → {result['new_audio']}")
            print(f"  words  → {result['new_words']}")
            if result["new_avatar"]:
                print(f"  avatar → {result['new_avatar']}")
            print(f"plan.yaml updated. Run: parallax produce --folder {args.folder} --plan {args.plan}")
            return 0

        if args.audio_command == "speed":
            from pathlib import Path as _P
            from .audio import speedup, parse_by_pct
            rate = args.rate if args.rate is not None else parse_by_pct(args.by)
            out = speedup(_P(args.in_path), _P(args.out_path), rate)
            print(f"audio speed: rate={rate:.4f} → {out}")
            return 0

        if args.audio_command == "cap-pauses":
            from .audio import cap_pauses
            result = cap_pauses(
                input_path=args.input,
                output_path=args.output,
                max_gap_s=args.max_gap,
                crossfade_s=args.crossfade,
            )
            print(f"cap-pauses: {result['gaps_trimmed']} gaps capped to {result['max_gap_s']:.2f}s")
            print(f"  duration: {result['original_duration_s']:.2f}s → {result['new_duration_s']:.2f}s "
                  f"({result['seconds_removed']:.2f}s removed)")
            print(f"  output  → {result['output']}")
            return 0

    if args.command == "video":
        if args.video_command == "frame":
            from .video import extract_frame
            out = extract_frame(args.input, args.time, args.out)
            print(out)
            return 0
        if args.video_command == "color":
            from .video import sample_color
            print(sample_color(args.input, args.x, args.y, args.time))
            return 0

    return 2


def _run_produce(args) -> int:
    """Dispatch `parallax produce` — handles --plan, --brief, and --scene."""
    from pathlib import Path

    from .openrouter import InsufficientCreditsError
    from .produce import run_plan, test_scene

    # Resolve plan path: either passed directly or materialized from a brief.
    if args.brief is not None:
        from .planner import plan_from_brief

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

    try:
        if args.scene is not None:
            return test_scene(
                folder=args.folder,
                plan_path=plan_path,
                scene_index=args.scene,
                aspect=args.aspect,
            )
        return run_plan(folder=args.folder, plan_path=plan_path, aspect=args.aspect)
    except InsufficientCreditsError as e:
        print(f"\nError: {e}\n", file=sys.stderr)
        return 1


def _run_plan_command(args) -> int:
    """Dispatch `parallax plan` — translate a brief.yaml into a plan.yaml."""
    from pathlib import Path

    from .planner import plan_from_brief

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

    print(
        f"✓ Wrote plan.yaml ({result.scene_count} scenes) → {result.plan_path}"
    )
    return 0


def _run_ingest_command(args) -> int:
    """Dispatch `parallax ingest` — index a clip or directory."""
    from .ingest import ingest

    try:
        result = ingest(
            args.path,
            out_path=args.out,
            visual=args.visual,
            estimate=args.estimate,
            parallel=args.parallel,
        )
    except NotImplementedError:
        print(
            "Error: --visual is not implemented yet; rerun without it.",
            file=sys.stderr,
        )
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


def _print_models_list(models_pkg, kind: str | None, as_json: bool) -> int:
    """List the model catalog grouped by kind."""
    import json as _json

    tables = (
        ("image", models_pkg.IMAGE_MODELS),
        ("video", models_pkg.VIDEO_MODELS),
        ("tts", models_pkg.TTS_MODELS),
    )
    if kind is not None:
        tables = tuple((k, t) for k, t in tables if k == kind)

    if as_json:
        out = {}
        for k, table in tables:
            out[k] = [
                {
                    "alias": s.alias,
                    "model_id": s.model_id,
                    "tier": s.tier,
                    "cost": s.cost_usd,
                    "unit": s.cost_unit,
                    "fallback": s.fallback_alias,
                    "aspect_ratios": list(s.aspect_ratios),
                    "max_refs": s.max_refs,
                    "start_frame": s.start_frame,
                    "end_frame": s.end_frame,
                    "inputs": list(s.inputs),
                    "voices": list(s.voices),
                    "description": s.description,
                }
                for s in table.values()
            ]
        print(_json.dumps(out, indent=2))
        return 0

    for k, table in tables:
        print(f"\n{k.upper()}:")
        print(f"  {'alias':<18} {'tier':<8} {'cost':<10} {'fallback':<14} description")
        print(f"  {'-' * 18} {'-' * 8} {'-' * 10} {'-' * 14} {'-' * 40}")
        for s in table.values():
            cost = f"${s.cost_usd:.3f}/{s.cost_unit}"
            fb = s.fallback_alias or "—"
            print(f"  {s.alias:<18} {s.tier:<8} {cost:<10} {fb:<14} {s.description}")
    return 0


def _print_model_show(models_pkg, alias: str, kind: str | None) -> int:
    """Print full capabilities for one alias."""
    try:
        spec = models_pkg.resolve(alias, kind=kind)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"alias:          {spec.alias}")
    print(f"kind:           {spec.kind}")
    print(f"tier:           {spec.tier}")
    print(f"model_id:       {spec.model_id}")
    print(f"cost:           ${spec.cost_usd:.4f} / {spec.cost_unit}")
    print(f"fallback:       {spec.fallback_alias or '—'}")
    print(f"aspect_ratios:  {', '.join(spec.aspect_ratios) if spec.aspect_ratios else '—'}")
    if spec.kind == "image":
        print(f"max_refs:       {spec.max_refs}")
        print(f"inputs:         {', '.join(spec.inputs) if spec.inputs else '—'}")
    if spec.kind == "video":
        print(f"start_frame:    {spec.start_frame}")
        print(f"end_frame:      {spec.end_frame}")
    if spec.kind == "tts":
        if spec.voices:
            print(f"voices ({len(spec.voices)}):")
            for v in spec.voices:
                print(f"  - {v}")
    if spec.description:
        print(f"\n{spec.description}")
    return 0


def _enable_help_on_empty(parser: argparse.ArgumentParser) -> None:
    """Walk the parser tree: any parser with subparsers prints its help when
    invoked with no subcommand, instead of erroring. Each ancestor stamps its
    own print_help into args._help_on_empty; the deepest matched parser wins.
    Leaf parsers clear the default so concrete subcommands run normally."""
    has_subparsers = False
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            has_subparsers = True
            action.required = False
            parser.set_defaults(_help_on_empty=parser.print_help)
            for subparser in action.choices.values():
                _enable_help_on_empty(subparser)
    if not has_subparsers:
        parser.set_defaults(_help_on_empty=None)


def _run_completions_print(shell: str) -> int:
    import argcomplete

    print(argcomplete.shellcode(["parallax"], shell=shell))
    return 0


def _detect_shell() -> str:
    import os

    name = os.path.basename(os.environ.get("SHELL", ""))
    if name in {"zsh", "bash"}:
        return name
    return "zsh"


def _run_completions_install(shell: str | None, path: str | None) -> int:
    import argcomplete
    from pathlib import Path

    target_shell = shell or _detect_shell()
    if target_shell not in {"zsh", "bash"}:
        print(f"Unsupported shell: {target_shell}", file=sys.stderr)
        return 1

    if path:
        out = Path(path).expanduser()
    else:
        out = Path.home() / ".cache" / target_shell / f"parallax-completion.{target_shell}"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(argcomplete.shellcode(["parallax"], shell=target_shell))

    print(f"Wrote {target_shell} completion stub to {out}")
    print()
    print("Add this line to your shell config (e.g. ~/.zshrc or ~/dotfiles/zshrc):")
    print(f"  source {out}")
    print()
    print(f"Then restart your shell. To refresh later: rm {out} && parallax completions install")
    return 0


def _run_update() -> int:
    uv = shutil.which("uv")
    if not uv:
        print(
            "uv not found on PATH. Install it first:\n"
            "  curl -LsSf https://astral.sh/uv/install.sh | sh",
            file=sys.stderr,
        )
        return 1
    print("Upgrading parallax via uv tool upgrade --reinstall…")
    result = subprocess.run([uv, "tool", "upgrade", "parallax", "--reinstall"])
    return result.returncode


def _print_usage(summary: dict) -> None:
    scope = "all records (incl. test mode)" if summary["include_test_mode"] else "real runs only"
    print(f"Usage summary — {scope}")
    print(f"Log: {summary['log_path']}")
    print(
        f"Totals: {summary['total_calls']} calls, "
        f"${summary['total_cost_usd']:.4f}, "
        f"{summary['total_duration_ms']} ms, "
        f"{summary['session_count']} sessions"
    )
    by_alias = summary["by_alias"]
    if not by_alias:
        print("(no records)")
        return
    print(f"{'alias':<14}{'tier':<10}{'calls':>7}{'cost_usd':>12}{'duration_ms':>14}")
    for alias, slot in sorted(by_alias.items()):
        print(
            f"{alias:<14}{slot['tier']:<10}{slot['calls']:>7}"
            f"{slot['cost_usd']:>12.4f}{slot['duration_ms']:>14}"
        )


def _run_log_command(args) -> int:
    """Dispatch `parallax log` — summary, raw, follow, or list."""
    from pathlib import Path
    from . import runlog

    if args.spec == "list":
        return _print_log_list(limit=args.limit, since=args.since)

    spec = args.spec or "latest"
    row = runlog.find_run(spec)
    if row is None:
        print(f"Error: no run found for spec {spec!r}", file=sys.stderr)
        return 1

    if args.follow:
        # Live tail — bypass summary, stream raw with level filter ignored.
        return runlog.tail(spec, follow=True)

    out_dir = row.get("output_dir")
    if not out_dir:
        print(f"Error: run {row.get('run_id')} has no output_dir", file=sys.stderr)
        return 1
    log_path = Path(out_dir) / "run.log"
    if not log_path.exists():
        print(f"Error: log file missing: {log_path}", file=sys.stderr)
        return 1

    events = _load_events(log_path)
    if args.summary:
        _print_log_summary(row, events, level=args.level)
        return 0
    return _print_log_raw(events, level=args.level)


def _load_events(path) -> list[dict]:
    import json as _json
    out: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(_json.loads(line))
            except _json.JSONDecodeError:
                continue
    return out


_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


def _level_passes(rec_level: str, min_level: str) -> bool:
    floor = _LEVEL_ORDER.get(min_level.upper(), 20)
    return _LEVEL_ORDER.get(rec_level.upper(), 20) >= floor


def _print_log_raw(events: list[dict], level: str) -> int:
    import json as _json
    for ev in events:
        if not _level_passes(ev.get("level", "INFO"), level):
            continue
        print(_json.dumps(ev, ensure_ascii=False))
    return 0


def _print_log_summary(row: dict, events: list[dict], level: str = "info") -> None:
    """Operator-readable digest: timing, output, stages, provider calls, warnings."""
    from datetime import datetime as _dt
    from pathlib import Path
    rid = row.get("run_id", "?")
    short = row.get("short", rid[-6:])
    started_iso = row.get("started", "")
    ended_iso = row.get("ended", "")

    def _fmt_clock(iso: str) -> str:
        try:
            return _dt.fromisoformat(iso.replace("Z", "+00:00")).strftime("%H:%M:%S")
        except Exception:
            return "?"

    started_clk = _fmt_clock(started_iso)
    ended_clk = _fmt_clock(ended_iso)

    duration_s = 0.0
    try:
        a = _dt.fromisoformat(started_iso.replace("Z", "+00:00"))
        b = _dt.fromisoformat(ended_iso.replace("Z", "+00:00"))
        duration_s = (b - a).total_seconds()
    except Exception:
        pass
    mins = int(duration_s // 60)
    secs = duration_s - mins * 60
    dur_str = f"{mins}m{secs:.0f}s" if mins else f"{secs:.1f}s"

    print(f"run {rid}   started {started_clk}  ended {ended_clk}  ({dur_str})")
    plan_path = row.get("plan_path") or "?"
    print(f"plan         {plan_path}")

    # Output path comes from run.end event's `final_video` field.
    final_video = ""
    for ev in events:
        if ev.get("event") == "run.end":
            final_video = ev.get("final_video", "")
    # Probe the mp4 for resolution + duration if it exists.
    extras = ""
    if final_video and Path(final_video).exists():
        try:
            import subprocess as _sp
            probe = _sp.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,duration",
                 "-of", "csv=p=0", str(final_video)],
                capture_output=True, text=True,
            )
            parts = probe.stdout.strip().split(",")
            if len(parts) >= 3:
                w, h, d = parts[0], parts[1], parts[2]
                extras = f"  ({w}x{h}, {float(d):.2f}s)"
        except Exception:
            pass
    print(f"output       {final_video}{extras}")
    print(f"total cost   ${float(row.get('cost_usd', 0.0)):.2f}")

    # Stage timings — pair stage.<name>.start with stage.<name>.end.
    stage_durations: list[tuple[str, int]] = []
    pending: dict[str, float] = {}
    for ev in events:
        name = ev.get("event", "")
        if name.startswith("stage.") and name.endswith(".end"):
            stage_name = name[len("stage."):-len(".end")]
            stage_durations.append((stage_name, int(ev.get("duration_ms", 0))))
    if stage_durations:
        print("\nStages")
        for name, dur_ms in stage_durations:
            human = _format_duration_ms(dur_ms)
            print(f"  {name:<11} {human}")

    # Provider calls — read usage NDJSON for this run.
    try:
        from . import usage as _usage
        records = [r for r in _usage.load_records(include_test=True)
                   if r.get("run_id") == rid]
    except Exception:
        records = []
    if records:
        print("\nProvider calls")
        for r in records:
            backend = r.get("backend", "?")
            alias = r.get("alias", "?")
            model_id = r.get("fal_id", "?")
            dur_ms = int(r.get("duration_ms", 0))
            human = _format_duration_ms(dur_ms)
            status = "ok"
            print(f"  {backend:<8} {alias:<10} {model_id:<32} {human:>8}   {status}")

    # Warnings / errors.
    warns = [ev for ev in events
             if ev.get("level") in ("WARN", "ERROR") or ev.get("event", "").endswith(".error")]
    if warns:
        print("\nWarnings/errors")
        for ev in warns:
            print(f"  [{ev.get('level', '?')}] {ev.get('event', '?')}: "
                  f"{ev.get('msg') or ev.get('error') or ''}")
    else:
        print("\nNo warnings or errors.")

    if level == "debug":
        debug_events = [ev for ev in events if ev.get("level") == "DEBUG"]
        if debug_events:
            print(f"\nDebug events ({len(debug_events)})")
            import json as _json
            for ev in debug_events:
                print("  " + _json.dumps(ev, ensure_ascii=False))


def _format_duration_ms(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    s = ms / 1000.0
    if s < 60:
        return f"{s:.1f}s"
    m = int(s // 60)
    rs = s - m * 60
    return f"{m}m{rs:.1f}s"


def _print_log_list(limit: int, since: str | None) -> int:
    """Table view of runs from the index, newest first."""
    from datetime import datetime as _dt, timedelta, timezone as _tz
    from . import runlog
    rows = runlog.load_run_index()
    if not rows:
        print("(no runs)")
        return 0
    rows = list(reversed(rows))  # newest first

    if since:
        delta = _parse_duration(since)
        if delta is None:
            print(f"Error: invalid --since value {since!r} (e.g. '1d', '6h', '30m')",
                  file=sys.stderr)
            return 1
        cutoff = _dt.now(_tz.utc) - delta
        rows = [
            r for r in rows
            if _safe_iso(r.get("started")) and _safe_iso(r.get("started")) >= cutoff
        ]

    rows = rows[:limit]
    print(f"{'short':<8} {'started':<20} {'status':<8} {'cost':>8}  output")
    print(f"{'-'*8} {'-'*20} {'-'*8} {'-'*8}  {'-'*40}")
    for r in rows:
        started = (r.get("started") or "")[:19].replace("T", " ")
        status = r.get("status", "?")
        cost = float(r.get("cost_usd", 0.0))
        out_dir = r.get("output_dir") or ""
        # Resolve mp4 path: <output_dir>/<folder.name>-vN-<short>.mp4 — but
        # we don't have folder.name. Just print the output_dir; users grep
        # the dir for the .mp4 if needed.
        print(f"{r.get('short', '?'):<8} {started:<20} {status:<8} ${cost:>6.2f}  {out_dir}")
    return 0


def _safe_iso(s):
    from datetime import datetime as _dt
    if not s:
        return None
    try:
        return _dt.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _parse_duration(s: str):
    """Parse '1d', '6h', '30m', '90s' into a timedelta. Returns None on bad input."""
    from datetime import timedelta
    if not s or len(s) < 2:
        return None
    unit = s[-1].lower()
    try:
        n = int(s[:-1])
    except ValueError:
        return None
    if unit == "s":
        return timedelta(seconds=n)
    if unit == "m":
        return timedelta(minutes=n)
    if unit == "h":
        return timedelta(hours=n)
    if unit == "d":
        return timedelta(days=n)
    return None


if __name__ == "__main__":
    sys.exit(main())
