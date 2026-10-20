from __future__ import annotations

import sys
import json
from typing import List, Optional

import typer


video_app = typer.Typer(
    help="Video utilities.",
    invoke_without_command=True,
    no_args_is_help=True,
)

@video_app.command("ken-burns")
def video_ken_burns(image: str = typer.Option(..., "--image"), duration: float = typer.Option(..., "--duration"), move: str = typer.Option("in", "--move"), zoom: float = typer.Option(1.12, "--zoom"), aspect: str = typer.Option("9:16", "--aspect"), fps: int = typer.Option(30, "--fps"), out: str = typer.Option(..., "--out")) -> int:
    """Render a smooth, silent Ken Burns clip locally (no provider call)."""
    from ..local_video import ken_burns
    if move not in {"in","out","pan-left","pan-right","pan-up","pan-down"}: raise typer.BadParameter("move must be in, out, pan-left, pan-right, pan-up, or pan-down")
    print(ken_burns(image, duration, out, move=move, zoom=zoom, aspect=aspect, fps=fps)); return 0

@video_app.command("assemble")
def video_assemble(shot: list[str] = typer.Option([], "--shot", help="clip-or-still[:duration], repeatable"), shots: Optional[str] = typer.Option(None, "--shots", help="JSON shot manifest; clip fields: path, trim_start, duration, speed"), voice: Optional[str] = typer.Option(None,"--voice"), voice_offset: float = typer.Option(0,"--voice-offset"), words: Optional[str] = typer.Option(None,"--words"), captions: bool = typer.Option(False,"--captions/--no-captions"), caption_style: str = typer.Option("anton","--caption-style"), caption_y: float = typer.Option(.65,"--caption-y", min=0., max=1.), report: Optional[str] = typer.Option(None,"--report"), aspect: str = typer.Option("9:16","--aspect"), resolution: Optional[str] = typer.Option(None,"--resolution"), out: str = typer.Option(...,"--out")) -> int:
    """Normalize and concatenate local shots; optionally mix VO and captions."""
    from ..local_video import assemble
    items = json.loads(open(shots).read()) if shots else [{"path": x.rsplit(":",1)[0], **({"duration":float(x.rsplit(":",1)[1])} if ":" in x and x.rsplit(":",1)[1].replace('.','',1).isdigit() else {})} for x in shot]
    if not items: raise typer.BadParameter("provide --shot or --shots")
    print(assemble(items,out,aspect=aspect,resolution_=resolution,voice=voice,voice_offset=voice_offset,captions=captions,words=words,caption_style=caption_style,caption_y=caption_y,report=report)); return 0

@video_app.command("captions")
def video_captions(video: str = typer.Option(...,"--video"), words: str = typer.Option(...,"--words"), offset: float = typer.Option(0,"--offset"), style: str = typer.Option("anton","--style"), caption_y: float = typer.Option(.65,"--caption-y", min=0., max=1.), report: Optional[str] = typer.Option(None,"--report"), out: str = typer.Option(...,"--out")) -> int:
    """Burn safe-area captions over an existing local video."""
    from ..captions import burn_captions
    print(burn_captions(video,words,out,caption_style=style,shift_s=offset,caption_y=caption_y,report_path=report)); return 0

@video_app.command("text-overlay")
def video_text_overlay(input: str = typer.Option(...,"--in"), text: str = typer.Option(...,"--text"), preset: str = typer.Option("pop","--preset"), position: str = typer.Option("center","--position"), y: Optional[float] = typer.Option(None,"--y", min=0., max=1.), size: int = typer.Option(64,"--size"), color: str = typer.Option("#ffffff","--color"), duration: float = typer.Option(...,"--duration"), out: str = typer.Option(...,"--out"), aspect: str = typer.Option("9:16","--aspect"), resolution: Optional[str] = typer.Option(None,"--resolution")) -> int:
    """Render zero-spend kinetic text over a still or clip; output is silent MP4."""
    from ..local_video import text_overlay
    if preset not in {"pop","count-up","slide-up","typewriter"}: raise typer.BadParameter("preset must be pop, count-up, slide-up, or typewriter")
    if position not in {"center","upper","lower"}: raise typer.BadParameter("position must be center, upper, or lower")
    print(text_overlay(input,text,duration,out,preset=preset,position=position,y=y,size=size,color=color,aspect=aspect,resolution_=resolution)); return 0


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

