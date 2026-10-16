"""parallax guide — machine-readable workflow map."""
from __future__ import annotations

import json
from typing import Optional

import typer


def register_guide(app: typer.Typer) -> None:
    app.command("guide")(_guide_cmd)


def _guide_cmd(
    topic: Optional[str] = typer.Argument(None, help="One topic to print: a stage id or an operator topic. Omit for the whole map, which lists every topic."),
) -> int:
    """Print the workflow map as JSON: stages, commands, flags, preconditions, review boundaries, human decisions. `guide <topic>` prints one slice."""
    from .. import cli as _cli
    from ..guide import build_guide
    try:
        guide = build_guide(typer.main.get_group(_cli.app), topic)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        return 2
    typer.echo(json.dumps(guide, indent=2))
    return 0
