"""parallax guide — machine-readable workflow map."""
from __future__ import annotations

import json

import typer


def register_guide(app: typer.Typer) -> None:
    app.command("guide")(_guide_cmd)


def _guide_cmd() -> int:
    """Print the workflow map as JSON: stages, commands, flags, preconditions, review boundaries, human decisions."""
    from .. import cli as _cli
    from ..guide import build_guide
    typer.echo(json.dumps(build_guide(typer.main.get_group(_cli.app)), indent=2))
    return 0
