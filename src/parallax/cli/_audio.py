from __future__ import annotations

import argparse
import sys


def register_parser(sub: argparse._SubParsersAction) -> None:
    audio_p = sub.add_parser("audio", help="Audio utilities.")
    audio_sub = audio_p.add_subparsers(dest="audio_command", required=True)

    transcribe_p = audio_sub.add_parser(
        "transcribe",
        help="Transcribe audio or video to word-level timestamps JSON.",
    )
    transcribe_p.add_argument("input", help="Audio or video file to transcribe.")
    transcribe_p.add_argument("--out", required=True, help="Output path for words JSON.")
    transcribe_p.add_argument(
        "--no-whisperx", dest="no_whisperx", action="store_true", default=False,
        help="Use faster-whisper instead of WhisperX. Less precise timestamps; no install required.",
    )
    transcribe_p.add_argument(
        "--words", default=None,
        help="Path to existing words JSON — skips transcription, reformats and writes to --out.",
    )

    detect_p = audio_sub.add_parser(
        "detect-silences",
        help="List silent sections in audio — use output to choose a range for trim.",
    )
    detect_p.add_argument("input", help="Audio or video file to analyze.")
    detect_p.add_argument(
        "--min-silence", type=float, default=0.15,
        help="Minimum silence duration in seconds to report (default: 0.15).",
    )
    detect_p.add_argument(
        "--noise-db", type=float, default=-40.0,
        help="Noise floor in dB (default: -40).",
    )

    trim_p = audio_sub.add_parser(
        "trim",
        help="Remove a specific time range from plan audio, avatar, and words. Updates plan.yaml in-place.",
    )
    trim_p.add_argument("--plan", required=True, help="Path to plan.yaml.")
    trim_p.add_argument("--folder", required=True, help="Project folder (paths in plan are relative to this).")
    trim_p.add_argument("--start", type=float, required=True, help="Start of range to remove (seconds).")
    trim_p.add_argument("--end", type=float, required=True, help="End of range to remove (seconds).")

    cap_p = audio_sub.add_parser(
        "cap-pauses",
        help=(
            "Cap inter-word gaps to a max length using WhisperX word boundaries — "
            "trims long pauses without amplitude probing. Pure word-driven."
        ),
    )
    cap_p.add_argument("--input", "-i", required=True, help="Audio (or m4a/mp3) file to trim.")
    cap_p.add_argument("--output", "-o", required=True, help="Output wav path.")
    cap_p.add_argument(
        "--max-gap", type=float, default=0.75,
        help=(
            "Max allowed gap between adjacent words, in seconds (default: 0.75). "
            "Gaps longer than this are reduced to exactly this value, split half/half "
            "across the joint so 0.75 → 0.375s tail of prev word + 0.375s lead-in of next."
        ),
    )
    cap_p.add_argument(
        "--crossfade", type=float, default=0.05,
        help="Crossfade duration at each cut joint, in seconds (default: 0.05).",
    )
    cap_p.add_argument(
        "--words", default=None,
        help="Path to existing words JSON — skips WhisperX alignment.",
    )

    vo_p = audio_sub.add_parser(
        "voiceover",
        help="Synthesize a voiceover from text. Supports elevenlabs, tts-mini, tts-gemini backends.",
    )
    vo_p.add_argument("--text", default=None, help="Text to synthesize. Reads from stdin if omitted.")
    vo_p.add_argument("--out", required=True, help="Output audio file path (e.g. /tmp/out.mp3).")
    vo_p.add_argument("--voice", default="nova", help="Voice name or ElevenLabs voice ID (default: nova).")
    vo_p.add_argument(
        "--voice-model", dest="voice_model", default="tts-mini",
        help="TTS backend: tts-mini, tts-gemini, elevenlabs (default: tts-mini).",
    )
    vo_p.add_argument(
        "--speed", type=float, default=None,
        help="atempo multiplier applied after synthesis (e.g. 1.2 = 20%% faster).",
    )
    vo_p.add_argument(
        "--style", default=None,
        help="Delivery style preset: rapid_fire, fast, calm, natural. OpenRouter backends only.",
    )

    speed_p = audio_sub.add_parser(
        "speed",
        help="Apply ffmpeg atempo to retime an audio file. Use --rate <multiplier> or --by <pct%%.>",
    )
    speed_p.add_argument("--in", dest="in_path", required=True, help="Input audio file.")
    speed_p.add_argument("--out", dest="out_path", required=True, help="Output audio path.")
    rate_grp = speed_p.add_mutually_exclusive_group(required=True)
    rate_grp.add_argument("--rate", type=float, default=None, help="atempo multiplier (e.g. 1.3 = 30%% faster).")
    rate_grp.add_argument(
        "--by", type=str, default=None,
        help="Percent change with trailing %% — e.g. '30%%' (=1.3) or '-20%%' (=0.8).",
    )


