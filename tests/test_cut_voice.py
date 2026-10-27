"""Synthetic unit and ffmpeg tests for `parallax cut` voiceover processing."""

from __future__ import annotations

import math
import struct
import subprocess
import wave
from pathlib import Path

from parallax.cut import (
    PAUSE_TRIM_ESCALATION_LEVELS,
    align_script_words,
    chunk_script,
    compute_energy_pause_cuts,
    compute_naive_alignment_cuts,
    detect_energy_silences,
    flag_long_aligned_tokens,
    process_voiceover_audio,
)
from parallax.voiceover import expand_numbers_and_symbols, normalize_text_for_verify


def _make_burst_silence_burst_wav(
    path: Path,
    *,
    b1_dur: float = 0.60,
    sil_dur: float = 1.00,
    b2_dur: float = 0.70,
    sr: int = 16000,
) -> Path:
    """Synthesize `[sine burst 1][silence][sine burst 2]` via ffmpeg."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fc = (
        f"sine=frequency=440:sample_rate={sr}:duration={b1_dur},volume=0.8[b1];"
        f"anullsrc=r={sr}:cl=mono:d={sil_dur}[sil];"
        f"sine=frequency=660:sample_rate={sr}:duration={b2_dur},volume=0.8[b2];"
        f"[b1][sil][b2]concat=n=3:v=0:a=1[out]"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-filter_complex",
            fc,
            "-map",
            "[out]",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
    )
    return path


def _wav_rms(wav_path: Path, start_s: float, end_s: float) -> float:
    """Compute normalized RMS (0..1) of a 16-bit mono WAV slice `[start_s, end_s]`."""
    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        n_frames = wf.getnframes()
        s_idx = max(0, min(n_frames, int(round(start_s * sr))))
        e_idx = max(s_idx, min(n_frames, int(round(end_s * sr))))
        if e_idx <= s_idx:
            return 0.0
        wf.setpos(s_idx)
        raw = wf.readframes(e_idx - s_idx)
    samples = struct.unpack(f"<{len(raw) // 2}h", raw)
    if not samples:
        return 0.0
    mean_sq = sum((s / 32768.0) ** 2 for s in samples) / len(samples)
    return math.sqrt(mean_sq)


def test_expand_numbers_and_align_script_words_keeps_script_numerals() -> None:
    assert expand_numbers_and_symbols("up to 97% of sea salt") == "up to ninety seven percent of sea salt"
    assert expand_numbers_and_symbols("It costs $5 or $1,000 in 45 AD.") == (
        "It costs five dollars or one thousand dollars in forty five AD."
    )
    assert normalize_text_for_verify("97% — ninety-seven per cent!") == [
        "ninety",
        "seven",
        "percent",
        "ninety",
        "seven",
        "percent",
    ]

    script = "Up to 97% of human sea salt costs $5 in 45 AD."
    asr_words = [
        {"word": "up", "start": 0.0, "end": 0.15},
        {"word": "to", "start": 0.15, "end": 0.30},
        {"word": "ninety", "start": 0.40, "end": 0.65},
        {"word": "seven", "start": 0.65, "end": 0.90},
        {"word": "percent", "start": 0.90, "end": 1.20},
        {"word": "of", "start": 1.25, "end": 1.35},
        {"word": "human", "start": 1.35, "end": 1.65},
        {"word": "sea", "start": 1.65, "end": 1.95},
        {"word": "salt", "start": 1.95, "end": 2.30},
        {"word": "costs", "start": 2.35, "end": 2.65},
        {"word": "five", "start": 2.70, "end": 2.95},
        {"word": "dollars", "start": 2.95, "end": 3.30},
        {"word": "in", "start": 3.35, "end": 3.45},
        {"word": "forty", "start": 3.50, "end": 3.75},
        {"word": "five", "start": 3.75, "end": 4.00},
        {"word": "ad", "start": 4.00, "end": 4.30},
    ]
    aligned = align_script_words(script, asr_words, audio_duration=4.40)
    assert aligned["unmatched_tokens"] == []
    by_word = {w["word"]: w for w in aligned["words"]}
    # Script/caption token stays "97%" and spans "ninety".."percent" (0.40..1.20)
    assert "97%" in by_word
    assert by_word["97%"]["start"] == 0.40
    assert by_word["97%"]["end"] == 1.20
    assert by_word["$5"]["start"] == 2.70
    assert by_word["$5"]["end"] == 3.30


def test_compute_energy_pause_cuts_midpoint_padding_escalation_and_long_token() -> None:
    # Gap without energy silence must NEVER be cut
    words_short = [
        {"word": "hello", "start": 0.0, "end": 0.40},
        {"word": "world", "start": 1.40, "end": 1.80},
    ]
    res_no_sil = compute_energy_pause_cuts(words_short, [], max_sentence_silence=0.25, min_padding_s=0.15)
    assert res_no_sil["cuts"] == []

    # Real silence [0.50, 1.30] inside alignment gap [0.40, 1.40]:
    # midpoint of real silence is 0.90; with padding 0.15 each side -> cut is [0.65, 1.15]
    silences = [{"start": 0.50, "end": 1.30, "duration": 0.80}]
    res_015 = compute_energy_pause_cuts(words_short, silences, max_sentence_silence=0.20, min_padding_s=0.15)
    assert res_015["cuts"] == [(0.65, 1.15)]
    c0, c1 = res_015["cuts"][0]
    assert round((c0 + c1) / 2.0, 4) == 0.90  # exact middle of energy silence [0.50, 1.30]
    assert round(c0 - 0.50, 4) >= 0.15
    assert round(1.30 - c1, 4) >= 0.15

    # Escalation levels: 0.30 -> [0.80, 1.00]; 0.50 -> no cut (needs >1.0s silence); None/"off" -> no cut
    res_030 = compute_energy_pause_cuts(words_short, silences, max_sentence_silence=0.20, min_padding_s=0.30)
    assert res_030["cuts"] == [(0.80, 1.00)]
    res_050 = compute_energy_pause_cuts(words_short, silences, max_sentence_silence=0.20, min_padding_s=0.50)
    assert res_050["cuts"] == []
    res_off = compute_energy_pause_cuts(words_short, silences, max_sentence_silence=0.20, min_padding_s=None)
    assert res_off["cuts"] == []
    assert PAUSE_TRIM_ESCALATION_LEVELS == (0.15, 0.30, 0.50, None)

    # Long aligned token (>0.6s) widens margin x2 on adjacent gap (0.15 * 2 = 0.30 -> [0.80, 1.00])
    words_long = [
        {"word": "hello", "start": 0.0, "end": 0.40},
        {"word": "97%", "start": 1.40, "end": 2.15},  # 0.75s (>0.6s)
    ]
    flagged = flag_long_aligned_tokens(words_long)
    assert len(flagged) == 1 and flagged[0]["word"] == "97%"
    res_long = compute_energy_pause_cuts(words_long, silences, max_sentence_silence=0.20, min_padding_s=0.15)
    assert res_long["cuts"] == [(0.80, 1.00)]


def test_energy_pause_trim_preserves_late_aligned_numeral_burst_onset(tmp_path: Path) -> None:
    """Synthetic test: sine bursts + silence + fake words.json with '97%' aligned 0.2s late.

     Burst 1: 0.00..0.60s
     Silence: 0.60..1.60s (1.00s real silence)
     Burst 2: 1.60..2.30s (real onset at 1.60s; aligned start in words.json is 1.80s, 0.20s late!)
    """
    raw_wav = _make_burst_silence_burst_wav(
        tmp_path / "raw_bursts.wav",
        b1_dur=0.60,
        sil_dur=1.00,
        b2_dur=0.70,
    )
    sils = detect_energy_silences(raw_wav)
    assert len(sils) == 1
    assert abs(sils[0]["start"] - 0.60) < 0.05
    assert abs(sils[0]["end"] - 1.60) < 0.05

    # Fake words.json: "97%" aligned 0.20s late at 1.80s (real burst 2 starts at 1.60s)
    raw_words = [
        {"word": "up", "start": 0.00, "end": 0.60},
        {"word": "97%", "start": 1.80, "end": 2.25},  # 0.45s span, aligned 0.20s late
    ]

    out_wav = tmp_path / "trimmed_energy.wav"
    proc = process_voiceover_audio(
        raw_audio_path=raw_wav,
        raw_words=raw_words,
        out_audio_path=out_wav,
        speed=1.0,
        max_sentence_silence=0.10,
        min_padding_s=0.15,
    )
    assert proc["cuts_count"] == 1
    c0, c1 = proc["cuts"][0]
    # Cut must end >= 0.15s BEFORE real burst onset at 1.60s (i.e. c1 <= 1.45s)
    assert c1 <= 1.46
    removed_s = c1 - c0
    assert removed_s > 0.50

    # Compare RMS in the first 150ms of Burst 2 in raw vs energy-trimmed output
    raw_onset_rms = _wav_rms(raw_wav, 1.60, 1.75)
    trimmed_burst2_start = 1.60 - removed_s
    trimmed_onset_rms = _wav_rms(out_wav, trimmed_burst2_start, trimmed_burst2_start + 0.15)
    assert raw_onset_rms > 0.05
    assert trimmed_onset_rms >= raw_onset_rms * 0.95

    # Verify a naive alignment cut (g0+half .. g1-half = 0.65 .. 1.75) WOULD cut through [1.60, 1.75]!
    naive_cuts = compute_naive_alignment_cuts(raw_words, max_sentence_silence=0.10)
    assert len(naive_cuts) == 1
    nc0, nc1 = naive_cuts[0]
    # Naive cut ends at 1.75s, which chops off the entire first 150ms [1.60, 1.75] of Burst 2!
    assert nc1 >= 1.74
    naive_clipped_onset_s = max(0.0, min(1.75, nc1) - max(1.60, nc0))
    assert naive_clipped_onset_s >= 0.14


def test_chunk_script_boundaries() -> None:
    p1 = " ".join(["First paragraph sentence one is here."] * 4)
    p2 = " ".join(["Second paragraph sentence two is here."] * 4)
    script = f"[Scene_01] {p1}\n\n[Scene_02] {p2}"
    assert len(chunk_script(script, max_chunk_seconds=0, speed=1.15)) == 1
    chunks = chunk_script(script, max_chunk_seconds=12.0, speed=1.15)
    assert len(chunks) == 2
    assert "[Scene" not in chunks[0] and "[Scene" not in chunks[1]


def test_chunked_voiceover_per_chunk_cache_and_word_offset(tmp_path: Path) -> None:
    """First run = N calls, rerun = 0, speed change = 0 (re-processed from per-chunk raw cache),
    one edited sentence = 1 call (only the edited chunk re-synthesized).
    """
    from parallax.cut import compute_chunk_cache_keys, resolve_or_generate_voiceover
    from parallax.voiceover import VoiceSynthesisResult

    calls: list[str] = []

    class _MockProvider:
        def synthesize(
            self,
            text: str,
            voice: str = "nova",
            style: str | None = None,
            *,
            out_dir: str | None = None,
        ) -> VoiceSynthesisResult:
            calls.append(text)
            dest = Path(out_dir or tmp_path)
            dest.mkdir(parents=True, exist_ok=True)
            mp3_path = dest / f"synth_{len(calls)}.mp3"
            mp3_path.write_bytes(b"FAKE_CHUNK_MP3_BYTES")
            toks = text.split()
            words = [
                {"word": w, "start": round(i * 0.4, 4), "end": round((i + 1) * 0.4, 4)}
                for i, w in enumerate(toks)
            ]
            dur = round(len(toks) * 0.4, 4)
            return VoiceSynthesisResult(
                audio_path=str(mp3_path),
                words=words,
                duration_s=dur,
                cost_usd=0.01,
            )

    para1 = " ".join(["Alpha sentence one is spoken clearly."] * 5)
    para2 = " ".join(["Beta sentence two follows right after."] * 5)
    para3 = " ".join(["Gamma sentence three concludes the story."] * 5)
    script = f"{para1}\n\n{para2}\n\n{para3}"

    expected_chunks = chunk_script(script, max_chunk_seconds=12.0, speed=1.15)
    assert len(expected_chunks) == 3

    proj = tmp_path / "chunk_proj"
    provider = _MockProvider()

    # 1. First run: N=3 provider calls, raw + processed chunk files created, word timings offset with gap
    r1 = resolve_or_generate_voiceover(
        clean_script=script,
        project_dir=proj,
        speed=1.15,
        max_sentence_silence=0.25,
        vo_chunk_seconds=12.0,
        vo_gap=0.25,
        provider=provider,
        quiet=True,
    )
    assert len(calls) == 3
    assert r1["tts_calls"] == 3
    assert r1["chunks_count"] == 3
    assert r1["source"] == "tts"

    # Verify per-chunk raw cache `<project>/cache/vo/chunks/<sha256>.raw.{mp3,json}` exists
    c0_raw_key, _ = compute_chunk_cache_keys(
        chunk_text=expected_chunks[0],
        voice="nova",
        voice_model="tts-mini",
        style=None,
        speed=1.15,
        max_sentence_silence=0.25,
    )
    chunks_dir = proj / "cache" / "vo" / "chunks"
    assert (chunks_dir / f"{c0_raw_key}.raw.mp3").is_file()
    assert (chunks_dir / f"{c0_raw_key}.raw.json").is_file()

    # Check word timings are monotonically offset across chunk boundaries
    w_starts = [w["start"] for w in r1["words"]]
    assert w_starts == sorted(w_starts)
    assert w_starts[-1] > w_starts[0] + 5.0

    # 2. Rerun with identical arguments: 0 provider calls
    calls.clear()
    r2 = resolve_or_generate_voiceover(
        clean_script=script,
        project_dir=proj,
        speed=1.15,
        max_sentence_silence=0.25,
        vo_chunk_seconds=12.0,
        vo_gap=0.25,
        provider=provider,
        quiet=True,
    )
    assert len(calls) == 0
    assert r2["tts_calls"] == 0
    assert r2["source"] == "cached"

    # 3. Speed change (1.15 -> 1.25): 0 provider calls (re-processed from per-chunk raw cache)
    calls.clear()
    r3 = resolve_or_generate_voiceover(
        clean_script=script,
        project_dir=proj,
        speed=1.25,
        max_sentence_silence=0.25,
        vo_chunk_seconds=12.0,
        vo_gap=0.25,
        provider=provider,
        quiet=True,
    )
    assert len(calls) == 0
    assert r3["tts_calls"] == 0
    assert r3["source"] == "cached"
    assert r3["cache_tier"] == "raw"

    # 4. Edit ONE sentence in paragraph 2 (at speed=1.15): only chunk 2 is synthesized (1 call!)
    calls.clear()
    para2_edited = para2.replace("Beta sentence two", "Delta revised sentence", 1)
    script_edited = f"{para1}\n\n{para2_edited}\n\n{para3}"
    r4 = resolve_or_generate_voiceover(
        clean_script=script_edited,
        project_dir=proj,
        speed=1.15,
        max_sentence_silence=0.25,
        vo_chunk_seconds=12.0,
        vo_gap=0.25,
        provider=provider,
        quiet=True,
    )
    assert len(calls) == 1
    assert r4["tts_calls"] == 1
    assert "Delta revised sentence" in calls[0]


def test_per_chunk_loudnorm_and_loudness_consistency_check(tmp_path: Path, capsys) -> None:
    """Per-chunk loudnorm brings chunks within ±1.5 LU of target/mean; when one chunk is
    deliberately off by >1.5 LU (e.g. -12 dBFS vs others -26 dBFS, with normalisation disabled),
    consistency check emits a warning naming that chunk, whereas consistent chunks do not.
    """
    import json
    from parallax.cli._cut import _print_result_human
    from parallax.cut import (
        analyze_voiceover_loudness,
        concatenate_chunk_voiceovers,
        measure_audio_loudness_lufs,
        normalize_audio_loudness,
        resolve_or_generate_voiceover,
    )
    from parallax.voiceover import VoiceSynthesisResult

    # 1. Create 3 sine chunks where chunk 1 is at -12 dBFS (0.25) and chunks 0 & 2 are at -26 dBFS (0.05)
    chunk_specs = [
        (0, "Opening paragraph one about ancient ports.", 0.05),
        (1, "Middle outlier paragraph two with loud sine.", 0.25),
        (2, "Closing paragraph three about calm waters.", 0.05),
    ]
    raw_chunks: list[dict] = []
    norm_chunks: list[dict] = []
    norm_lufs: list[float] = []

    for idx, preview_text, amp in chunk_specs:
        raw_chunk = tmp_path / f"chunk_{idx}_raw.wav"
        norm_chunk = tmp_path / f"chunk_{idx}_norm.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"aevalsrc={amp}*sin(2*PI*440*t):s=44100:d=3.0",
                str(raw_chunk),
            ],
            check=True,
        )
        raw_chunks.append(
            {
                "index": idx,
                "text": preview_text,
                "audio_path": str(raw_chunk),
                "total_duration_s": 3.0,
            }
        )
        res_norm = normalize_audio_loudness(raw_chunk, norm_chunk, target_lufs=-16.0)
        assert res_norm["normalized"] is True
        post = measure_audio_loudness_lufs(norm_chunk, target_lufs=-16.0)
        assert post is not None
        norm_lufs.append(post["input_i"])
        norm_chunks.append(
            {
                "index": idx,
                "text": preview_text,
                "audio_path": str(norm_chunk),
                "total_duration_s": 3.0,
                "integrated_lufs": post["input_i"],
            }
        )

    # All normalised chunks must be within 1.5 LU of each other and 1.5 LU of -16 LUFS
    assert max(norm_lufs) - min(norm_lufs) <= 1.5
    for val in norm_lufs:
        assert abs(val - (-16.0)) <= 1.5

    # 2. Consistent chunks (post-normalisation): no deviations, no warnings
    concat_norm = tmp_path / "concat_norm.wav"
    concatenate_chunk_voiceovers(norm_chunks, concat_norm, vo_gap=0.25)
    norm_json = tmp_path / "consistent.loudness.json"
    rep_ok = analyze_voiceover_loudness(
        concat_norm,
        chunks=norm_chunks,
        target_lufs=-16.0,
        tolerance_lu=1.5,
        vo_gap=0.25,
        out_json_path=norm_json,
    )
    assert norm_json.is_file()
    saved_ok = json.loads(norm_json.read_text(encoding="utf-8"))
    for key in (
        "target_lufs",
        "chunks",
        "final_lufs",
        "mean_chunk_lufs",
        "tolerance_lu",
        "deviations",
        "warnings",
    ):
        assert key in saved_ok
    assert len(rep_ok["chunks"]) == 3
    for idx, ch_entry in enumerate(rep_ok["chunks"]):
        assert ch_entry["index"] == idx
        assert ch_entry["text_preview"] == chunk_specs[idx][1]
        assert isinstance(ch_entry["start_s"], float)
        assert isinstance(ch_entry["end_s"], float)
        assert isinstance(ch_entry["integrated_lufs"], float)
    assert rep_ok["deviations"] == []
    assert rep_ok["warnings"] == []

    # Human summary for consistent chunks prints "loudness: final ... LUFS, chunks within ±... LU"
    _print_result_human(
        {
            "video_path": "out.mp4",
            "video_duration_s": 9.5,
            "duration_diff_s": 0.0,
            "voiceover_source": "tts",
            "voiceover_duration_s": 9.5,
            "tts_cost_usd": 0.0,
            "segments_count": 3,
            "reuse_count": 0,
            "loudness": rep_ok,
            "loudness_report_path": str(norm_json),
            "manifest_path": "manifest.yaml",
        }
    )
    human_ok = capsys.readouterr().out
    assert "loudness: final " in human_ok
    assert "chunks within ±" in human_ok

    # 3. End-to-end via resolve_or_generate_voiceover with --no-vo-normalize where chunk 1 is -12 dBFS vs others -26 dBFS
    para0 = " ".join(["Opening paragraph one about ancient ports."] * 4)
    para1 = " ".join(["Middle outlier paragraph two with loud sine."] * 4)
    para2 = " ".join(["Closing paragraph three about calm waters."] * 4)
    script = f"{para0}\n\n{para1}\n\n{para2}"

    class _OutlierProvider:
        def synthesize(
            self,
            text: str,
            voice: str = "nova",
            style: str | None = None,
            *,
            out_dir: str | None = None,
        ) -> VoiceSynthesisResult:
            dest = Path(out_dir or tmp_path)
            dest.mkdir(parents=True, exist_ok=True)
            is_outlier = "Middle outlier" in text
            amp = 0.25 if is_outlier else 0.05
            tag = "outlier" if is_outlier else f"normal_{abs(hash(text)) % 1000}"
            mp3_p = dest / f"{tag}.mp3"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    f"aevalsrc={amp}*sin(2*PI*440*t):s=44100:d=3.0",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "128k",
                    str(mp3_p),
                ],
                check=True,
            )
            toks = text.split()
            dur_per_w = 3.0 / max(1, len(toks))
            words = [
                {"word": w, "start": round(i * dur_per_w, 4), "end": round((i + 1) * dur_per_w, 4)}
                for i, w in enumerate(toks)
            ]
            return VoiceSynthesisResult(audio_path=str(mp3_p), words=words, duration_s=3.0, cost_usd=0.0)

    proj_unnorm = tmp_path / "proj_unnorm"
    res_unnorm = resolve_or_generate_voiceover(
        clean_script=script,
        project_dir=proj_unnorm,
        speed=1.0,
        vo_chunk_seconds=12.0,
        vo_gap=0.25,
        vo_lufs=-16.0,
        vo_lufs_tolerance=1.5,
        vo_normalize=False,
        vo_verify=False,
        provider=_OutlierProvider(),
        quiet=True,
    )
    ld_unnorm = res_unnorm["loudness"]
    loudness_file = proj_unnorm / "voiceover.loudness.json"
    assert loudness_file.is_file()
    saved_unnorm = json.loads(loudness_file.read_text(encoding="utf-8"))
    assert saved_unnorm == ld_unnorm
    assert len(ld_unnorm["chunks"]) == 3
    assert len(ld_unnorm["deviations"]) == 1
    assert ld_unnorm["deviations"][0]["index"] == 1
    assert ld_unnorm["deviations"][0]["abs_delta_lu"] > 1.5
    assert len(ld_unnorm["warnings"]) == 1
    assert "chunk 1" in ld_unnorm["warnings"][0]
    assert "Middle outlier" in ld_unnorm["warnings"][0]

    # Human summary prints the warning naming chunk 1
    _print_result_human(
        {
            "video_path": "out.mp4",
            "video_duration_s": 9.5,
            "duration_diff_s": 0.0,
            "voiceover_source": "tts",
            "voiceover_duration_s": 9.5,
            "tts_cost_usd": 0.0,
            "segments_count": 3,
            "reuse_count": 0,
            "warnings": list(ld_unnorm["warnings"]),
            "loudness": ld_unnorm,
            "loudness_report_path": str(loudness_file),
            "manifest_path": "manifest.yaml",
        }
    )
    human_warn = capsys.readouterr().out
    assert "chunk 1" in human_warn


def test_verifier_flags_missing_sea_salt_regenerates_only_failing_chunk_and_keeps_rejected(
    tmp_path: Path,
) -> None:
    """Fake transcriber missing 'sea salt' on chunk 2 attempt 1 -> flagged,
    triggers exactly one regeneration of only chunk 2, preserves .rejected, then passes.
    """
    import pytest
    from parallax.cut import (
        CutError,
        chunk_script,
        diff_script_vs_transcript,
        resolve_or_generate_voiceover,
    )
    from parallax.voiceover import VoiceSynthesisResult

    # Unit check on diff_script_vs_transcript with "97%" vs "ninety seven percent" and missing "sea salt"
    diff_bad = diff_script_vs_transcript(
        "Up to 97% of human sea salt is mineral.",
        [
            {"word": "up", "start": 0.0, "end": 0.2},
            {"word": "to", "start": 0.2, "end": 0.4},
            {"word": "ninety", "start": 0.4, "end": 0.7},
            {"word": "seven", "start": 0.7, "end": 1.0},
            {"word": "percent", "start": 1.0, "end": 1.3},
            {"word": "of", "start": 1.3, "end": 1.5},
            {"word": "human", "start": 1.5, "end": 1.8},
            {"word": "is", "start": 2.1, "end": 2.3},
            {"word": "mineral", "start": 2.3, "end": 2.7},
        ],
    )
    assert diff_bad["passed"] is False
    assert any(r["words"] == ["sea", "salt"] for r in diff_bad["missing_runs"])

    para1 = " ".join(["Roman concrete piers stood firm in the bay."] * 5)
    para2 = " ".join(["Up to 97% of human sea salt is pure mineral."] * 4)
    script = f"{para1}\n\n{para2}"
    chunks = chunk_script(script, max_chunk_seconds=15.0, speed=1.15)
    assert len(chunks) == 2

    provider_calls: list[str] = []
    chunk2_synth_attempts = 0

    class _Provider:
        def synthesize(
            self,
            text: str,
            voice: str = "nova",
            style: str | None = None,
            *,
            out_dir: str | None = None,
        ) -> VoiceSynthesisResult:
            nonlocal chunk2_synth_attempts
            provider_calls.append(text)
            dest = Path(out_dir or tmp_path)
            dest.mkdir(parents=True, exist_ok=True)
            tag = "c1"
            if "97%" in text:
                chunk2_synth_attempts += 1
                tag = f"c2_attempt{chunk2_synth_attempts}"
            mp3_p = dest / f"{tag}.mp3"
            mp3_p.write_bytes(f"AUDIO_{tag}".encode("utf-8"))
            toks = text.split()
            words = [
                {"word": w, "start": round(i * 0.3, 4), "end": round((i + 1) * 0.3, 4)}
                for i, w in enumerate(toks)
            ]
            return VoiceSynthesisResult(
                audio_path=str(mp3_p),
                words=words,
                duration_s=round(len(toks) * 0.3, 4),
                cost_usd=0.01,
            )

    def _fake_transcriber(audio_path: str, label: str = "") -> list[dict]:
        data = Path(audio_path).read_bytes()
        if b"c2_attempt1" in data:
            # Drop "sea salt" on the first attempt of chunk 2!
            text = chunks[1].replace("sea salt", "")
        elif b"c2_attempt" in data:
            text = chunks[1]
        elif "final" in label:
            text = f"{chunks[0]} {chunks[1]}"
        else:
            text = chunks[0]
        return [
            {"word": w, "start": round(i * 0.25, 3), "end": round((i + 1) * 0.25, 3)}
            for i, w in enumerate(text.split())
        ]

    proj = tmp_path / "verify_retry_proj"
    res = resolve_or_generate_voiceover(
        clean_script=script,
        project_dir=proj,
        vo_chunk_seconds=15.0,
        vo_verify=True,
        vo_retries=2,
        provider=_Provider(),
        transcriber=_fake_transcriber,
        quiet=True,
    )
    # Chunk 1 called once, Chunk 2 called twice (initial + 1 retry) -> 3 total calls, 1 retry used
    assert len(provider_calls) == 3
    assert chunk2_synth_attempts == 2
    assert res["tts_calls"] == 3
    assert res["retries_used"] == 1
    assert res["verification"]["passed"] is True
    assert res["verification"]["residual_mismatches"] == []
    rejected_files = list((proj / "cache" / "vo" / "chunks").glob("*.rejected"))
    assert len(rejected_files) >= 1

    # Now test residual mismatch when all retries fail: non-fatal by default, fatal under --strict-vo
    def _always_drop_transcriber(audio_path: str, label: str = "") -> list[dict]:
        text = chunks[0].replace("concrete piers", "")
        return [
            {"word": w, "start": round(i * 0.25, 3), "end": round((i + 1) * 0.25, 3)}
            for i, w in enumerate(text.split())
        ]

    proj_res = tmp_path / "verify_residual_proj"
    res_residual = resolve_or_generate_voiceover(
        clean_script=chunks[0],
        project_dir=proj_res,
        vo_chunk_seconds=0,
        vo_verify=True,
        vo_retries=2,
        strict_vo=False,
        provider=_Provider(),
        transcriber=_always_drop_transcriber,
        quiet=True,
    )
    assert res_residual["verification"]["passed"] is False
    assert res_residual["retries_used"] == 2
    assert len(res_residual["verification"]["residual_mismatches"]) == 1
    assert any(
        r["words"] == ["concrete", "piers"]
        for r in res_residual["verification"]["residual_mismatches"][0]["missing_runs"]
    )

    proj_strict = tmp_path / "verify_strict_proj"
    with pytest.raises(CutError) as exc_info:
        resolve_or_generate_voiceover(
            clean_script=chunks[0],
            project_dir=proj_strict,
            vo_chunk_seconds=0,
            vo_verify=True,
            vo_retries=1,
            strict_vo=True,
            provider=_Provider(),
            transcriber=_always_drop_transcriber,
            quiet=True,
        )
    assert exc_info.value.exit_code == 1
    assert "--strict-vo" in str(exc_info.value)


def test_verifier_trim_induced_mismatch_escalates_padding(tmp_path: Path) -> None:
    """If pause trim introduces a mismatch not present in the raw audio, re-runs trim with bigger padding."""
    from parallax.cut import resolve_or_generate_voiceover

    raw_wav = _make_burst_silence_burst_wav(
        tmp_path / "supplied_raw.wav",
        b1_dur=0.60,
        sil_dur=0.80,
        b2_dur=0.60,
    )
    words_json = tmp_path / "supplied_words.json"
    import json

    words_json.write_text(
        json.dumps(
            {
                "words": [
                    {"word": "97%", "start": 0.0, "end": 0.60},
                    {"word": "sea", "start": 1.40, "end": 1.70},
                    {"word": "salt", "start": 1.70, "end": 2.00},
                ]
            }
        ),
        encoding="utf-8",
    )

    trimmed_eval_count = 0

    def _transcriber(audio_path: str, label: str = "") -> list[dict]:
        nonlocal trimmed_eval_count
        if "raw" in label:
            # Raw audio has all words intact
            return [
                {"word": "ninety", "start": 0.0, "end": 0.2},
                {"word": "seven", "start": 0.2, "end": 0.4},
                {"word": "percent", "start": 0.4, "end": 0.6},
                {"word": "sea", "start": 1.4, "end": 1.7},
                {"word": "salt", "start": 1.7, "end": 2.0},
            ]
        trimmed_eval_count += 1
        if trimmed_eval_count == 1:
            # Simulate initial trim (padding=0.15) clipping "ninety seven"
            return [
                {"word": "percent", "start": 0.0, "end": 0.2},
                {"word": "sea", "start": 0.8, "end": 1.1},
                {"word": "salt", "start": 1.1, "end": 1.4},
            ]
        # Escalated padding (0.30) keeps full phrase intact
        return [
            {"word": "ninety", "start": 0.0, "end": 0.2},
            {"word": "seven", "start": 0.2, "end": 0.4},
            {"word": "percent", "start": 0.4, "end": 0.6},
            {"word": "sea", "start": 1.0, "end": 1.3},
            {"word": "salt", "start": 1.3, "end": 1.6},
        ]

    proj = tmp_path / "trim_esc_proj"
    res = resolve_or_generate_voiceover(
        clean_script="97% sea salt",
        project_dir=proj,
        audio_path=raw_wav,
        words_path=words_json,
        process_audio=True,
        speed=1.0,
        max_sentence_silence=0.20,
        vo_verify=True,
        transcriber=_transcriber,
        quiet=True,
    )
    assert res["verification"]["passed"] is True
    assert len(res["verification"]["trim_escalations"]) >= 1
    assert res["verification"]["trim_escalations"][0]["padding_s"] == 0.30



