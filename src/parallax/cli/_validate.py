"""parallax validate — dry-run brief and plan validation with structured errors."""
from __future__ import annotations

import argparse
import json
import logging

log = logging.getLogger(__name__)


def register_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "validate",
        help="Validate a brief or plan — no assets generated, no credits spent.",
        description=(
            "Run all checks against a brief or plan and exit with structured JSON errors. "
            "Exits 0 if valid (warnings allowed), 1 if any errors."
        ),
    )
    p.add_argument("--folder", required=True, help="Path to the project folder.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--brief", help="Path to a brief.yaml to validate.")
    src.add_argument("--plan", help="Path to a plan.yaml to validate.")


def run(args: argparse.Namespace) -> int:
    from ..validate import validate_brief, validate_plan

    if args.brief is not None:
        log.info("validate: brief=%s folder=%s", args.brief, args.folder)
        result = validate_brief(args.brief, args.folder)
    else:
        log.info("validate: plan=%s folder=%s", args.plan, args.folder)
        result = validate_plan(args.plan, args.folder)

    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1
