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
    refs: Optional[List[str]] = typer.Option(None, "--ref", metavar="PATH", help="Reference image path (can repeat). Model must support references."),
    out: Optional[str] = typer.Option(None, "--out", help="Output path. If it ends with a recognized image extension, written to that exact file. Otherwise treated as output directory."),
) -> int:
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
    return _run_analyze(SimpleNamespace(
        image_command="analyze",
        path=path,
        question=question,
        model=model,
    ))


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _run_generate(args) -> int:
    from pathlib import Path
    from ..openrouter import generate_image, InsufficientCreditsError

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
            f"Current: {args.model}. Add --hq or --model premium to upgrade.",
            flush=True,
        )
    try:
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
        print(f"Error: image generation failed ({type(e).__name__}: {e})", file=sys.stderr)
        return 1
    print(path)
    return 0


def _run_analyze(args) -> int:
    from pathlib import Path
    from ..openrouter import analyze_image

    try:
        result = analyze_image(Path(args.path), question=args.question, model=args.model)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: analyze failed ({type(e).__name__}: {e})", file=sys.stderr)
        return 1
    print(result)
    return 0
