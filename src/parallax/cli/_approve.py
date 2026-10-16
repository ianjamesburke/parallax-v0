"""parallax approve — record a human's spend budget in the project. No provider calls."""
from __future__ import annotations

import json
import shlex
import sys

import typer

approve_app = typer.Typer(name="approve", help="Record human approvals that unlock paid work.", no_args_is_help=True)


@approve_app.command("spend")
def _spend_cmd(
    folder: str = typer.Option(..., "--folder", help="Project root; the approval is written to <folder>/spend-approval.yaml."),
    budget_usd: float = typer.Option(..., "--budget-usd", help="Total USD the human allows for this project across produce, image generate, video animate and audio voiceover."),
    authorized_by: str = typer.Option(..., "--authorized-by", help="Name of the human who authorized the spend. Never fill this in yourself."),
) -> int:
    """Record the budget paid commands may spend. Human only: needs an interactive terminal and a typed confirmation."""
    from ..spend_guard import approve
    # Never echo agent-supplied --budget-usd or --authorized-by: the human supplies both.
    command = f"parallax approve spend --folder {shlex.quote(folder)} --budget-usd <budget> --authorized-by <your name>"
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        typer.echo("Error: `approve spend` records a human's decision, so it only runs in an interactive terminal "
                   "(stdin and stdout must both be a TTY); an agent cannot run it.\n"
                   f"Fix: ask the human to run this in their own terminal, or in this session with the `!` prefix, filling in the budget and their own name:\n  ! {command}", err=True)
        return 2
    typer.echo(f"{authorized_by} authorizes up to ${budget_usd:g} of paid generation in {folder}.")
    typed = input("Re-type the budget in USD to confirm: ").strip()
    try:
        confirmed = float(typed) == budget_usd
    except ValueError:
        confirmed = False
    if not confirmed:
        typer.echo(f"Error: typed {typed!r}, which does not match --budget-usd {budget_usd:g}. Nothing was recorded. Re-run to try again.", err=True)
        return 2
    try:
        result = approve(folder, budget_usd, authorized_by)
    except (ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        return 2
    typer.echo(json.dumps(result, indent=2))
    return 0
