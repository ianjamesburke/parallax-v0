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
        result = transcribe_words(input, out, words=preloaded)
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


@audio_app.command(name="cap-sentence-silence")
def audio_cap_sentence_silence(
    input: str = typer.Option(..., "--input", "-i", help="Source audio; preserved unchanged."),
    output: str = typer.Option(..., "--output", "-o", help="Edited WAV output path."),
    words: str = typer.Option(..., "--words", help="Canonical word-timing JSON with sentence punctuation."),
    words_out: str = typer.Option(..., "--words-out", help="Output JSON with adjusted canonical word timings."),
    report: Optional[str] = typer.Option(None, "--report", help="Detailed acoustic cuts and provenance JSON."),
    max_silence: float = typer.Option(0.075, "--max-silence", help="Maximum continuous acoustic quiet at sentence boundaries, seconds (default: 0.075)."),
    crossfade: float = typer.Option(0.010, "--crossfade", help="Quiet-only join crossfade in seconds (default: 0.010)."),
    noise_db: float = typer.Option(-40.0, "--noise-db", help="Acoustic quiet threshold in dB (default: -40)."),
    min_silence: float = typer.Option(0.040, "--min-silence", help="Minimum detected quiet segment, seconds (default: 0.040)."),
    first_word_by: Optional[float] = typer.Option(None, "--first-word-by", help="Optionally trim verified opening quiet so the first aligned word starts by this second (e.g. 0.100)."),
) -> int:
    """Shorten measured quiet between sentences while preserving audible edges.

    Unlike cap-pauses, this uses punctuation only to select boundaries and
    measured acoustic quiet to place cuts. It always writes revised timestamps
    for downstream captions, scene timing, and graphics.
    """
    import json
    import tempfile
    from contextlib import nullcontext
    from pathlib import Path
    from ..audio import cap_sentence_silence, trim_leading_quiet

    paths = [Path(p).expanduser().resolve() for p in (input, output, words, words_out)]
    if report:
        paths.append(Path(report).expanduser().resolve())
    if len(set(paths)) != len(paths):
        raise typer.BadParameter("input, output, words, words-out, and report must be distinct paths")

    word_data = json.loads(Path(words).read_text())
    word_list = word_data if isinstance(word_data, list) else word_data.get("words", [])
    with (tempfile.TemporaryDirectory() if first_word_by is not None else nullcontext(None)) as tmpdir:
        sentence_output = str(Path(tmpdir) / "sentence-capped.wav") if tmpdir else output
        result = cap_sentence_silence(
            input_path=input,
            output_path=sentence_output,
            words=word_list,
            max_silence_s=max_silence,
            crossfade_s=crossfade,
            noise_db=noise_db,
            min_silence_s=min_silence,
        )
        if first_word_by is not None:
            opening = trim_leading_quiet(
                input_path=sentence_output, output_path=output,
                words=result["adjusted_words"], first_word_by_s=first_word_by,
                noise_db=noise_db,
            )
            result["sentence_stage_duration_s"] = result["new_duration_s"]
            result["output"] = str(Path(output).resolve())
            result["new_duration_s"] = opening["new_duration_s"]
            result["seconds_removed"] = round(
                result["original_duration_s"] - opening["new_duration_s"], 4
            )
            result["adjusted_words"] = opening["adjusted_words"]
            for edit in result["edits"]:
                edit["sentence_stage_verified_quiet_start_s"] = edit["verified_quiet_start_s"]
                edit["verified_quiet_start_s"] = round(
                    edit["verified_quiet_start_s"] - opening["seconds_removed"], 4
                )
            result["source_cut_coordinate_system"] = "original input audio"
            result["verified_quiet_coordinate_system"] = "final output audio"
            result["opening_trim"] = {
                key: value for key, value in opening.items()
                if key not in ("input", "output", "adjusted_words")
            }
    adjusted = result.pop("adjusted_words")
    words_path = Path(words_out)
    words_path.parent.mkdir(parents=True, exist_ok=True)
    words_path.write_text(json.dumps({"words": adjusted, "total_duration_s": result["new_duration_s"]}, indent=2))
    result["words_input"] = str(Path(words).resolve())
    result["words_output"] = str(words_path.resolve())
    if report:
        report_path = Path(report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, indent=2))
    print(
        f"cap-sentence-silence: {result['quiet_segments_capped']} quiet segments at "
        f"{result['sentence_boundaries']} sentence boundaries; "
        f"{result['seconds_removed']:.3f}s removed"
    )
    print(f"  audio  → {output}")
    print(f"  words  → {words_out}")
    if report:
        print(f"  report → {report}")
    return 0


