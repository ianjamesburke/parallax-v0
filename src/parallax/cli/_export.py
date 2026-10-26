from __future__ import annotations

import json
import sys
from typing import Optional

import typer

export_app = typer.Typer(
    help="Export editor handoff packages (Premiere Pro, DaVinci Resolve, FCP).",
    invoke_without_command=True,
    no_args_is_help=True,
)


@export_app.command("editor")
def export_editor(
    folder: str = typer.Option(..., "--folder", help="Path to the project folder."),
    version: Optional[int] = typer.Option(
        None, "--version", help="Specific produce output version N (default: latest)."
    ),
    manifest: Optional[str] = typer.Option(
        None, "--manifest", help="Explicit path to manifest.yaml."
    ),
    out: Optional[str] = typer.Option(
        None, "--out", help="Output directory (default: <folder>/editor-handoff)."
    ),
    music: Optional[str] = typer.Option(
        None, "--music", help="Optional background music audio file."
    ),
    music_db: float = typer.Option(
        -18.0, "--music-db", help="Music track gain in dB (default: -18.0)."
    ),
    logo: Optional[str] = typer.Option(
        None, "--logo", help="Optional brand logo image file."
    ),
    brand: Optional[str] = typer.Option(
        None, "--brand", help="Optional brand.yaml file to resolve logo from."
    ),
    fps: int = typer.Option(30, "--fps", help="Sequence frame rate (default: 30)."),
    fcpxml: bool = typer.Option(
        True, "--fcpxml/--no-fcpxml", help="Also write timeline.fcpxml (FCPXML 1.10)."
    ),
    render_stills: bool = typer.Option(
        True,
        "--render-stills/--stills-as-is",
        help="Render still scenes to Ken Burns MP4 clips (default) or copy still images as-is.",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Print the export summary as JSON."
    ),
) -> int:
    """Export a self-contained editor handoff package (timeline.xml, timeline.fcpxml, assets, captions.srt, README.txt)."""
    from ..editor_export import export_editor_package

    try:
        summary = export_editor_package(
            folder=folder,
            out_dir=out,
            version=version,
            manifest_path=manifest,
            music=music,
            music_db=music_db,
            logo=logo,
            brand=brand,
            fps=fps,
            fcpxml=fcpxml,
            render_stills=render_stills,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1)

    if as_json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"Editor handoff package: {summary['out_dir']}")
    print(f"  Timeline (xmeml v4): {summary['out_dir']}/timeline.xml")
    if fcpxml:
        print(f"  FCPXML 1.10:         {summary['out_dir']}/timeline.fcpxml")
    print(
        f"  Duration:            {summary['sequence_duration_frames']} frames "
        f"({summary['sequence_duration_s']:.2f}s @ {summary['fps']} fps)"
    )
    print(f"  Files:               {len(summary['files'])}")
    for warning in summary.get("warnings") or []:
        print(f"  [warning] {warning}")
    return 0
