from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import List, Optional

import typer


image_app = typer.Typer(
    help="Image generation utilities.",
    invoke_without_command=True,
    no_args_is_help=True,
)


@image_app.command("generate")
def image_generate(
    prompt: str = typer.Argument(..., help="Generation prompt."),
    model: str = typer.Option("mid", "--model", help="Model alias (draft/mid/premium or a named alias). Default: mid."),
    aspect: Optional[str] = typer.Option(None, "--aspect", help="Aspect ratio, e.g. '9:16', '16:9', '1:1'. Default: model default."),
    size: Optional[str] = typer.Option(None, "--size", help="Explicit WxH, e.g. '1080x1920'. Overrides --aspect for sizing."),
    refs: Optional[List[str]] = typer.Option(None, "--ref", "--reference", metavar="PATH", help="Reference image path (can repeat). Model must support references."),
    out: Optional[str] = typer.Option(None, "--out", "--output", "-o", help="Output path. If it ends with a recognized image extension, written to that exact file. Otherwise treated as output directory."),
) -> int:
    """Generate one image. Spends money; prints the estimated cost first."""
    return _run_generate(SimpleNamespace(
        image_command="generate",
        prompt=prompt,
        model=model,
        aspect=aspect,
        size=size,
        refs=refs,
        out=out,
    ))


@image_app.command("analyze")
def image_analyze(
    path: str = typer.Argument(..., help="Image file to analyze."),
    question: Optional[str] = typer.Argument(default=None, help="Optional question or instruction. Default: describe the image."),
    model: str = typer.Option("google/gemini-2.5-flash-preview", "--model", help="Vision model ID or alias."),
) -> int:
    """Describe an image with a vision model. Spends money; prints the estimated cost first."""
    return _run_analyze(SimpleNamespace(
        image_command="analyze",
        path=path,
        question=question,
        model=model,
    ))


@image_app.command("crop-grid")
def image_crop_grid(
    image: str = typer.Argument(..., help="Numbered grid image to crop."),
    rows: int = typer.Option(..., "--rows", min=1, help="Grid row count."),
    cols: int = typer.Option(..., "--cols", min=1, help="Grid column count."),
    pick: int = typer.Option(..., "--pick", min=1, help="1-based cell number, row-major."),
    out: Optional[str] = typer.Option(None, "--out", "--output", help="Exact output image for --pick."),
    all_: Optional[str] = typer.Option(None, "--all", help="Directory to write every cell."),
) -> int:
    """Crop a numbered character-option grid locally; no provider call."""
    from pathlib import Path
    from PIL import Image

    if pick > rows * cols:
        typer.echo(f"Error: --pick must be between 1 and {rows * cols}", err=True)
        return 2
    if out is None and all_ is None:
        typer.echo("Error: pass --out for the picked panel and/or --all for every panel", err=True)
        return 2
    try:
        source = Image.open(image)
        width, height = source.size
        def cell(n: int):
            index = n - 1
            r, c = divmod(index, cols)
            # Integer boundaries preserve every pixel even when dimensions do not divide evenly.
            return source.crop((c * width // cols, r * height // rows,
                                (c + 1) * width // cols, (r + 1) * height // rows))
        written: list[Path] = []
        if all_:
            target = Path(all_)
            target.mkdir(parents=True, exist_ok=True)
            for n in range(1, rows * cols + 1):
                path = target / f"panel-{n}.png"
                cell(n).save(path)
                written.append(path)
        if out:
            target = Path(out)
            target.parent.mkdir(parents=True, exist_ok=True)
            cell(pick).save(target)
            written.append(target)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: crop-grid failed ({type(exc).__name__}: {exc})", err=True)
        return 1
    for path in written:
        typer.echo(path)
    return 0


# Reservation ceiling (not a price) for one vision call: OpenRouter returns no price before the call.
VISION_CALL_ESTIMATE_USD = 0.01
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _run_generate(args) -> int:
    from pathlib import Path
    from ..models import resolve
    from ..openrouter import generate_image, InsufficientCreditsError
    from ..shim import is_test_mode
    from ..preflight import print_estimate

    out_file: Path | None = None
    out_dir: Path | None = None
    if args.out:
        p = Path(args.out).expanduser()
        if p.suffix.lower() in _IMAGE_EXTENSIONS:
            out_file = p
        else:
            out_dir = p
            out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = Path.cwd()
    refs = args.refs or []
    if len(refs) >= 2 and args.model != "premium":
        print(
            f"Advisory: multiple reference images work best with --model premium (Gemini 3 Pro).\n"
            f"Current: {args.model}. Pass --model premium to upgrade.",
            flush=True,
        )
    try:
        estimate = resolve(args.model, kind="image").cost_usd + (VISION_CALL_ESTIMATE_USD if refs else 0.0)
        if not is_test_mode():
            print_estimate(f"image generate --model {args.model}", estimate)
        path = generate_image(
            args.prompt,
            args.model,
            reference_images=refs,
            out_dir=out_dir,
            out_file=out_file,
            size=args.size,
            aspect_ratio=args.aspect,
        )
    except InsufficientCreditsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        saved_at = out_file.parent if out_file is not None else out_dir
        if saved_at is not None and any(p.is_file() for p in saved_at.glob("*")):
            print(f"Paid assets are saved at {saved_at}. Rerunning the same command resumes and reuses them; do not regenerate them.", file=sys.stderr)
        print(f"Error: image generation failed ({type(e).__name__}: {e})", file=sys.stderr)
        return 1
    print(path)
    return 0


def _run_analyze(args) -> int:
    from pathlib import Path
    from ..openrouter import analyze_image
    from ..preflight import print_estimate

    try:
        print_estimate(f"image analyze --model {args.model}", VISION_CALL_ESTIMATE_USD)
        result = analyze_image(Path(args.path), question=args.question, model=args.model)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: analyze failed ({type(e).__name__}: {e})", file=sys.stderr)
        return 1
    print(result)
    return 0
