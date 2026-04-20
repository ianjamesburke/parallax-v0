from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys

from . import usage
from .backends import AVAILABLE_BACKENDS, run as backend_run
from .log import configure as configure_logging
from .update_check import check_for_update


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

    run_p = sub.add_parser("run", help="Run a creative brief through the agent loop.")
    run_p.add_argument("--brief", required=True, help="Creative brief / prompt.")
    run_p.add_argument("--resume", dest="session_id", default=None, help="Resume a session by ID.")
    run_p.add_argument(
        "--backend",
        choices=AVAILABLE_BACKENDS,
        default=None,
        help="Which backend to use (default: claude-code; override with PARALLAX_BACKEND env).",
    )

    # --- Voiceover ---
    run_p.add_argument(
        "--voice",
        default=None,
        help=(
            "ElevenLabs voice name (partial match), full name, or raw voice ID. "
            "Default: george. Run 'parallax voices' to browse all 700+ options."
        ),
    )
    run_p.add_argument(
        "--speed", type=float, default=None,
        help="Voiceover speed multiplier. Default: 1.1.",
    )

    # --- Video ---
    run_p.add_argument(
        "--resolution",
        choices=["1080x1920", "1920x1080"],
        default=None,
        help="Output resolution. Default: 1080x1920 (vertical).",
    )

    # --- Image generation ---
    run_p.add_argument(
        "--image-model",
        choices=["draft", "mid", "premium", "nano-banana", "grok"],
        default=None,
        dest="image_model",
        help="Image generation tier. Default: mid.",
    )

    # --- Captions ---
    run_p.add_argument(
        "--caption-style",
        choices=["bangers", "impact", "bebas", "anton", "clean"],
        default=None,
        dest="caption_style",
        help="Caption font/style preset. Default: anton.",
    )
    run_p.add_argument(
        "--fontsize", type=int, default=None,
        help="Caption font size in pixels. Default: 70.",
    )
    run_p.add_argument(
        "--words-per-chunk", type=int, default=None, dest="words_per_chunk",
        help="Words per caption chunk. Default: 1 (one word at a time).",
    )
    run_p.add_argument(
        "--no-captions", action="store_true", dest="no_captions",
        help="Skip caption burning entirely.",
    )

    # --- Headline ---
    run_p.add_argument(
        "--headline", default=None,
        help="Overlay a persistent headline title (e.g. 'SHE TRAINS ALONE'). Omit to skip.",
    )
    run_p.add_argument(
        "--headline-fontsize", type=int, default=None, dest="headline_fontsize",
        help="Headline font size. Default: 64.",
    )
    run_p.add_argument(
        "--headline-bg", default=None, dest="headline_bg",
        help="Headline background box color. Default: white.",
    )
    run_p.add_argument(
        "--headline-color", default=None, dest="headline_color",
        help="Headline text color. Default: black.",
    )

    chat_p = sub.add_parser("chat", help="Start an interactive session with the Parallax agent.")
    chat_p.add_argument("--resume", dest="session_id", default=None, help="Resume a session by ID.")
    chat_p.add_argument(
        "--backend",
        choices=AVAILABLE_BACKENDS,
        default=None,
        help="Which backend to use.",
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

    # Best-effort, non-blocking: never raises, swallows network/filesystem errors.
    # Skipped during `update` itself (no point nagging mid-upgrade).
    if args.command != "update":
        check_for_update()

    if args.command == "run":
        brief = args.brief
        overrides = _build_overrides(args)
        if overrides:
            brief = f"{brief}\n\n{overrides}"
        result = backend_run(brief=brief, session_id=args.session_id, backend=args.backend)
        print(f"[session {result['session_id']}]")
        if result["text"]:
            print(result["text"])
        return 0

    if args.command == "chat":
        return _run_chat(args.session_id, args.backend)

    if args.command == "voices":
        return _print_voices(args.voice_filter, args.limit)

    if args.command == "usage":
        _print_usage(usage.summarize(include_test=args.include_test))
        return 0

    if args.command == "update":
        return _run_update()

    return 2


def _run_chat(session_id: str | None, backend: str | None) -> int:
    if session_id:
        print(f"Resuming session {session_id}. Type your message, Ctrl+C or empty line to exit.\n")
    else:
        print("Parallax chat. Type your message, Ctrl+C or empty line to exit.\n")
    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except EOFError:
                break
            if not user_input:
                break
            result = backend_run(brief=user_input, session_id=session_id, backend=backend)
            session_id = result["session_id"]
            if result["text"]:
                print(f"\nParallax: {result['text']}\n")
            else:
                print(f"\n[session {session_id}]\n")
    except KeyboardInterrupt:
        print()
    if session_id:
        print(f"Session: {session_id}")
    return 0


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


def _build_overrides(args: argparse.Namespace) -> str:
    """Build a pipeline-overrides block from CLI flags. Only includes explicitly set values."""
    lines: list[str] = []
    if args.voice:
        lines.append(f"voice: {args.voice}")
    if args.speed is not None:
        lines.append(f"speed: {args.speed}")
    if args.resolution:
        lines.append(f"resolution: {args.resolution}")
    if args.image_model:
        lines.append(f"image_model: {args.image_model}")
    if args.no_captions:
        lines.append("captions: skip")
    else:
        if args.caption_style:
            lines.append(f"caption_style: {args.caption_style}")
        if args.fontsize is not None:
            lines.append(f"fontsize: {args.fontsize}")
        if args.words_per_chunk is not None:
            lines.append(f"words_per_chunk: {args.words_per_chunk}")
    if args.headline:
        lines.append(f"headline: {args.headline}")
        if args.headline_fontsize is not None:
            lines.append(f"headline_fontsize: {args.headline_fontsize}")
        if args.headline_bg:
            lines.append(f"headline_bg: {args.headline_bg}")
        if args.headline_color:
            lines.append(f"headline_color: {args.headline_color}")
    if not lines:
        return ""
    return "---PIPELINE OVERRIDES (apply these exactly, override all defaults)---\n" + "\n".join(lines) + "\n---"


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