def run(args) -> int:
    if args.audio_command == "voiceover":
        import shutil
        import tempfile
        from pathlib import Path as _P

        from ..voiceover import generate_voiceover_dict

        text = args.text or sys.stdin.read().strip()
        if not text:
            print("ERROR: provide --text or pipe text via stdin.", file=sys.stderr)
            return 1

        out_path = _P(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            result = generate_voiceover_dict(
                text=text,
                voice=args.voice,
                out_dir=tmp,
                style=args.style,
                voice_model=args.voice_model,
            )
            audio_src = _P(result["audio_path"])
            if args.speed and args.speed != 1.0:
                from ..audio import speedup
                speedup(audio_src, out_path, args.speed)
            else:
                shutil.copy2(audio_src, out_path)

        duration = result["total_duration_s"]
        print(f"voiceover: {duration:.2f}s → {out_path}")
        if args.speed and args.speed != 1.0:
            print(f"  speed: {args.speed:.2f}x applied")
        return 0

    if args.audio_command == "transcribe":
        from pathlib import Path
        from ..audio import transcribe_words
        preloaded = None
        if getattr(args, "words", None):
            import json as _json
            data = _json.loads(Path(args.words).read_text())
            preloaded = data if isinstance(data, list) else data.get("words", [])
        try:
            words = transcribe_words(args.input, args.out, no_whisperx=args.no_whisperx, words=preloaded)
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(f"{len(words)} words → {args.out}")
        if words:
            last = words[-1]
            print(f"  duration: {last['end']:.2f}s  last word: '{last['word']}' @ {last['end']:.2f}s")
        return 0

    if args.audio_command == "detect-silences":
        from ..audio import detect_silences
        silences = detect_silences(args.input, noise_db=args.noise_db, min_silence_s=args.min_silence)
        if not silences:
            print("No silences detected.")
            return 0
        print(f"{'#':<4} {'start':>8} {'end':>8} {'duration':>10}")
        print("-" * 36)
        for i, s in enumerate(silences):
            print(f"{i:<4} {s['start']:>8.3f} {s['end']:>8.3f} {s['duration']:>10.3f}s")
        print(
            f"\nTo remove silence #{0}: parallax audio trim --plan <plan.yaml> "
            f"--folder <folder> --start {silences[0]['start']} --end {silences[0]['end']}"
        )
        return 0

    if args.audio_command == "trim":
        from ..audio import trim_silence
        result = trim_silence(
            plan_path=args.plan,
            folder=args.folder,
            cut_start=args.start,
            cut_end=args.end,
        )
        removed = result["seconds_removed"]
        print(f"Removed {removed:.3f}s ({args.start:.3f}s–{args.end:.3f}s)")
        print(f"  audio  → {result['new_audio']}")
        print(f"  words  → {result['new_words']}")
        if result["new_avatar"]:
            print(f"  avatar → {result['new_avatar']}")
        print(f"plan.yaml updated. Run: parallax produce --folder {args.folder} --plan {args.plan}")
        return 0

    if args.audio_command == "speed":
        from pathlib import Path as _P
        from ..audio import speedup, parse_by_pct
        rate = args.rate if args.rate is not None else parse_by_pct(args.by)
        out = speedup(_P(args.in_path), _P(args.out_path), rate)
        print(f"audio speed: rate={rate:.4f} → {out}")
        return 0

    if args.audio_command == "cap-pauses":
        from pathlib import Path
        from ..audio import cap_pauses
        preloaded = None
        if getattr(args, "words", None):
            import json as _json
            data = _json.loads(Path(args.words).read_text())
            preloaded = data if isinstance(data, list) else data.get("words", [])
        result = cap_pauses(
            input_path=args.input,
            output_path=args.output,
            max_gap_s=args.max_gap,
            crossfade_s=args.crossfade,
            words=preloaded,
        )
        print(f"cap-pauses: {result['gaps_trimmed']} gaps capped to {result['max_gap_s']:.2f}s")
        print(
            f"  duration: {result['original_duration_s']:.2f}s → {result['new_duration_s']:.2f}s "
            f"({result['seconds_removed']:.2f}s removed)"
        )
        print(f"  output  → {result['output']}")
        return 0

    return 2
