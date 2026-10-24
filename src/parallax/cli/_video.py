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
def video_assemble(shot: list[str] = typer.Option([], "--shot", help="clip-or-still[:duration], repeatable"), shots: Optional[str] = typer.Option(None, "--shots", help="JSON shot manifest; clip fields: path, trim_start, duration, speed"), voice: Optional[str] = typer.Option(None,"--voice"), voice_offset: float = typer.Option(0,"--voice-offset"), words: Optional[str] = typer.Option(None,"--words"), captions: bool = typer.Option(False,"--captions/--no-captions"), caption_style: str = typer.Option("anton","--caption-style"), caption_y: float = typer.Option(.65,"--caption-y", min=0., max=1.), report: Optional[str] = typer.Option(None,"--report"), caption_size: int = typer.Option(55,"--caption-size",help="Caption font px at 1080 wide; layout still shrinks to the safe area"), words_per_chunk: str = typer.Option("smart","--words-per-chunk",help="N words per caption, or smart"), aspect: str = typer.Option("9:16","--aspect"), resolution: Optional[str] = typer.Option(None,"--resolution"), out: str = typer.Option(...,"--out")) -> int:
    """Normalize and concatenate local shots; optionally mix VO and captions."""
    from ..local_video import assemble
    items = json.loads(open(shots).read()) if shots else [{"path": x.rsplit(":",1)[0], **({"duration":float(x.rsplit(":",1)[1])} if ":" in x and x.rsplit(":",1)[1].replace('.','',1).isdigit() else {})} for x in shot]
    if not items: raise typer.BadParameter("provide --shot or --shots")
    print(assemble(items,out,aspect=aspect,resolution_=resolution,voice=voice,voice_offset=voice_offset,captions=captions,words=words,caption_style=caption_style,caption_y=caption_y,report=report,caption_size=caption_size,words_per_chunk=(int(words_per_chunk) if words_per_chunk.isdigit() else words_per_chunk))); return 0

@video_app.command("captions")
def video_captions(video: str = typer.Option(...,"--video"), words: str = typer.Option(...,"--words"), offset: float = typer.Option(0,"--offset"), style: str = typer.Option("anton","--style"), caption_y: float = typer.Option(.65,"--caption-y", min=0., max=1.), report: Optional[str] = typer.Option(None,"--report"), caption_size: int = typer.Option(55,"--caption-size",help="Caption font px at 1080 wide; layout still shrinks to the safe area"), words_per_chunk: str = typer.Option("smart","--words-per-chunk",help="N words per caption, or smart"), out: str = typer.Option(...,"--out")) -> int:
    """Burn safe-area captions over an existing local video."""
    from ..captions import burn_captions
    print(burn_captions(video,words,out,caption_style=style,shift_s=offset,caption_y=caption_y,report_path=report,fontsize=caption_size,words_per_chunk=(int(words_per_chunk) if words_per_chunk.isdigit() else words_per_chunk))); return 0

@video_app.command("text-overlay")
def video_text_overlay(input: Optional[str] = typer.Option(None,"--in"), bg_color: Optional[str] = typer.Option(None,"--bg-color"), text: str = typer.Option(...,"--text"), preset: str = typer.Option("pop","--preset"), position: str = typer.Option("center","--position"), y: Optional[float] = typer.Option(None,"--y", min=0., max=1.), size: int = typer.Option(64,"--size"), color: str = typer.Option("#ffffff","--color"), duration: float = typer.Option(...,"--duration"), out: str = typer.Option(...,"--out"), aspect: str = typer.Option("9:16","--aspect"), resolution: Optional[str] = typer.Option(None,"--resolution")) -> int:
    """Render zero-spend kinetic text over a still or clip; output is silent MP4."""
    from ..local_video import text_overlay
    if preset not in {"pop","count-up","slide-up","typewriter"}: raise typer.BadParameter("preset must be pop, count-up, slide-up, or typewriter")
    if position not in {"center","upper","lower"}: raise typer.BadParameter("position must be center, upper, or lower")
    if not input and not bg_color: raise typer.BadParameter("provide --in or --bg-color")
    if bg_color and not input:
        from PIL import Image
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.png') as f:
            Image.new('RGB',(1080,1920),bg_color).save(f.name); print(text_overlay(f.name,text,duration,out,preset=preset,position=position,y=y,size=size,color=color,aspect=aspect,resolution_=resolution))
    else: print(text_overlay(input,text,duration,out,preset=preset,position=position,y=y,size=size,color=color,aspect=aspect,resolution_=resolution))
    return 0

