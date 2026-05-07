from __future__ import annotations

import sys
from typing import Optional

import typer


audio_app = typer.Typer(
    help="Audio utilities.",
    invoke_without_command=True,
    no_args_is_help=True,
)


@audio_app.command("transcribe")
def audio_transcribe(
    input: str = typer.Argument(..., help="Audio or video file to transcribe."),
    out: str = typer.Option(..., "--out", help="Output path for words JSON."),
    no_whisperx: bool = typer.Option(False, "--no-whisperx", help="Use faster-whisper instead of WhisperX."),
    words: Optional[str] = typer.Option(None, "--words", help="Path to existing words JSON — skips transcription."),
) -> int:
    from pathlib import Path
    from ..audio import transcribe_words
    preloaded = None
    if words:
        import json as _json
        data = _json.loads(Path(words).read_text())
        preloaded = data if isinstance(data, list) else data.get("words", [])
    try:
        result = transcribe_words(input, out, no_whisperx=no_whisperx, words=preloaded)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"{len(result)} words → {out}")
    if result:
        last = result[-1]
        print(f"  duration: {last['end']:.2f}s  last word: '{last['word']}' @ {last['end']:.2f}s")
    return 0


@audio_app.command(name="detect-silences")
def audio_detect_silences(
    input: str = typer.Argument(..., help="Audio or video file to analyze."),
    min_silence: float = typer.Option(0.15, "--min-silence", help="Minimum silence duration in seconds to report (default: 0.15)."),
    noise_db: float = typer.Option(-40.0, "--noise-db", help="Noise floor in dB (default: -40)."),
) -> int:
    from ..audio import detect_silences
    silences = detect_silences(input, noise_db=noise_db, min_silence_s=min_silence)
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


@audio_app.command("trim")
def audio_trim(
    plan: str = typer.Option(..., "--plan", help="Path to plan.yaml."),
    folder: str = typer.Option(..., "--folder", help="Project folder (paths in plan are relative to this)."),
    start: float = typer.Option(..., "--start", help="Start of range to remove (seconds)."),
    end: float = typer.Option(..., "--end", help="End of range to remove (seconds)."),
) -> int:
    from ..audio import trim_silence
    result = trim_silence(
        plan_path=plan,
        folder=folder,
        cut_start=start,
        cut_end=end,
    )
    removed = result["seconds_removed"]
    print(f"Removed {removed:.3f}s ({start:.3f}s–{end:.3f}s)")
    print(f"  audio  → {result['new_audio']}")
    print(f"  words  → {result['new_words']}")
    if result["new_avatar"]:
        print(f"  avatar → {result['new_avatar']}")
    print(f"plan.yaml updated. Run: parallax produce --folder {folder} --plan {plan}")
    return 0


@audio_app.command(name="cap-pauses")
def audio_cap_pauses(
    input: str = typer.Option(..., "--input", "-i", help="Audio (or m4a/mp3) file to trim."),
    output: str = typer.Option(..., "--output", "-o", help="Output wav path."),
    max_gap: float = typer.Option(0.75, "--max-gap", help="Max allowed gap between adjacent words, in seconds (default: 0.75)."),
    crossfade: float = typer.Option(0.05, "--crossfade", help="Crossfade duration at each cut joint, in seconds (default: 0.05)."),
    words: Optional[str] = typer.Option(None, "--words", help="Path to existing words JSON — skips WhisperX alignment."),
) -> int:
    from pathlib import Path
    from ..audio import cap_pauses
    preloaded = None
    if words:
        import json as _json
        data = _json.loads(Path(words).read_text())
        preloaded = data if isinstance(data, list) else data.get("words", [])
    result = cap_pauses(
        input_path=input,
        output_path=output,
        max_gap_s=max_gap,
        crossfade_s=crossfade,
        words=preloaded,
    )
    print(f"cap-pauses: {result['gaps_trimmed']} gaps capped to {result['max_gap_s']:.2f}s")
    print(
        f"  duration: {result['original_duration_s']:.2f}s → {result['new_duration_s']:.2f}s "
        f"({result['seconds_removed']:.2f}s removed)"
    )
    print(f"  output  → {result['output']}")
    return 0


