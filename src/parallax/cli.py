from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys

from . import usage
from .log import configure as configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="parallax", description="Agentic creative production CLI.")
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
        help="Run a pre-planned scene manifest directly — no agent, no replanning.",
    )
    produce_p.add_argument(
        "--folder", required=True,
        help="Path to the project folder.",
    )
    produce_p.add_argument(
        "--plan", required=True,
        help="Path to a plan YAML file with scenes, prompts, voice, and model settings.",
    )

    test_scene_p = sub.add_parser(
        "test-scene",
        help="Apply the video filter for one scene and open the result. No full pipeline.",
    )
    test_scene_p.add_argument("--folder", required=True, help="Project folder path.")
    test_scene_p.add_argument("--plan", required=True, help="Plan YAML path.")
    test_scene_p.add_argument(
        "--index", required=True, type=int,
        help="Scene index to test (must have clip_path or still_path in the plan).",
    )

    voices_p = sub.add_parser("voices", help="Browse ElevenLabs voices with bios.")
    voices_p.add_argument(
        "--filter", default=None, dest="voice_filter",
        help="Filter by name, gender, accent, or use-case (partial, case-insensitive).",
    )
    voices_p.add_argument(
        "--limit", type=int, default=20,
        help="Max voices to show (default: 20). Use 0 for all.",
    )

    usage_p = sub.add_parser("usage", help="Summarize per-model and per-session usage.")
    usage_p.add_argument(
        "--include-test",
        action="store_true",
        help="Include PARALLAX_TEST_MODE records (excluded by default).",
    )

    sub.add_parser("update", help="Upgrade parallax to the latest release via uv.")

    args = parser.parse_args(argv)

    level: int | None = None
    if args.verbose >= 2:
        level = logging.DEBUG
    elif args.verbose == 1:
        level = logging.INFO
    configure_logging(level)

    if args.command == "produce":
        from .produce import run_plan
        return run_plan(folder=args.folder, plan_path=args.plan)

    if args.command == "test-scene":
        from .produce import test_scene
        return test_scene(folder=args.folder, plan_path=args.plan, scene_index=args.index)

    if args.command == "voices":
        return _print_voices(args.voice_filter, args.limit)

    if args.command == "usage":
        _print_usage(usage.summarize(include_test=args.include_test))
        return 0

    if args.command == "update":
        return _run_update()

    return 2


def _print_voices(filter_str: str | None, limit: int) -> int:
    import os
    key = os.environ.get("AI_VIDEO_ELEVENLABS_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        print(
            "ElevenLabs key required: set AI_VIDEO_ELEVENLABS_KEY or ELEVENLABS_API_KEY",
            file=sys.stderr,
        )
        return 1
    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=key)
        resp = client.voices.get_all()
    except Exception as e:
        print(f"Failed to fetch voices: {e}", file=sys.stderr)
        return 1

    voices = resp.voices
    if filter_str:
        needle = filter_str.lower()
        voices = [
            v for v in voices
            if needle in (v.name or "").lower()
            or needle in (v.description or "").lower()
            or needle in str(v.labels or {}).lower()
        ]

    total = len(voices)
    if limit > 0:
        voices = voices[:limit]

    print(f"\n{'ID':<24} {'Name':<36} {'Gender':<8} {'Accent':<14} Use case")
    print("-" * 100)
    for v in voices:
        labels = v.labels or {}
        gender = labels.get("gender", "")
        accent = labels.get("accent", "")
        use_case = labels.get("use_case", "")
        name = (v.name or "")[:35]
        print(f"{v.voice_id:<24} {name:<36} {gender:<8} {accent:<14} {use_case}")
        if v.description:
            print(f"  {v.description[:90]}")

    shown = len(voices)
    if filter_str:
        print(f"\n{shown} of {total} voices match '{filter_str}'.", end="")
    else:
        print(f"\nShowing {shown} of {total} total voices.", end="")
    print(" Pass --voice NAME or --voice ID to use one.\n")
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
    print("Upgrading parallax via uv tool upgrade…")
    result = subprocess.run([uv, "tool", "upgrade", "parallax"])
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


if __name__ == "__main__":
    sys.exit(main())
