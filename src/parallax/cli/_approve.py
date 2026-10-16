"""parallax approve — record a human's spend budget in the project. No provider calls."""
from __future__ import annotations

import json

import typer

approve_app = typer.Typer(name="approve", help="Record human approvals that unlock paid work.", no_args_is_help=True)


@approve_app.command("spend")
def _spend_cmd(
    folder: str = typer.Option(..., "--folder", help="Project root; the approval is written to <folder>/spend-approval.yaml."),
    budget_usd: float = typer.Option(..., "--budget-usd", help="Total USD the human allows for this project across produce, image generate, video animate and audio voiceover."),
    authorized_by: str = typer.Option(..., "--authorized-by", help="Name of the human who authorized the spend. Never fill this in yourself."),
) -> int:
    """Record the budget paid commands may spend in this project. Run only after a human gave you the number."""
    from ..spend_guard import approve
    try:
        result = approve(folder, budget_usd, authorized_by)
    except (ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        return 2
    typer.echo(json.dumps(result, indent=2))
    return 0
