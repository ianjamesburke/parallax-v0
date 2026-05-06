from __future__ import annotations

import argparse
import sys


def register_parser(sub: argparse._SubParsersAction) -> None:
    image_p = sub.add_parser("image", help="Image generation utilities.")
    image_sub = image_p.add_subparsers(dest="image_command", required=True)

    img_gen_p = image_sub.add_parser("generate", help="Generate an image from a prompt.")
    img_gen_p.add_argument("prompt", help="Generation prompt.")
    img_gen_p.add_argument(
        "--model", default="mid",
        help="Model alias (draft/mid/premium or a named alias). Default: mid.",
    )
    img_gen_p.add_argument(
        "--aspect", default=None,
        help="Aspect ratio, e.g. '9:16', '16:9', '1:1'. Default: model default.",
    )
    img_gen_p.add_argument(
        "--size", default=None,
        help="Explicit WxH, e.g. '1080x1920'. Overrides --aspect for sizing.",
    )
    img_gen_p.add_argument(
        "--ref", action="append", dest="refs", metavar="PATH",
        help="Reference image path (can repeat). Model must support references.",
    )
    img_gen_p.add_argument(
        "--out", default=None,
        help=(
            "Output path. If it ends with a recognized image extension "
            "(.png .jpg .jpeg .webp), the image is written to that exact file. "
            "Otherwise it is treated as an output directory and the image is "
            "written there with an auto-generated filename. Default: current directory."
        ),
    )

    img_analyze_p = image_sub.add_parser(
        "analyze", help="Describe or answer questions about an image using a vision model."
    )
    img_analyze_p.add_argument("path", help="Image file to analyze.")
    img_analyze_p.add_argument(
        "question", nargs="?", default=None,
        help="Optional question or instruction. Default: describe the image.",
    )
    img_analyze_p.add_argument(
        "--model", default="google/gemini-2.5-flash-preview",
        help="Vision model ID or alias. Default: google/gemini-2.5-flash-preview.",
    )


def run(args) -> int:
    if args.image_command == "generate":
        return _run_generate(args)
    if args.image_command == "analyze":
        return _run_analyze(args)
    return 2


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
    try:
        path = generate_image(
            args.prompt,
            args.model,
            reference_images=args.refs or [],
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
