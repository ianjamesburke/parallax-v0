from __future__ import annotations

import json
import sys
from typing import Optional

import typer


def register_cut(app: typer.Typer) -> None:
    app.command("cut")(cut_cmd)


def _parse_words_per_chunk(val: str) -> int | str:
    cleaned = str(val).strip()
    if cleaned.lower() == "smart":
        return "smart"
    try:
        n = int(cleaned)
        if n < 1:
            raise ValueError
        return n
    except ValueError as exc:
        raise ValueError(f"--words-per-chunk must be a positive integer or 'smart', got {val!r}") from exc


def _print_plan_human(res: dict) -> None:
    est_tag = " (estimated from word count / speed)" if res.get("timing_estimated") else ""
    print(f"parallax cut --plan-only: {res.get('status')}  [mapping: {res.get('mapping_source')}]")
    print(
        f"  voiceover: {res.get('voiceover_source')}  "
        f"duration={res.get('voiceover_duration_s', 0.0):.2f}s{est_tag}  "
        f"estimated_tts_cost=${res.get('estimated_tts_cost_usd', 0.0):.4f}"
    )
    print(
        f"  segments:  {res.get('segments_count', 0)}  "
        f"reuse_count={res.get('reuse_count', 0)}  "
        f"fps={res.get('fps', 24)}  resolution={res.get('resolution')}"
    )
    for sc in res.get("scenes") or []:
        print(
            f"    [{sc['index']:02d}] {sc['start_s']:6.2f}s..{sc['end_s']:6.2f}s "
            f"({sc['duration_s']:5.2f}s, trim={sc['clip_trim_start_s']:.2f}s)  "
            f"{sc['clip']}  <- {sc['anchor']!r}"
        )
    if res.get("unused_clips"):
        print(f"  unused_clips: {', '.join(res['unused_clips'])}")
    if res.get("reused_clips"):
        reused_desc = ", ".join(
            f"{r['clip']} (seg {r['segment']}, trim {r['trim_start_s']:.2f}s..{r['trim_end_s']:.2f}s)"
            for r in res["reused_clips"]
        )
        print(f"  reused_clips: {reused_desc}")
    if res.get("fuzzy_anchors"):
        fa_desc = ", ".join(f"seg {f['segment']} ({f['anchor']!r} -> {f['matched_script']!r})" for f in res["fuzzy_anchors"])
        print(f"  fuzzy_anchors: {fa_desc}")
    if res.get("unmatched_script_tokens"):
        toks = ", ".join(u["token"] for u in res["unmatched_script_tokens"][:12])
        more = f" (+{len(res['unmatched_script_tokens']) - 12} more)" if len(res["unmatched_script_tokens"]) > 12 else ""
        print(f"  interpolated_tokens ({len(res['unmatched_script_tokens'])}): {toks}{more}")
    for w in res.get("warnings") or []:
        print(f"  warning: {w}")
    print(f"  plan_json: {res.get('plan_json_path')}")
    print(f"  manifest:  {res.get('manifest_path')}")


def _print_result_human(res: dict) -> None:
    print(
        f"parallax cut: {res.get('video_path')} "
        f"({res.get('video_duration_s', 0.0):.2f}s, diff vs voiceover={res.get('duration_diff_s', 0.0):.3f}s)"
    )
    print(
        f"  voiceover: {res.get('voiceover_source')} ({res.get('voiceover_duration_s', 0.0):.2f}s)  "
        f"tts_spend=${res.get('tts_cost_usd', 0.0):.4f}"
    )
    print(
        f"  segments:  {res.get('segments_count', 0)}  "
        f"reuse_count={res.get('reuse_count', 0)}"
    )
    if res.get("unused_clips"):
        print(f"  unused_clips: {', '.join(res['unused_clips'])}")
    if res.get("reused_clips"):
        reused_desc = ", ".join(
            f"{r['clip']} (seg {r['segment']}, trim {r['trim_start_s']:.2f}s..{r['trim_end_s']:.2f}s)"
            for r in res["reused_clips"]
        )
        print(f"  reused_clips: {reused_desc}")
    if res.get("fuzzy_anchors"):
        fa_desc = ", ".join(f"seg {f['segment']} ({f['anchor']!r} -> {f['matched_script']!r})" for f in res["fuzzy_anchors"])
        print(f"  fuzzy_anchors: {fa_desc}")
    if res.get("unmatched_script_tokens"):
        toks = ", ".join(u["token"] for u in res["unmatched_script_tokens"][:12])
        print(f"  interpolated_tokens ({len(res['unmatched_script_tokens'])}): {toks}")
    for w in res.get("warnings") or []:
        print(f"  warning:   {w}")
    if res.get("caption_report_path"):
        print(f"  captions:  safe_area={res.get('caption_safe_area')} ({res.get('caption_report_path')})")
    if res.get("loudness"):
        ld = res["loudness"]
        print(
            f"  loudness:  drop={ld.get('first_to_last_drop_db', 0.0):.2f}dB "
            f"(decay_detected={ld.get('decay_detected', False)}, report={res.get('loudness_report_path')})"
        )
    if res.get("verification"):
        vf = res["verification"]
        note_str = f" ({vf['note']})" if vf.get("note") else ""
        print(
            f"  verify:    status={vf.get('status')} retries={vf.get('retries_used', 0)}"
            f"{note_str} ({res.get('verify_report_path')})"
        )
    print(f"  manifest:  {res.get('manifest_path')}")
    if res.get("editor_export_path"):
        print(f"  export:    {res.get('editor_export_path')} (timeline: {res.get('timeline_xml_path')})")
        print(f"  consistency: {res.get('consistency')}")


