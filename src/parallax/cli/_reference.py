"""parallax reference — record answers to storyboard reference gates in brief.yaml."""
from __future__ import annotations

import json
from typing import Optional

import typer

reference_app = typer.Typer(name="reference", help="Resolve storyboard reference gates.", no_args_is_help=True)


@reference_app.command("resolve")
def _resolve_cmd(
    folder: str = typer.Option(..., "--folder", help="Project root."),
    brief: str = typer.Option(..., "--brief", help="Path to the authored brief.yaml; edited in place."),
    gate: str = typer.Option(..., "--gate", help="Gate id from `parallax storyboard --json` (editorial-reference | product-reference)."),
    choice: str = typer.Option(..., "--choice", help="supplied | official | generated_anchor | no_reference. Allowed choices are listed on the gate."),
    scenes: Optional[str] = typer.Option(None, "--scenes", help="Comma-separated scene indexes to resolve (default: every scene in the gate)."),
    asset: Optional[str] = typer.Option(None, "--asset", help="Reference file, relative to --folder. Required unless --choice no_reference."),
    kind: Optional[str] = typer.Option(None, "--kind", help="style_ref | product_ref | character_ref (default: style_ref for editorial gates, product_ref for product gates)."),
    source_url: Optional[str] = typer.Option(None, "--source-url", help="Page the material came from. Required for --choice official."),
    authorized_by: Optional[str] = typer.Option(None, "--authorized-by", help="Who authorized the paid generation. Required for --choice generated_anchor."),
    reason: Optional[str] = typer.Option(None, "--reason", help="Why no reference is needed. Required for --choice no_reference."),
) -> int:
    """Attach a reference to the gate's scenes (or record a no-reference decision). No provider calls."""
    from ..reference_gates import resolve_gate
    try:
        indexes = [int(part) for part in scenes.split(",")] if scenes else None
        result = resolve_gate(
            brief, folder, gate, choice, scenes=indexes, asset=asset, kind=kind,
            source_url=source_url, authorized_by=authorized_by, reason=reason,
        )
    except (ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        return 2
    typer.echo(json.dumps(result, indent=2))
    return 0
