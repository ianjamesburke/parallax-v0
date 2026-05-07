from __future__ import annotations

import sys
from typing import List, Optional

import typer


video_app = typer.Typer(
    help="Video utilities.",
    invoke_without_command=True,
    no_args_is_help=True,
)


@video_app.command("frame")
def video_frame(
    input: str = typer.Argument(..., help="Video file."),
    time: float = typer.Argument(..., help="Timestamp in seconds."),
    out: Optional[str] = typer.Option(None, "--out", help="Output image path (default: temp file)."),
) -> int:
    from ..video import extract_frame
    out_path = extract_frame(input, time, out)
    print(out_path)
    return 0


@video_app.command("color")
def video_color(
    input: str = typer.Argument(..., help="Video or image file."),
    time: float = typer.Option(2.0, "--time", help="Timestamp for video frames (default: 2.0)."),
    x: int = typer.Option(10, "--x", help="X pixel coordinate (default: 10)."),
    y: int = typer.Option(10, "--y", help="Y pixel coordinate (default: 10)."),
) -> int:
    from ..video import sample_color
    print(sample_color(input, x, y, time))
    return 0


@video_app.command("animate")
def video_animate(
    prompt: str = typer.Option(..., "--prompt", help="Animation prompt."),
    model: str = typer.Option("mid", "--model", help="Video model alias (default: mid)."),
    duration: float = typer.Option(5.0, "--duration", help="Duration in seconds (default: 5)."),
    out: Optional[str] = typer.Option(None, "--out", help="Output path (default: current directory)."),
    start: Optional[str] = typer.Option(None, "--start", metavar="IMAGE", help="Start frame image path."),
    ref: Optional[List[str]] = typer.Option(None, "--ref", metavar="IMAGE", help="Reference image (repeatable). Mutually exclusive with --start/--end."),
    end: Optional[str] = typer.Option(None, "--end", metavar="IMAGE", help="End frame image path. Requires --start."),
) -> int:
    # Validate mutual exclusion of --start and --ref
    if start is not None and ref:
        typer.echo("Error: --start and --ref are mutually exclusive", err=True)
        return 2

    if end and not start:
        print("error: --end requires --start", file=sys.stderr)
        return 2

    from pathlib import Path
    from ..openrouter import generate_video

    out_dir = Path(out) if out else Path.cwd()
    start_path = Path(start) if start else None
    end_path = Path(end) if end else None
    refs = [Path(r) for r in ref] if ref else None

    path = generate_video(
        prompt,
        model,
        image_path=start_path,
        end_image_path=end_path,
        input_references=refs,
        duration_s=duration,
        out_dir=out_dir,
    )
    print(path)
    return 0