def cut_cmd(
    script: Optional[str] = typer.Option(
        None,
        "--script",
        help="Path to the spoken script text file (required unless --from-manifest is given).",
    ),
    clips: Optional[str] = typer.Option(
        None,
        "--clips",
        help="Directory of source video clips (required unless --from-manifest is given).",
    ),
    map_file: Optional[str] = typer.Option(
        None,
        "--map",
        help=(
            "YAML/JSON clip-to-script mapping (`version: 1`, `segments: [{anchor, clip, in?, note?}]`). "
            "When omitted, infers from inline `[Scene_01]` markers or numbered clip names if unambiguous; "
            "otherwise exits 2 with a MAPPING NEEDED report and writes `<project>/cut-map.template.yaml`."
        ),
    ),
    out: str = typer.Option(
        ...,
        "--out",
        "--output",
        help="Output MP4 file path (e.g. out.mp4).",
    ),
    project: Optional[str] = typer.Option(
        None,
        "--project",
        help="Working directory holding manifest.yaml, words.json, voiceover, captions, and assets/ export (default: <out stem>.cut/ next to --out).",
    ),
    audio: Optional[str] = typer.Option(
        None,
        "--audio",
        help="Use existing voiceover audio file instead of calling TTS.",
    ),
    words: Optional[str] = typer.Option(
        None,
        "--words",
        help="Use existing word-timing JSON (`{\"words\": [{\"word\", \"start\", \"end\"}]}`) with --audio.",
    ),
    process_audio: bool = typer.Option(
        False,
        "--process-audio/--no-process-audio",
        help="Apply sentence-silence cap + speed to supplied --audio (default: off for supplied audio; always on for TTS output).",
    ),
    speed: float = typer.Option(
        1.15,
        "--speed",
        help="Voiceover speed multiplier (default: 1.15; use --speed 1.0 to disable).",
    ),
    voice: str = typer.Option(
        "nova",
        "--voice",
        help="TTS voice name or ElevenLabs voice ID (default: nova).",
    ),
    voice_model: str = typer.Option(
        "tts-mini",
        "--voice-model",
        help="TTS backend alias: tts-mini, tts-gemini, elevenlabs, seed-audio (default: tts-mini).",
    ),
    style: Optional[str] = typer.Option(
        None,
        "--style",
        help="Delivery style preset for TTS: rapid_fire, fast, calm, natural.",
    ),
    max_sentence_silence: float = typer.Option(
        0.25,
        "--max-sentence-silence",
        help="Cap inter-word/sentence silences to this duration in seconds (default: 0.25).",
    ),
    vo_chunk_seconds: float = typer.Option(
        60.0,
        "--vo-chunk-seconds",
        help="Soft cap in seconds per TTS voiceover chunk at paragraph/sentence boundaries (default: 60; 0 = single take).",
    ),
    vo_gap: float = typer.Option(
        0.25,
        "--vo-gap",
        help="Natural silence gap in seconds inserted between concatenated voiceover chunks (default: 0.25).",
    ),
    vo_lufs: float = typer.Option(
        -16.0,
        "--vo-lufs",
        help="Target integrated loudness in LUFS for per-chunk voiceover normalisation (default: -16.0).",
    ),
    vo_normalize: bool = typer.Option(
        True,
        "--vo-normalize/--no-vo-normalize",
        help="Apply two-pass ffmpeg loudnorm to each voiceover chunk (default: on).",
    ),
    vo_verify: bool = typer.Option(
        True,
        "--vo-verify/--no-vo-verify",
        help="Verify synthesized/processed voiceover against script via whisper transcription when available (default: on).",
    ),
    vo_retries: int = typer.Option(
        2,
        "--vo-retries",
        help="Max per-chunk TTS regeneration retries when verification detects missing words or low similarity (default: 2).",
    ),
    strict_vo: bool = typer.Option(
        False,
        "--strict-vo/--no-strict-vo",
        help="Fail with exit code 1 if voiceover verification still has residual mismatches after retries (default: off).",
    ),
    fps: int = typer.Option(
        24,
        "--fps",
        help="Output video and editor timeline frame rate (default: 24).",
    ),
    aspect: Optional[str] = typer.Option(
        None,
        "--aspect",
        help="Output aspect ratio preset (9:16, 16:9, 1:1, 4:3, 3:4). Default: common clip resolution or 9:16 (720x1280).",
    ),
    resolution: Optional[str] = typer.Option(
        None,
        "--resolution",
        help="Explicit output resolution WxH (e.g. 720x1280 or 1080x1920).",
    ),
    captions: bool = typer.Option(
        True,
        "--captions/--no-captions",
        help="Burn script-text captions onto the rendered video (default: on).",
    ),
    caption_style: str = typer.Option(
        "bangers",
        "--caption-style",
        help="Caption preset name: bangers, impact, bebas, anton, clean (default: bangers).",
    ),
    caption_y: float = typer.Option(
        0.65,
        "--caption-y",
        help="Vertical center position of captions 0..1 (default: 0.65).",
    ),
    caption_size: Optional[int] = typer.Option(
        None,
        "--caption-size",
        help="Caption font size in pixels (default: auto-scaled from 55 at 720px width).",
    ),
    words_per_chunk: str = typer.Option(
        "3",
        "--words-per-chunk",
        help="Max words per caption chunk (default: 3; 'smart' also accepted).",
    ),
    max_clip_s: Optional[float] = typer.Option(
        None,
        "--max-clip-s",
        help="Override per-clip usable length in seconds (default: probed clip duration).",
    ),
    allow_hold: bool = typer.Option(
        False,
        "--allow-hold",
        help="Freeze last frame when a scene slot overruns its clip instead of exiting 2.",
    ),
    export_editor: bool = typer.Option(
        True,
        "--export-editor/--no-export-editor",
        help="Export Premiere/Resolve/FCP package (`timeline.xml`, `timeline.fcpxml`, `captions.srt`) from the same manifest into `<project>/assets/` (default: on).",
    ),
    from_manifest: Optional[str] = typer.Option(
        None,
        "--from-manifest",
        help="Skip planning: re-render video and re-export editor package from an existing or hand-edited manifest.yaml.",
    ),
    encode_preset: str = typer.Option(
        "drive-safe",
        "--encode-preset",
        help="Output encoding preset: drive-safe (default; H.264 High@L4.0, yuv420p, 24fps CFR, AAC-LC 48kHz stereo 160k, +faststart, even dimensions) or source.",
    ),
    plan_only: bool = typer.Option(
        False,
        "--plan-only",
        "--dry-run",
        help="Print plan + mapping report, write plan.json and manifest preview into --project, call NO paid service and render nothing (exit 0 if renderable, 2 if mapping needed or overrun).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable plan, report, or result as JSON to stdout.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Ignore voiceover caches and re-synthesize.",
    ),
) -> int:
    """Cut a folder of clips to a script: voiceover (cached), word alignment, midpoint-of-gap scene plan, manifest.yaml, captioned MP4, and XML editor export from ONE manifest."""
    from ..cut import CutError, run_cut

    try:
        wpc = _parse_words_per_chunk(words_per_chunk)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(code=2)

    try:
        res = run_cut(
            out_path=out,
            script_path=script,
            clips_dir=clips,
            map_path=map_file,
            project_dir=project,
            audio_path=audio,
            words_path=words,
            process_audio=process_audio,
            speed=speed,
            voice=voice,
            voice_model=voice_model,
            style=style,
            max_sentence_silence=max_sentence_silence,
            vo_chunk_seconds=vo_chunk_seconds,
            vo_gap=vo_gap,
            vo_lufs=vo_lufs,
            vo_normalize=vo_normalize,
            vo_verify=vo_verify,
            vo_retries=vo_retries,
            strict_vo=strict_vo,
            fps=fps,
            aspect=aspect,
            resolution=resolution,
            captions=captions,
            caption_style=caption_style,
            caption_y=caption_y,
            caption_size=caption_size,
            words_per_chunk=wpc,
            max_clip_s=max_clip_s,
            allow_hold=allow_hold,
            export_editor=export_editor,
            from_manifest=from_manifest,
            encode_preset=encode_preset,
            plan_only=plan_only,
            force=force,
            quiet=as_json,
        )
    except CutError as exc:
        if as_json:
            payload = exc.report if exc.report else {"status": "error", "message": str(exc), "exit_code": exc.exit_code}
            print(json.dumps(payload, indent=2))
        else:
            out_stream = sys.stdout if exc.report.get("status") == "mapping_needed" else sys.stderr
            print(str(exc), file=out_stream)
        raise typer.Exit(code=exc.exit_code)
    except Exception as exc:
        if as_json:
            print(json.dumps({"status": "error", "message": str(exc), "exit_code": 1}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1)

    if as_json:
        print(json.dumps(res, indent=2))
        return 0

    if plan_only:
        _print_plan_human(res)
    else:
        _print_result_human(res)
    return 0
