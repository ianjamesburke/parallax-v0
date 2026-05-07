"""parallax.audio — audio utilities: transcription, word timestamps, silence trimming, speed adjust."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path

import yaml
from .ffmpeg_utils import run_ffmpeg
from . import whisper_backend

_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

log = logging.getLogger("parallax.audio")


def transcribe_words(input_path: str, out_path: str, no_whisperx: bool = False, words: list[dict] | None = None) -> list[dict]:
    """Transcribe audio or video to word-level timestamps.

    Writes {"words": [{word, start, end}], "total_duration_s": X} to out_path.
    Returns the word list.

    Prefers WhisperX (whisper + wav2vec2 forced alignment) when installed.
    Falls back to faster-whisper native word timestamps otherwise.
    """
    if words is not None:
        total = words[-1]["end"]
        log.info("transcribe_words: reusing %d preloaded words, span %.2f–%.2fs", len(words), words[0]["start"], total)
        Path(out_path).write_text(json.dumps({"words": words, "total_duration_s": total}, indent=2))
        return words

    tmp_audio: str | None = None
    try:
        tmp_audio = tempfile.mktemp(suffix=".wav")
        run_ffmpeg(
            ["ffmpeg", "-y", "-i", input_path, "-vn", "-ar", "44100", "-ac", "1", tmp_audio],
            check=True,
            capture_output=True,
        )

        words = whisper_backend.transcribe_wav(tmp_audio, label=Path(input_path).name, no_whisperx=no_whisperx)

        if not words:
            raise RuntimeError(f"transcribe_words: produced 0 words for {input_path}")

        total = words[-1]["end"]
        log.info("transcribe_words: %d words, span %.2f–%.2fs", len(words), words[0]["start"], total)
        Path(out_path).write_text(json.dumps({"words": words, "total_duration_s": total}, indent=2))
        return words
    finally:
        if tmp_audio:
            Path(tmp_audio).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Speed adjustment
# ---------------------------------------------------------------------------

def speedup(in_path: Path, out_path: Path, rate: float) -> Path:
    """Re-time `in_path` by `rate` via ffmpeg `atempo`, writing `out_path`.

    `rate > 1.0` shortens the audio (faster); `rate < 1.0` lengthens it.
    `rate == 1.0` is a no-op identity copy through ffmpeg (re-encodes to
    keep the output container/codec consistent with the speed-changed
    branch).

    Raises RuntimeError naming `audio.speedup` if ffmpeg fails or the
    output file is missing — no silent fallback. The caller is expected
    to handle the exception or let it propagate.

    `atempo` accepts factors in [0.5, 100.0]; values outside that range
    raise immediately so callers don't get a misleading ffmpeg error.
    """
    in_p = Path(in_path)
    out_p = Path(out_path)
    if not in_p.is_file():
        raise FileNotFoundError(f"audio.speedup: input not found: {in_p}")
    if rate <= 0:
        raise ValueError(f"audio.speedup: rate must be > 0, got {rate}")
    # ffmpeg's atempo filter is documented for [0.5, 100.0]; chain not
    # supported here because we have no real-world need for it yet.
    if rate < 0.5 or rate > 100.0:
        raise ValueError(
            f"audio.speedup: rate {rate} outside ffmpeg atempo range [0.5, 100.0]"
        )
    out_p.parent.mkdir(parents=True, exist_ok=True)

    # Pick a codec by extension so the output stays in a sane format.
    suffix = out_p.suffix.lower()
    codec_args: list[str]
    if suffix == ".wav":
        codec_args = ["-c:a", "pcm_s16le"]
    elif suffix == ".mp3":
        codec_args = ["-c:a", "libmp3lame", "-b:a", "128k"]
    elif suffix in (".m4a", ".aac"):
        codec_args = ["-c:a", "aac", "-b:a", "192k"]
    else:
        # Default to libmp3lame; ffmpeg will fail loudly if the container
        # disagrees with the codec, which is the right behaviour.
        codec_args = ["-c:a", "libmp3lame", "-b:a", "128k"]

    result = run_ffmpeg(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(in_p), "-af", f"atempo={rate}",
         *codec_args, str(out_p)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out_p.exists():
        stderr = (result.stderr or "").strip().splitlines()[-1:] if result.stderr else []
        tail = stderr[0] if stderr else "(no stderr)"
        raise RuntimeError(
            f"audio.speedup: ffmpeg atempo={rate} failed for {in_p} -> {out_p}: {tail}"
        )
    return out_p


def parse_by_pct(by: str) -> float:
    """Translate a `--by N%` string to a numeric atempo rate.

    `--by 30%` -> 1.30 (30% faster); `--by -20%` -> 0.80 (20% slower).
    The trailing `%` is required to make the "percent change" semantics
    explicit at the call site — bare numbers should use `--rate`.
    """
    s = by.strip()
    if not s.endswith("%"):
        raise ValueError(
            f"audio.parse_by_pct: '{by}' must end with '%' (e.g. '30%' or '-20%')"
        )
    try:
        pct = float(s[:-1])
    except ValueError as e:
        raise ValueError(f"audio.parse_by_pct: cannot parse '{by}' as a percentage") from e
    return 1.0 + pct / 100.0


# ---------------------------------------------------------------------------
# Silence detection and trimming
# ---------------------------------------------------------------------------

def detect_silences(
    audio_path: str,
    noise_db: float = -40.0,
    min_silence_s: float = 0.15,
) -> list[dict]:
    """Return list of {start, end, duration} for silent sections in audio.

    Runs ffmpeg silencedetect filter. Silences shorter than min_silence_s are excluded.
    """
    result = run_ffmpeg(
        [
            "ffmpeg", "-i", audio_path,
            "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    silences = []
    start: float | None = None
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            start = float(line.split("silence_start:")[1].strip())
        elif "silence_end:" in line and start is not None:
            parts = line.split("silence_end:")[1].split("|")
            end = float(parts[0].strip())
            dur = float(parts[1].split(":")[1].strip())
            silences.append({"start": round(start, 4), "end": round(end, 4), "duration": round(dur, 4)})
            start = None
    return silences


def trim_silence(
    plan_path: str,
    folder: str,
    cut_start: float,
    cut_end: float,
) -> dict:
    """Remove a specific time range from the plan's audio, words, and avatar track.

    Writes new versioned files to the same scratch directory.
    Updates plan.yaml in-place with the new file references.

    When an avatar track is present, it is trimmed first and audio is extracted
    from it — the avatar is the single source of truth for A/V timing.

    Returns summary: {seconds_removed, new_audio, new_words, new_avatar (or None)}.
    """
    folder_path = Path(folder).resolve()
    plan_file = Path(plan_path).resolve()
    plan = yaml.safe_load(plan_file.read_text())

    silence_range = [(cut_start, cut_end)]
    duration_removed = round(cut_end - cut_start, 4)

    # --- words ---
    words_abs = (folder_path / plan["words_path"]).resolve()
    words_data = json.loads(words_abs.read_text())
    words_list = words_data.get("words", words_data)
    adjusted = _adjust_words(words_list, silence_range)
    new_words = _next_versioned_path(words_abs)
    new_total = adjusted[-1]["end"] if adjusted else 0.0
    new_words.write_text(json.dumps({"words": adjusted, "total_duration_s": new_total}, indent=2))

    # --- avatar track + audio (avatar is audio source of truth) ---
    new_avatar: Path | None = None
    new_audio: Path
    avatar_cfg = plan.get("avatar") or {}
    avatar_track_rel = avatar_cfg.get("avatar_track")
    if avatar_track_rel:
        avatar_abs = (folder_path / avatar_track_rel).resolve()
        new_avatar = _trim_video(avatar_abs, _next_versioned_path(avatar_abs), silence_range)
        # Extract audio from the trimmed avatar — guaranteed in sync
        new_audio = new_avatar.with_suffix(".mp3")
        _extract_audio(new_avatar, new_audio)
    else:
        # No avatar track — trim standalone audio
        audio_abs = (folder_path / plan["audio_path"]).resolve()
        new_audio = _next_versioned_path(audio_abs)
        _trim_audio(audio_abs, new_audio, silence_range)

    # --- update plan in-place ---
    plan["audio_path"] = str(new_audio.relative_to(folder_path))
    plan["words_path"] = str(new_words.relative_to(folder_path))
    if new_avatar is not None and "avatar" in plan:
        plan["avatar"]["avatar_track"] = str(new_avatar.relative_to(folder_path))
        plan["avatar"].pop("avatar_track_keyed", None)

    plan_file.write_text(yaml.dump(plan, allow_unicode=True, sort_keys=False))

    return {
        "seconds_removed": duration_removed,
        "new_audio": str(new_audio),
        "new_words": str(new_words),
        "new_avatar": str(new_avatar) if new_avatar else None,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _next_versioned_path(path: Path) -> Path:
    """Return path with _vN suffix, auto-incrementing past any existing files."""
    m = re.search(r"^(.+)_v(\d+)$", path.stem)
    if m:
        base, n = m.group(1), int(m.group(2)) + 1
    else:
        base, n = path.stem, 2
    while True:
        candidate = path.parent / f"{base}_v{n}{path.suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _build_aselect_expr(silences: list[tuple[float, float]]) -> str:
    parts = "+".join(f"between(t,{s:.4f},{e:.4f})" for s, e in silences)
    return f"not({parts})"


def _trim_audio(src: Path, dst: Path, silences: list[tuple[float, float]]) -> None:
    expr = _build_aselect_expr(silences)
    run_ffmpeg(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-af", f"aselect='{expr}',asetpts=N/SR/TB",
            str(dst),
        ],
        check=True,
        capture_output=True,
    )


def _trim_video(src: Path, dst: Path, silences: list[tuple[float, float]]) -> Path:
    """Frame-accurate removal of silence ranges from a video+audio track.

    Outputs ProRes 422 HQ (.mov) regardless of source container. H.264 re-encode
    changes color range metadata which breaks downstream chroma keying. Returns
    the actual output path.
    """
    expr = _build_aselect_expr(silences)
    out = dst.with_suffix(".mov")
    run_ffmpeg(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-vf", f"select='{expr}',setpts=N/FRAME_RATE/TB",
            "-af", f"aselect='{expr}',asetpts=N/SR/TB",
            "-c:v", "prores_ks", "-profile:v", "3",
            "-c:a", "pcm_s16le",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def _extract_audio(src: Path, dst: Path) -> None:
    """Extract audio stream from a video file to MP3."""
    run_ffmpeg(
        ["ffmpeg", "-y", "-i", str(src), "-vn", "-c:a", "libmp3lame", "-q:a", "0", str(dst)],
        check=True,
        capture_output=True,
    )


def _cumulative_silence_before(t: float, silences: list[tuple[float, float]]) -> float:
    """Total silence duration removed at or before time t."""
    total = 0.0
    for s_start, s_end in silences:
        if s_start >= t:
            break
        total += min(t, s_end) - s_start
    return total


def _adjust_words(words: list[dict], silences: list[tuple[float, float]]) -> list[dict]:
    """Shift word timestamps to account for removed silence ranges."""
    adjusted = []
    for w in words:
        new_start = round(max(0.0, w["start"] - _cumulative_silence_before(w["start"], silences)), 4)
        new_end = round(max(new_start, w["end"] - _cumulative_silence_before(w["end"], silences)), 4)
        adjusted.append({"word": w["word"], "start": new_start, "end": new_end})
    return adjusted



# ---------------------------------------------------------------------------
# WhisperX-driven pause capping (word-boundary based, no amplitude probing)
# ---------------------------------------------------------------------------

def cap_pauses(
    input_path: str,
    output_path: str,
    max_gap_s: float = 0.75,
    crossfade_s: float = 0.05,
    words: list[dict] | None = None,
) -> dict:
    """Trim every inter-word gap > max_gap_s down to exactly max_gap_s.

    Word boundaries come from `forced_align.align_words` (WhisperX) — there
    is no amplitude probing or silence detection. Each oversize gap is
    split symmetrically: the first `max_gap_s/2` seconds stay attached to
    the previous word as breathing room, and the last `max_gap_s/2`
    seconds stay attached to the next word's lead-in. Adjacent kept
    segments are joined with a brief `crossfade_s` acrossfade so the cuts
    don't click. Gaps already at or below `max_gap_s` are left alone.

    Returns a summary dict — original/new duration, gaps trimmed, total
    seconds removed. Output is written to `output_path` as wav (lossless,
    matches voiceover pipeline expectations).

    Designed to be agent-callable: input is a single audio file, output
    is a single trimmed audio file, no plan YAML or project layout
    required.
    """
    src = Path(input_path).expanduser().resolve()
    dst = Path(output_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"cap_pauses: input not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    if words is None:
        from . import forced_align
        # WhisperX needs wav-ish input. ffmpeg-based loaders accept m4a/mp3 fine,
        # but we transcode to a temp wav so probe + filter math don't have to
        # care about variable-bitrate containers.
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_for_align = Path(tmpdir) / "for_align.wav"
            run_ffmpeg(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(src), "-ar", "44100", "-ac", "1", str(wav_for_align)],
                check=True, capture_output=True,
            )
            words = forced_align.align_words(wav_for_align)

    if not words:
        raise RuntimeError(f"cap_pauses: no words detected in {src}")

    # Probe the source for total duration so the trailing tail (after the
    # last word) is preserved — we never want to cut sound past the last
    # word, only inter-word silence.
    probe = run_ffmpeg(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
        capture_output=True, text=True,
    )
    total_dur = float(probe.stdout.strip()) if probe.stdout.strip() else words[-1]["end"]

    # Identify the cuts: each oversize gap shrinks to max_gap_s with the
    # split half/half across the joint.
    half = max_gap_s / 2.0
    cuts: list[tuple[float, float]] = []
    for i in range(len(words) - 1):
        gap_start = words[i]["end"]
        gap_end = words[i + 1]["start"]
        gap = gap_end - gap_start
        if gap > max_gap_s:
            cuts.append((gap_start + half, gap_end - half))

    if not cuts:
        log.info("cap_pauses: no gaps > %.2fs — copying input unchanged", max_gap_s)
        # Re-encode to wav for output consistency rather than copying the
        # source format unchanged (callers expect wav).
        run_ffmpeg(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(src), "-ar", "44100", "-ac", "1", str(dst)],
            check=True, capture_output=True,
        )
        return {
            "input": str(src), "output": str(dst),
            "original_duration_s": round(total_dur, 3),
            "new_duration_s": round(total_dur, 3),
            "gaps_trimmed": 0, "seconds_removed": 0.0,
            "max_gap_s": max_gap_s, "crossfade_s": crossfade_s,
            "adjusted_words": list(words),  # no segments removed, timestamps unchanged
        }

    # Build keep-segments — the inverse of the cuts.
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for cut_start, cut_end in cuts:
        if cursor < cut_start:
            keep.append((cursor, cut_start))
        cursor = cut_end
    if cursor < total_dur:
        keep.append((cursor, total_dur))

    # ffmpeg filter_complex: atrim each kept slice into its own labeled
    # stream, then chain acrossfade between them so each joint gets a
    # brief equal-power fade (`acrossfade c1=tri:c2=tri` is the default).
    # acrossfade overlaps the last `d` of A with the first `d` of B, so
    # the resulting clip is len(A) + len(B) - d.
    filter_parts: list[str] = []
    for i, (s, e) in enumerate(keep):
        filter_parts.append(
            f"[0:a]atrim=start={s:.4f}:end={e:.4f},asetpts=PTS-STARTPTS[s{i}]"
        )

    cf = max(0.001, min(crossfade_s, half))  # cap crossfade so it can't exceed kept padding
    if len(keep) == 1:
        out_label = "s0"
    else:
        for i in range(len(keep) - 1):
            left = "s0" if i == 0 else f"c{i - 1}"
            right = f"s{i + 1}"
            out = f"c{i}" if i < len(keep) - 2 else "out"
            filter_parts.append(f"[{left}][{right}]acrossfade=d={cf:.4f}:c1=tri:c2=tri[{out}]")
        out_label = "out"

    filter_str = ";".join(filter_parts)

    run_ffmpeg(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(src),
         "-filter_complex", filter_str,
         "-map", f"[{out_label}]",
         "-ar", "44100", "-ac", "1",
         str(dst)],
        check=True, capture_output=True,
    )

    new_total = sum(e - s for s, e in keep) - cf * (len(keep) - 1)
    removed = total_dur - new_total
    log.info(
        "cap_pauses: %d gaps capped to %.2fs (removed %.2fs total, %.2fs → %.2fs)",
        len(cuts), max_gap_s, removed, total_dur, new_total,
    )
    adjusted_words = _adjust_words(words, cuts)
    return {
        "input": str(src), "output": str(dst),
        "original_duration_s": round(total_dur, 3),
        "new_duration_s": round(new_total, 3),
        "gaps_trimmed": len(cuts),
        "seconds_removed": round(removed, 3),
        "max_gap_s": max_gap_s, "crossfade_s": cf,
        "adjusted_words": adjusted_words,
    }


# ---------------------------------------------------------------------------
# Onset padding — ensure each word has at least pad_s of lead-in silence
# ---------------------------------------------------------------------------

def pad_onsets(
    input_path: str,
    output_path: str,
    words: list[dict],
    pad_s: float = 0.05,
) -> dict:
    """Insert silence before any word onset whose lead-in is shorter than pad_s.

    Complements cap_pauses: where cap_pauses removes excess silence, pad_onsets
    adds silence to restore minimum lead-in after a prior trim clipped too close
    to a word boundary. Requires a pre-existing words JSON (e.g. from
    `parallax audio transcribe`) — raw onset detection from audio is not supported.

    Output is written as a mono 44100 Hz wav. Segments are concatenated via
    ffmpeg filter_complex; the original audio is never decoded to Python arrays.

    Returns a summary dict: input, output, original_duration_s, new_duration_s,
    onsets_padded, seconds_added.
    """
    src = Path(input_path).expanduser().resolve()
    dst = Path(output_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"pad_onsets: input not found: {src}")
    if not words:
        raise ValueError("pad_onsets: words list is empty")
    dst.parent.mkdir(parents=True, exist_ok=True)

    probe = run_ffmpeg(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
        capture_output=True, text=True,
    )
    total_dur = float(probe.stdout.strip()) if probe.stdout.strip() else words[-1]["end"]

    # Find each word whose lead-in gap is below pad_s.
    # Insertion point is at prev_end (end of previous word, or 0) — we extend
    # the gap there rather than cutting into the word itself.
    insertions: list[tuple[float, float]] = []
    for i, word in enumerate(words):
        prev_end = words[i - 1]["end"] if i > 0 else 0.0
        lead_in = word["start"] - prev_end
        if lead_in < pad_s - 1e-6:
            insertions.append((prev_end, round(pad_s - lead_in, 6)))

    if not insertions:
        run_ffmpeg(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(src), "-ar", "44100", "-ac", "1", str(dst)],
            check=True, capture_output=True,
        )
        return {
            "input": str(src), "output": str(dst),
            "original_duration_s": round(total_dur, 3),
            "new_duration_s": round(total_dur, 3),
            "onsets_padded": 0, "seconds_added": 0.0,
        }

    # Partition the original audio at each insertion point and interleave silence.
    # For N insertions at times p0 < p1 < ..., we get N+1 audio segments:
    #   [0, p0), [p0, p1), ..., [p_(N-1), total_dur)
    # interleaved with N silence clips:
    #   output = seg0 + sil0 + seg1 + sil1 + ... + segN
    split_points = [ins[0] for ins in insertions]

    seg_bounds: list[tuple[float, float]] = []
    prev = 0.0
    for p in split_points:
        seg_bounds.append((prev, p))
        prev = p
    seg_bounds.append((prev, total_dur))

    sample_rate = 44100
    filter_parts: list[str] = []
    all_labels: list[str] = []

    seg_idx = 0
    for i, (s, e) in enumerate(seg_bounds):
        if e > s + 1e-6:
            label = f"seg{seg_idx}"
            filter_parts.append(
                f"[0:a]atrim=start={s:.6f}:end={e:.6f},asetpts=PTS-STARTPTS[{label}]"
            )
            all_labels.append(label)
            seg_idx += 1

        if i < len(insertions):
            sil_dur = insertions[i][1]
            sil_label = f"sil{i}"
            filter_parts.append(
                f"aevalsrc=0:s={sample_rate}:d={sil_dur:.6f}[{sil_label}]"
            )
            all_labels.append(sil_label)

    n_streams = len(all_labels)
    labels_str = "".join(f"[{lb}]" for lb in all_labels)
    filter_parts.append(f"{labels_str}concat=n={n_streams}:v=0:a=1[out]")
    filter_str = ";".join(filter_parts)

    run_ffmpeg(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(src),
         "-filter_complex", filter_str,
         "-map", "[out]",
         "-ar", str(sample_rate), "-ac", "1",
         str(dst)],
        check=True, capture_output=True,
    )

    seconds_added = sum(ins[1] for ins in insertions)
    new_total = total_dur + seconds_added
    log.info(
        "pad_onsets: %d onsets padded (+%.3fs total, %.2fs → %.2fs)",
        len(insertions), seconds_added, total_dur, new_total,
    )
    return {
        "input": str(src), "output": str(dst),
        "original_duration_s": round(total_dur, 3),
        "new_duration_s": round(new_total, 3),
        "onsets_padded": len(insertions),
        "seconds_added": round(seconds_added, 3),
    }