@video_app.command("app-screen")
def video_app_screen(screen:str=typer.Option(...,"--screen"),duration:float=typer.Option(...,"--duration"),out:str=typer.Option(...,"--out"),bg:str=typer.Option("#272137","--bg"),bg_image:Optional[str]=typer.Option(None,"--bg-image"),callout:Optional[str]=typer.Option(None,"--callout"),highlight:Optional[str]=typer.Option(None,"--highlight"),zoom_to_highlight:bool=typer.Option(False,"--zoom-to-highlight"),accent:str=typer.Option("#721CFF","--accent"),title:Optional[str]=typer.Option(None,"--title"),full_bleed:bool=typer.Option(False,"--full-bleed",help="Screenshot fills the frame (App Store shots that already include a device frame)."),callout_y:Optional[float]=typer.Option(None,"--callout-y",min=0.,max=1.),resolution:str=typer.Option("1080x1920","--resolution")):
    """Real app screenshot (pixels as-is) with push-in, highlight box and kinetic callout. Zero spend."""
    from ..local_video import app_screen
    box=tuple(map(float,highlight.split(','))) if highlight else None; print(app_screen(screen,duration,out,bg=bg,bg_image=bg_image,callout=callout,highlight=box,zoom_to_highlight=zoom_to_highlight,accent=accent,title=title,full_bleed=full_bleed,callout_y=callout_y,resolution_=resolution)); return 0

@video_app.command("end-card")
def video_end_card(logo:str=typer.Option(...,"--logo"),duration:float=typer.Option(...,"--duration"),out:str=typer.Option(...,"--out"),icon:Optional[str]=typer.Option(None,"--icon"),headline:str=typer.Option("Become a morning person","--headline"),cta:str=typer.Option("Try RISE free","--cta"),sub:str=typer.Option("Download on the App Store","--sub"),bg:str=typer.Option("#14102A","--bg"),accent:str=typer.Option("#721CFF","--accent"),colors:Optional[str]=typer.Option(None,"--colors"),resolution:str=typer.Option("1080x1920","--resolution")):
    """Animated brand CTA end card (icon pop, wordmark, headline, pulsing CTA pill). Zero spend."""
    from ..local_video import end_card
    print(end_card(logo,duration,out,icon=icon,headline=headline,cta=cta,sub=sub,bg=bg,accent=accent,colors=colors,resolution_=resolution)); return 0

