from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys

from . import usage
from .backends import AVAILABLE_BACKENDS, run as backend_run
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

    run_p = sub.add_parser("run", help="Run a creative brief through the agent loop.")
    run_p.add_argument("--brief", required=True, help="Creative brief / prompt.")
    run_p.add_argument("--resume", dest="session_id", default=None, help="Resume a session by ID.")
    run_p.add_argument(
        "--backend",
        choices=AVAILABLE_BACKENDS,
        default=None,
        help="Which backend to use (default: claude-code; override with PARALLAX_BACKEND env).",
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

    if args.command == "run":
        result = backend_run(brief=args.brief, session_id=args.session_id, backend=args.backend)
        print(f"[session {result['session_id']}]")
        if result["text"]:
            print(result["text"])
        return 0

    if args.command == "usage":
        _print_usage(usage.summarize(include_test=args.include_test))
        return 0

    if args.command == "update":
        return _run_update()

    return 2


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
