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
    """Animate one clip. Spends money; prints the estimated cost first."""
    # Validate mutual exclusion of --start and --ref
    if start is not None and ref:
        typer.echo("Error: --start and --ref are mutually exclusive", err=True)
        return 2

    if end and not start:
        print("error: --end requires --start", file=sys.stderr)
        return 2

    from pathlib import Path
    from ..models import resolve
    from ..openrouter import generate_video
    from ..preflight import print_estimate
    from ..shim import is_test_mode

    out_file: Path | None = None
    out_path = Path(out) if out else None
    if out_path is not None and not out_path.is_dir() and out_path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}:
        out_file = out_path
        out_dir = out_file.parent
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = out_path if out_path is not None else Path.cwd()
        out_dir.mkdir(parents=True, exist_ok=True)
    start_path = Path(start) if start else None
    end_path = Path(end) if end else None
    refs = [Path(r) for r in ref] if ref else None

    spec = resolve(model, kind="video")
    if end and not spec.end_frame:
        from ..models import VIDEO_MODELS
        supported = ", ".join(sorted(s.alias for s in VIDEO_MODELS.values() if s.end_frame))
        print(f"Error: video model {model!r} does not support end frames. Choose one of: {supported}.", file=sys.stderr)
        return 2
    if not is_test_mode():
        print_estimate(f"video animate --model {model}", spec.estimate_video_usd(duration, end_frame=end_path is not None))
    try:
        path = generate_video(
            prompt, model, image_path=start_path, end_image_path=end_path,
            input_references=refs, duration_s=duration, out_dir=out_dir,
        )
        if out_file is not None:
            Path(path).replace(out_file)
            path = out_file
    except Exception as exc:
        assets = [p for p in out_dir.glob("*") if p.is_file()]
        if assets:
            print(f"Paid assets are saved at {out_dir}. Rerunning the same command resumes and reuses them; do not regenerate them.", file=sys.stderr)
        print(f"Error: video animation failed ({type(exc).__name__}: {str(exc) or 'no message'})", file=sys.stderr)
        return 1
    print(path)
    return 0