@video_app.command("diagram")
def video_diagram(preset:str=typer.Option(...,"--preset"),duration:float=typer.Option(...,"--duration"),out:str=typer.Option(...,"--out"),label:str=typer.Option("YOUR WINDOW","--label"),value:float=typer.Option(75,"--value"),colors:Optional[str]=typer.Option(None,"--colors"),bg:str=typer.Option("#14102A","--bg"),sub:Optional[str]=typer.Option(None,"--sub",help="clock-window: pill under the clock"),unit:str=typer.Option("","--unit"),display:Optional[str]=typer.Option(None,"--display",help="meter: big number text; may use {v:.1f}"),max_value:float=typer.Option(100.,"--max",help="meter: full-scale value"),peak:str=typer.Option("PEAK","--peak"),dip:str=typer.Option("DIP","--dip"),resolution:str=typer.Option("1080x1920","--resolution")):
    """Animated explainer diagram: clock-window | meter | curve. Zero spend."""
    if preset not in {'clock-window','meter','curve'}: raise typer.BadParameter('preset must be clock-window, meter, or curve')
    from ..local_video import diagram
    print(diagram(preset,duration,out,label=label,value=value,colors=colors,bg=bg,sub=sub,unit=unit,display=display,max_value=max_value,peak=peak,dip=dip,resolution_=resolution)); return 0


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
        # With an explicit --out, only that file counts: the parent dir may be shared by
        # concurrent animate runs, and globbing it misattributed a sibling's clip.
        paid_output = (out_file if out_file.is_file() else None) if out_file is not None else next(
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


@video_app.command("motion")
def video_motion(
    style: Optional[str] = typer.Option(None, "--style", help="Motion graphic style preset name (see --list-styles)."),
    brand: Optional[str] = typer.Option(None, "--brand", help="Brand YAML or JSON file path."),
    text: Optional[str] = typer.Option(None, "--text", "--prompt", help="Scene copy / voiceover text."),
    words: Optional[str] = typer.Option(None, "--words", help="Word-timestamp JSON file."),
    duration: Optional[float] = typer.Option(None, "--duration", help="Duration in seconds (default: from --words or 5.0)."),
    fps: int = typer.Option(30, "--fps", help="Frame rate (default: 30)."),
    model: Optional[str] = typer.Option(None, "--model", help="OpenRouter LLM model (default: google/gemini-2.5-flash)."),
    from_html: Optional[str] = typer.Option(None, "--from-html", help="Re-time and re-render an existing HTML scene with zero LLM calls."),
    from_spec: Optional[str] = typer.Option(None, "--from-spec", help="Re-render an existing SceneSpec JSON file with zero LLM calls ($0)."),
    out: Optional[str] = typer.Option(None, "--out", help="Output MP4 path."),
    html_out: Optional[str] = typer.Option(None, "--html-out", help="Output HTML path (default: <out>.html next to the MP4)."),
    list_styles: bool = typer.Option(False, "--list-styles", help="List available motion graphic style presets and exit."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> int:
    """Render a styled HTML/SVG + GSAP motion graphic scene to MP4 (or list styles / re-render from SceneSpec or HTML)."""
    from pathlib import Path
    from ..html_motion import (
        LayoutViolationError,
        SceneSpecValidationError,
        _save_rejected_html,
        audit_and_repair_scene,
        audit_layout,
        estimate_motion_cost_usd,
        generate_scene_spec_and_html,
        inline_gsap_script,
        list_style_presets,
        load_brand_file,
        load_scene_spec,
        load_style_preset,
        normalize_words,
        patch_html_timing,
        render_html_to_mp4,
        render_scene_spec_html,
        resolve_motion_model,
        save_scene_spec,
    )
    from ..preflight import print_estimate
    from ..shim import is_test_mode

    if list_styles:
        presets = list_style_presets()
        if json_output:
            print(json.dumps(presets, indent=2))
        else:
            for p in presets:
                pal = p.get("palette") or {}
                pal_summary = ", ".join(f"{k}={v}" for k, v in pal.items())
                print(f"{p['name']}: {p.get('description', '')} ({pal_summary})")
        return 0

    if not out:
        raise typer.BadParameter("provide --out <scene.mp4> (or use --list-styles)")

    words_list = []
    inferred_dur: float | None = None
    if words:
        words_path = Path(words).expanduser()
        if not words_path.is_absolute():
            words_path = Path.cwd() / words_path
        if not words_path.is_file():
            raise typer.BadParameter(f"words file not found: {words}")
        raw_words_data = json.loads(words_path.read_text(encoding="utf-8"))
        words_list = normalize_words(raw_words_data)
        if isinstance(raw_words_data, dict) and raw_words_data.get("total_duration_s"):
            inferred_dur = float(raw_words_data["total_duration_s"])
        elif words_list:
            inferred_dur = float(words_list[-1]["end"])

    duration_s = float(duration) if duration is not None else (inferred_dur if inferred_dur and inferred_dur > 0 else 5.0)
    out_mp4 = Path(out).expanduser()
    if not out_mp4.is_absolute():
        out_mp4 = Path.cwd() / out_mp4
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    if html_out:
        target_html = Path(html_out).expanduser()
        if not target_html.is_absolute():
            target_html = Path.cwd() / target_html
    else:
        target_html = out_mp4.with_suffix(".html")
    target_html.parent.mkdir(parents=True, exist_ok=True)

    brand_dict = None
    if brand:
        brand_path = Path(brand).expanduser()
        if not brand_path.is_absolute():
            brand_path = Path.cwd() / brand_path
        try:
            brand_dict = load_brand_file(brand_path)
        except (FileNotFoundError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc

    if from_spec:
        src_spec_path = Path(from_spec).expanduser()
        if not src_spec_path.is_absolute():
            src_spec_path = Path.cwd() / src_spec_path
        if not src_spec_path.is_file():
            raise typer.BadParameter(f"--from-spec file not found: {from_spec}")
        try:
            spec = load_scene_spec(src_spec_path, style=style)
            resolved_style = style or str(spec.get("style") or "flat-explainer")
            load_style_preset(resolved_style)
        except (FileNotFoundError, ValueError, SceneSpecValidationError, json.JSONDecodeError) as exc:
            raise typer.BadParameter(str(exc)) from exc

        rendered_html = render_scene_spec_html(
            spec,
            style=resolved_style,
            brand=brand_dict,
            duration_s=duration_s,
            words=words_list,
            fps=fps,
        )
        target_html.write_text(rendered_html, encoding="utf-8")
        target_spec = save_scene_spec(spec, out_mp4)
        if target_html.with_suffix(".spec.json") != target_spec:
            save_scene_spec(spec, target_html)
        audit = audit_layout(target_html, duration_s=duration_s)
        if not audit.passed:
            rejected_path = _save_rejected_html(rendered_html, out_mp4)
            raise LayoutViolationError(audit.violations, rejected_html_path=rejected_path)
        render_html_to_mp4(target_html, out_mp4, duration_s=duration_s, fps=fps)
        if json_output:
            print(
                json.dumps(
                    {
                        "out": str(out_mp4),
                        "html_path": str(target_html),
                        "spec_path": str(target_spec),
                        "style": resolved_style,
                        "duration_s": duration_s,
                        "fps": fps,
                        "retime_only": True,
                    }
                )
            )
        else:
            print(out_mp4)
        return 0

    if from_html:
        src_html_path = Path(from_html).expanduser()
        if not src_html_path.is_absolute():
            src_html_path = Path.cwd() / src_html_path
        if not src_html_path.is_file():
            raise typer.BadParameter(f"--from-html file not found: {from_html}")
        raw_html = src_html_path.read_text(encoding="utf-8")
        patched_html = inline_gsap_script(
            patch_html_timing(raw_html, duration=duration_s, fps=fps, words=words_list)
        )
        target_html.write_text(patched_html, encoding="utf-8")
        audit = audit_layout(target_html, duration_s=duration_s)
        if not audit.passed:
            rejected_path = _save_rejected_html(patched_html, out_mp4)
            raise LayoutViolationError(audit.violations, rejected_html_path=rejected_path)
        render_html_to_mp4(target_html, out_mp4, duration_s=duration_s, fps=fps)
        if json_output:
            print(
                json.dumps(
                    {
                        "out": str(out_mp4),
                        "html_path": str(target_html),
                        "duration_s": duration_s,
                        "fps": fps,
                        "retime_only": True,
                    }
                )
            )
        else:
            print(out_mp4)
        return 0

    if not style:
        raise typer.BadParameter("provide --style <name>, --from-spec <scene.spec.json>, or --from-html <scene.html>")
    if not text:
        raise typer.BadParameter("provide --text '<scene copy>' when generating a motion scene")

    try:
        load_style_preset(style)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    resolved_model = resolve_motion_model(model)
    if not is_test_mode():
        print_estimate(
            f"video motion --style {style} --model {resolved_model}",
            estimate_motion_cost_usd(),
        )

    spec, html = generate_scene_spec_and_html(
        scene_text=text,
        style=style,
        brand=brand_dict,
        duration_s=duration_s,
        words=words_list,
        model=resolved_model,
    )
    spec, html, _audit = audit_and_repair_scene(
        spec,
        html,
        duration_s=duration_s,
        style=style,
        brand=brand_dict,
        words=words_list,
        model=resolved_model,
        out_path=out_mp4,
        scene_text=text,
    )
    target_html.write_text(html, encoding="utf-8")
    target_spec = save_scene_spec(spec, out_mp4)
    if target_html.with_suffix(".spec.json") != target_spec:
        save_scene_spec(spec, target_html)
    render_html_to_mp4(target_html, out_mp4, duration_s=duration_s, fps=fps)

    if json_output:
        print(
            json.dumps(
                {
                    "out": str(out_mp4),
                    "html_path": str(target_html),
                    "spec_path": str(target_spec),
                    "style": style,
                    "model": resolved_model,
                    "duration_s": duration_s,
                    "fps": fps,
                    "retime_only": False,
                }
            )
        )
    else:
        print(out_mp4)
    return 0