@audio_app.command(name="pad-onsets")
def audio_pad_onsets(
    input: str = typer.Option(..., "--input", "-i", help="Audio (or m4a/mp3) file to process."),
    output: str = typer.Option(..., "--output", "-o", help="Output wav path."),
    words: str = typer.Option(..., "--words", help="Path to words JSON (word-level timestamps)."),
    pad: float = typer.Option(0.05, "--pad", help="Minimum lead-in silence before each word onset, in seconds (default: 0.05)."),
) -> int:
    import json as _json
    from pathlib import Path
    from ..audio import pad_onsets
    data = _json.loads(Path(words).read_text())
    words_list = data if isinstance(data, list) else data.get("words", [])
    result = pad_onsets(
        input_path=input,
        output_path=output,
        words=words_list,
        pad_s=pad,
    )
    print(f"pad-onsets: {result['onsets_padded']} onsets padded (+{result['seconds_added']:.3f}s)")
    print(
        f"  duration: {result['original_duration_s']:.2f}s → {result['new_duration_s']:.2f}s"
    )
    print(f"  output  → {result['output']}")
    return 0


@audio_app.command("voiceover")
def audio_voiceover(
    text: Optional[str] = typer.Option(None, "--text", help="Text to synthesize. Reads from stdin if omitted."),
    out: str = typer.Option(..., "--out", help="Output audio file path (e.g. /tmp/out.mp3)."),
    voice: str = typer.Option("nova", "--voice", help="Voice name or ElevenLabs voice ID (default: nova)."),
    voice_model: str = typer.Option("tts-mini", "--voice-model", help="TTS backend: tts-mini, tts-gemini, elevenlabs (default: tts-mini)."),
    speed: Optional[float] = typer.Option(None, "--speed", help="atempo multiplier applied after synthesis (e.g. 1.2 = 20% faster)."),
    style: Optional[str] = typer.Option(None, "--style", help="Delivery style preset: rapid_fire, fast, calm, natural."),
) -> int:
    import shutil
    import tempfile
    from pathlib import Path as _P

    from ..voiceover import generate_voiceover_dict

    text_val = text or sys.stdin.read().strip()
    if not text_val:
        print("ERROR: provide --text or pipe text via stdin.", file=sys.stderr)
        return 1

    out_path = _P(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        result = generate_voiceover_dict(
            text=text_val,
            voice=voice,
            out_dir=tmp,
            style=style,
            voice_model=voice_model,
        )
        audio_src = _P(result["audio_path"])
        if speed and speed != 1.0:
            from ..audio import speedup
            speedup(audio_src, out_path, speed)
        else:
            shutil.copy2(audio_src, out_path)

    duration = result["total_duration_s"]
    print(f"voiceover: {duration:.2f}s → {out_path}")
    if speed and speed != 1.0:
        print(f"  speed: {speed:.2f}x applied")
    return 0


@audio_app.command("speed")
def audio_speed(
    in_path: str = typer.Option(..., "--in", help="Input audio file."),
    out_path: str = typer.Option(..., "--out", help="Output audio path."),
    rate: Optional[float] = typer.Option(None, "--rate", help="atempo multiplier (e.g. 1.3 = 30% faster)."),
    by: Optional[str] = typer.Option(None, "--by", help="Percent change with trailing % — e.g. '30%' (=1.3) or '-20%' (=0.8)."),
) -> int:
    if rate is None and by is None:
        typer.echo("Error: one of --rate or --by is required", err=True)
        return 2
    if rate is not None and by is not None:
        typer.echo("Error: --rate and --by are mutually exclusive", err=True)
        return 2
    from pathlib import Path as _P
    from ..audio import speedup, parse_by_pct
    effective_rate = rate if rate is not None else parse_by_pct(by)  # type: ignore[arg-type]
    out = speedup(_P(in_path), _P(out_path), effective_rate)
    print(f"audio speed: rate={effective_rate:.4f} → {out}")
    return 0