@video_app.command("resume")
def video_resume(job: str = typer.Option(..., "--job"), out: Optional[str] = typer.Option(None, "--out")) -> int:
    """Resume polling an accepted animation job; never submits a new request."""
    from pathlib import Path
    from ..openrouter import resume_video_job
    receipt = Path(job); payload = json.loads(receipt.read_text())
    target = Path(out) if out else Path(payload.get("out", receipt.parent))
    directory = target.parent if target.suffix else target
    path = resume_video_job(receipt, directory)
    if target.suffix: Path(path).replace(target); path = target
    print(path); return 0


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
    allow_fallback: bool = typer.Option(False, "--allow-fallback", help="Permit a more expensive/capability-reduced provider fallback."),
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
    existing_output_files = set(out_dir.glob("*"))
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
        print_estimate(
            f"video animate --model {model}",
            spec.estimate_video_usd(duration, image_conditioned=start_path is not None or end_path is not None),
        )
    job_path = Path(str(out_file or (out_dir / "animation")) + ".job.json")
    def persist_job(job: dict) -> None:
        # This callback is invoked immediately after the provider's accepted submit,
        # before the first poll, so an interrupted process leaves a recoverable receipt.
        payload = {**job, "out": str(out_file or out_dir), "estimate_usd": spec.estimate_video_usd(duration, image_conditioned=start_path is not None or end_path is not None)}
        job_path.write_text(json.dumps(payload, indent=2))
        try:
            from .. import runlog
            runlog.event("video.animate.submitted", generation_id=job.get("id"), polling_url=job.get("polling_url"), model=spec.alias, out_path=payload["out"], estimate_usd=payload["estimate_usd"])
        except Exception: pass
    try:
        if job_path.exists():
            from ..openrouter import resume_video_job
            path = resume_video_job(job_path, out_dir)
        else:
            path = generate_video(
                prompt, model, image_path=start_path, end_image_path=end_path,
                input_references=refs, duration_s=duration, out_dir=out_dir,
                allow_fallback=allow_fallback, on_submit=persist_job,
            )
        if out_file is not None:
            Path(path).replace(out_file)
            path = out_file
    except Exception as exc:
        # A failed submit has no paid result to recover. Only promise recovery
        # when this invocation has an actual video output to point at.
        paid_output = out_file if out_file is not None and out_file.is_file() else next(
            (p for p in out_dir.glob("*")
             if p not in existing_output_files and p.is_file()
             and p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}),
            None,
        )
        if paid_output is not None:
            print(f"Paid output is saved at {paid_output}. Rerunning the same command resumes and reuses it; do not regenerate it.", file=sys.stderr)
        if "InputImageSensitiveContentDetected.PrivacyInformation" in str(exc):
            print("Hint: Seedance rejected an input image as a possible real person; hide faces / stylize, or choose another model explicitly (e.g. --model kling, pricier).", file=sys.stderr)
        print(f"Error: video animation failed ({type(exc).__name__}: {str(exc) or 'no message'})", file=sys.stderr)
        return 1
    print(path)
    # Dispatcher writes the provider job id and billed usage alongside every
    # result. Surface that receipt immediately instead of making callers dig
    # through ~/.parallax/usage.ndjson.
    try:
        from ..usage import load_records
        record = next((r for r in reversed(load_records(include_test=True)) if r.get("output_path") == str(path)), None)
        if record:
            job = ", ".join(record.get("generation_ids") or []) or "(provider did not return an id)"
            print(f"generation id: {job}")
            print(f"actual billed cost: ${float(record.get('cost_usd', 0.0)):.4f} ({record.get('cost_source')})")
    except Exception:
        pass
    return 0