@audio_app.command(name="trim-leading-quiet")
def audio_trim_leading_quiet(
    input: str = typer.Option(..., "--input", "-i", help="Source audio; preserved unchanged."),
    output: str = typer.Option(..., "--output", "-o", help="Edited WAV output path."),
    words: str = typer.Option(..., "--words", help="Canonical word-timing JSON."),
    words_out: str = typer.Option(..., "--words-out", help="Output JSON with shifted canonical word timings."),
    report: Optional[str] = typer.Option(None, "--report", help="Detailed measured opening trim JSON."),
    first_word_by: float = typer.Option(0.100, "--first-word-by", help="Latest allowed first-word start, seconds (default: 0.100)."),
    min_leading_quiet: float = typer.Option(0.020, "--min-leading-quiet", help="Protected acoustic quiet before detectable speech, seconds (default: 0.020)."),
    fade: float = typer.Option(0.005, "--fade", help="Fade-in wholly inside retained quiet, seconds (default: 0.005)."),
    noise_db: float = typer.Option(-40.0, "--noise-db", help="Acoustic quiet threshold in dB (default: -40)."),
) -> int:
    """Trim proven opening quiet without touching the first spoken phoneme."""
    import json
    from pathlib import Path
    from ..audio import trim_leading_quiet

    paths = [Path(p).expanduser().resolve() for p in (input, output, words, words_out)]
    if report:
        paths.append(Path(report).expanduser().resolve())
    if len(set(paths)) != len(paths):
        raise typer.BadParameter("input, output, words, words-out, and report must be distinct paths")
    word_data = json.loads(Path(words).read_text())
    word_list = word_data if isinstance(word_data, list) else word_data.get("words", [])
    result = trim_leading_quiet(
        input_path=input, output_path=output, words=word_list,
        first_word_by_s=first_word_by, min_leading_quiet_s=min_leading_quiet,
        fade_s=fade, noise_db=noise_db,
    )
    adjusted = result.pop("adjusted_words")
    words_path = Path(words_out)
    words_path.parent.mkdir(parents=True, exist_ok=True)
    words_path.write_text(json.dumps({"words": adjusted, "total_duration_s": result["new_duration_s"]}, indent=2))
    result["words_input"] = str(Path(words).resolve())
    result["words_output"] = str(words_path.resolve())
    if report:
        report_path = Path(report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, indent=2))
    print(
        f"trim-leading-quiet: {result['seconds_removed']:.4f}s removed; "
        f"first word {result['first_word_before_s']:.4f}s → {result['first_word_after_s']:.4f}s"
    )
    print(f"  audio  → {output}")
    print(f"  words  → {words_out}")
    if report:
        print(f"  report → {report}")
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
    trim_pauses: bool = typer.Option(False, "--trim-pauses", help="Opt in to timestamp-based pause trimming. By default, preserve the provider's original audio."),
) -> int:
    """Synthesize speech. Spends money; prints the estimated cost first."""
    import shutil
    import tempfile
    from pathlib import Path as _P

    from ..models import TTS_MODELS
    from ..preflight import print_estimate
    from ..shim import is_test_mode
    from ..voiceover import generate_voiceover_dict

    text_val = text or sys.stdin.read().strip()
    if not text_val:
        print("ERROR: provide --text or pipe text via stdin.", file=sys.stderr)
        return 1

    spec = TTS_MODELS.get(voice_model)
    if not is_test_mode():
        print_estimate(f"audio voiceover --voice-model {voice_model}", spec.estimate_tts_usd(len(text_val)) if spec else None)
    out_path = _P(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    speed_applied = speed is not None and speed != 1.0
    raw_saved = None

    with tempfile.TemporaryDirectory() as tmp:
        result = generate_voiceover_dict(
            text=text_val,
            voice=voice,
            out_dir=tmp,
            style=style,
            voice_model=voice_model,
            trim_pauses=trim_pauses,
        )
        audio_src = _P(result["audio_path"])
        if trim_pauses or speed_applied:
            raw_src = _P(result["raw_audio_path"])
            raw_saved = out_path.with_name(out_path.stem + ".provider-raw" + raw_src.suffix)
            shutil.copy2(raw_src, raw_saved)
        if speed_applied:
            from ..audio import speedup
            assert speed is not None
            speedup(audio_src, out_path, speed)
        else:
            shutil.copy2(audio_src, out_path)

    duration = result["total_duration_s"]
    if speed_applied:
        from ..ffmpeg_utils import run_ffmpeg
        probe = run_ffmpeg(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(out_path)],
            capture_output=True, text=True,
        )
        if probe.stdout.strip():
            duration = float(probe.stdout.strip())
    print(f"voiceover: {duration:.2f}s → {out_path}")
    if trim_pauses:
        print("  pause trimming: enabled")
    elif speed_applied:
        print("  pause trimming: disabled; output speed-adjusted")
    else:
        print("  pause trimming: disabled (output preserves provider audio)")
    if raw_saved is not None:
        print(f"  provider raw: {raw_saved}")
    if speed_applied:
        assert speed is not None
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
