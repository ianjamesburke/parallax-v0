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
    return 2
