"""parallax validate — dry-run brief and plan validation with structured errors."""
from __future__ import annotations

import json
import logging
from typing import Optional

import typer

log = logging.getLogger(__name__)


def register_validate(app: typer.Typer) -> None:
    app.command("validate")(_validate_cmd)


def _validate_cmd(
    folder: str = typer.Option(..., "--folder", help="Path to the project folder."),
    brief: Optional[str] = typer.Option(None, "--brief", help="Path to a brief.yaml to validate."),
    plan: Optional[str] = typer.Option(None, "--plan", help="Path to a plan.yaml to validate."),
) -> int:
    if brief is None and plan is None:
        typer.echo("Error: one of --brief or --plan is required", err=True)
        return 2
    if brief is not None and plan is not None:
        typer.echo("Error: --brief and --plan are mutually exclusive", err=True)
        return 2

    from ..validate import validate_brief, validate_plan

    if brief is not None:
        log.info("validate: brief=%s folder=%s", brief, folder)
        result = validate_brief(brief, folder)
    else:
        log.info("validate: plan=%s folder=%s", plan, folder)
        result = validate_plan(plan, folder)  # type: ignore[arg-type]  # validated: brief/plan mutex means plan is str here

    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1
