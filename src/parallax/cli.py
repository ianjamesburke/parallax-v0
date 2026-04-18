from __future__ import annotations

import argparse
import sys

from .agent import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="parallax", description="Agentic creative production CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a creative brief through the agent loop.")
    run_p.add_argument("--brief", required=True, help="Creative brief / prompt.")
    run_p.add_argument("--resume", dest="session_id", default=None, help="Resume a session by ID.")

    args = parser.parse_args(argv)

    if args.command == "run":
        result = run(brief=args.brief, session_id=args.session_id)
        print(f"[session {result['session_id']}]")
        if result["text"]:
            print(result["text"])
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
