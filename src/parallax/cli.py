from __future__ import annotations

import argparse
import logging
import sys

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

    return 2


if __name__ == "__main__":
    sys.exit(main())
