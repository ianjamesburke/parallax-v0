from __future__ import annotations

import argparse


def register_parser(sub: argparse._SubParsersAction) -> None:
    video_p = sub.add_parser("video", help="Video utilities.")
    video_sub = video_p.add_subparsers(dest="video_command", required=True)

    frame_p = video_sub.add_parser("frame", help="Extract a single frame from a video.")
    frame_p.add_argument("input", help="Video file.")
    frame_p.add_argument("time", type=float, help="Timestamp in seconds.")
    frame_p.add_argument("--out", default=None, help="Output image path (default: temp file).")

    color_p = video_sub.add_parser(
        "color", help="Sample a pixel color from a video or image. Prints 0xRRGGBB."
    )
    color_p.add_argument("input", help="Video or image file.")
    color_p.add_argument("--time", type=float, default=2.0, help="Timestamp for video frames (default: 2.0).")
    color_p.add_argument("--x", type=int, default=10, help="X pixel coordinate (default: 10).")
    color_p.add_argument("--y", type=int, default=10, help="Y pixel coordinate (default: 10).")

    anim_p = video_sub.add_parser(
        "animate", help="Generate a video from a prompt and optional frame/reference images."
    )
    anim_p.add_argument("--prompt", required=True, help="Animation prompt.")
    anim_p.add_argument("--model", default="mid", help="Video model alias (default: mid).")
    anim_p.add_argument("--duration", type=float, default=5.0, help="Duration in seconds (default: 5).")
    anim_p.add_argument("--out", default=None, help="Output path (default: current directory).")
    # --start and --ref are mutually exclusive input modes
    input_group = anim_p.add_mutually_exclusive_group()
    input_group.add_argument("--start", metavar="IMAGE", help="Start frame image path.")
    input_group.add_argument("--ref", metavar="IMAGE", action="append", dest="refs",
                             help="Reference image (repeatable). Mutually exclusive with --start/--end.")
    anim_p.add_argument("--end", metavar="IMAGE", default=None,
                        help="End frame image path. Requires --start.")


def run(args) -> int:
    if args.video_command == "frame":
        from ..video import extract_frame
        out = extract_frame(args.input, args.time, args.out)
        print(out)
        return 0
    if args.video_command == "color":
        from ..video import sample_color
        print(sample_color(args.input, args.x, args.y, args.time))
        return 0
    if args.video_command == "animate":
        from pathlib import Path
        from ..openrouter import generate_video

        if args.end and not args.start:
            import sys
            print("error: --end requires --start", file=sys.stderr)
            return 2

        out_dir = Path(args.out) if args.out else Path.cwd()
        start = Path(args.start) if args.start else None
        end = Path(args.end) if args.end else None
        refs = [Path(r) for r in args.refs] if args.refs else None

        path = generate_video(
            args.prompt,
            args.model,
            image_path=start,
            end_image_path=end,
            input_references=refs,
            duration_s=args.duration,
            out_dir=out_dir,
        )
        print(path)
        return 0
    return 2
