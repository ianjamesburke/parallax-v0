"""Voiceover synthesis (pure TTS).

`generate_voiceover` calls `openrouter.generate_tts` (always Gemini TTS via
OpenRouter) and runs `_trim_long_pauses` to surgically cut overlong
inter-word silence. Speed adjustment is no longer the voiceover module's
concern — it lives in `audio.speedup` and runs as `stage_speed_adjust`
after this stage.

`_mock_voiceover` produces a deterministic silent stand-in for
PARALLAX_TEST_MODE.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .ffmpeg_utils import run_ffmpeg
from .log import get_logger
from .shim import is_test_mode, output_dir

log = get_logger(__name__)


def apply_pronunciations(text: str, pronunciations: dict[str, str]) -> str:
    """Substitute phonetic spellings before TTS. Word-boundary, case-insensitive."""
    for word, phonetic in pronunciations.items():
        text = re.sub(r'\b' + re.escape(word) + r'\b', phonetic, text, flags=re.IGNORECASE)
    return text


def _restore_pronunciations(
    words: list[dict], pronunciations: dict[str, str]
) -> list[dict]:
    """Restore original spellings in aligned word list after forced alignment."""
    if not pronunciations:
        return list(words)

    def _norm(s: str) -> str:
        return re.sub(r'[-]', '', s).lower().strip(".,!?;:")

    phonetic_map = {_norm(v): k for k, v in pronunciations.items()}
    result = []
    for w in words:
        n = _norm(w["word"])
        original = phonetic_map.get(n)
        result.append({**w, "word": original} if original else w)
    return result


def generate_voiceover_dict(
    text: str,
    voice: str = "nova",
    out_dir: str | None = None,
    style: str | None = None,
    style_hint: str | None = None,
    voice_model: str = "tts-mini",
    pronunciations: dict[str, str] | None = None,
    trim_pauses: bool | float = True,
) -> dict:
    """Generate voiceover and trim overlong inter-word silences.

    Calls `openrouter.generate_tts` (always Gemini TTS via OpenRouter),
    then post-processes with `_trim_long_pauses`. Speed adjustment is
    handled separately by `audio.speedup` / `stage_speed_adjust`.

    `voice` is an OpenAI voice name (e.g. 'nova', 'shimmer', 'alloy'). See
    `parallax models show tts-mini` for the full list.

    `style` accepts presets from `gemini_tts.STYLE_PRESETS`
    (`rapid_fire`, `fast`, `calm`, `natural`); `style_hint` is freeform.

    Returns dict: {audio_path, words_path, words, total_duration_s}.
    """
    dest = Path(out_dir or str(output_dir()))
    dest.mkdir(parents=True, exist_ok=True)

    pronunciations = pronunciations or {}

    if is_test_mode():
        return json.loads(_mock_voiceover(text, dest))

    from . import openrouter

    t0 = time.monotonic()
    log.info(
        "voiceover: voice=%s voice_model=%s chars=%d style=%s",
        voice, voice_model, len(text), style or style_hint or "default",
    )

    tts_text = apply_pronunciations(text, pronunciations) if pronunciations else text
    if pronunciations and tts_text != text:
        log.info("voiceover: applying %d pronunciation substitution(s)", len(pronunciations))

    raw_path, words_with_ends, raw_audio_duration = openrouter.generate_tts(
        text=tts_text,
        alias=voice_model,
        voice=voice,
        out_dir=dest,
        style=style,
        style_hint=style_hint,
    )

    import shutil as _shutil
    raw_suffix = Path(raw_path).suffix or ".mp3"
    audio_path = dest / f"voiceover{raw_suffix}"
    if trim_pauses is False:
        log.info("voiceover: trim_pauses=false — skipping silence removal")
        _shutil.copy2(raw_path, audio_path)
        words_trimmed = list(words_with_ends)
        duration = max(words_trimmed[-1]["end"], raw_audio_duration) if words_trimmed else raw_audio_duration
    elif isinstance(trim_pauses, float) and not isinstance(trim_pauses, bool):
        words_trimmed, duration = _trim_long_pauses(
            Path(raw_path), list(words_with_ends), audio_path, max_gap_s=trim_pauses,
        )
    else:
        words_trimmed, duration = _trim_long_pauses(
            Path(raw_path), list(words_with_ends), audio_path,
        )
    words_trimmed = _restore_pronunciations(words_trimmed, pronunciations)

    words_path = dest / "vo_words.json"
    words_path.write_text(json.dumps({
        "words": words_trimmed,
        "total_duration_s": duration,
    }, indent=2))

    elapsed = int((time.monotonic() - t0) * 1000)
    log.info("voiceover: done duration=%.2fs elapsed=%dms", duration, elapsed)

    return {
        "audio_path": str(audio_path),
        "words_path": str(words_path),
        "words": words_trimmed,
        "total_duration_s": duration,
    }


def generate_voiceover(
    text: str,
    voice: str = "nova",
    out_dir: str | None = None,
    style: str | None = None,
    style_hint: str | None = None,
    voice_model: str = "tts-mini",
    pronunciations: dict[str, str] | None = None,
    trim_pauses: bool | float = True,
) -> str:
    """JSON-string wrapper around generate_voiceover_dict. Kept for CLI/external callers.

    Returns JSON: {audio_path, words_path, words, total_duration_s}.
    """
    return json.dumps(generate_voiceover_dict(
        text=text,
        voice=voice,
        out_dir=out_dir,
        style=style,
        style_hint=style_hint,
        voice_model=voice_model,
        pronunciations=pronunciations,
        trim_pauses=trim_pauses,
    ))


def _trim_long_pauses(
    audio_path: Path,
    words: list[dict],
    out_path: Path,
    max_gap_s: float = 0.4,
    keep_gap_s: float = 0.1,
) -> tuple[list[dict], float]:
    """Remove inter-word gaps > max_gap_s, trimming each to keep_gap_s.

    Uses ffmpeg atrim+concat for surgical cuts driven by word timestamps.
    No amplitude detection — purely timestamp-based.
    Returns (adjusted_words, new_duration_s).
    """
    import shutil

    gaps: list[tuple[float, float]] = []
    for i in range(len(words) - 1):
        gap = words[i + 1]["start"] - words[i]["end"]
        if gap > max_gap_s:
            gaps.append((words[i]["end"] + keep_gap_s, words[i + 1]["start"]))

    probe = run_ffmpeg(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True,
    )
    total_dur = float(probe.stdout.strip()) if probe.stdout.strip() else (words[-1]["end"] if words else 0.0)

    if not gaps:
        shutil.copy2(audio_path, out_path)
        return list(words), max(words[-1]["end"], total_dur) if words else 0.0

    # Build the audio segments to keep
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for trim_start, trim_end in gaps:
        if cursor < trim_start:
            keep.append((cursor, trim_start))
        cursor = trim_end
    if cursor < total_dur:
        keep.append((cursor, total_dur))

    n = len(keep)
    parts = [
        f"[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS[s{i}]"
        for i, (s, e) in enumerate(keep)
    ]
    concat_in = "".join(f"[s{i}]" for i in range(n))
    parts.append(f"{concat_in}concat=n={n}:v=0:a=1[out]")

    result = run_ffmpeg(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(audio_path),
         "-filter_complex", ";".join(parts),
         "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "128k",
         str(out_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.warning("_trim_long_pauses: ffmpeg failed (%s), keeping original", result.stderr[:200])
        shutil.copy2(audio_path, out_path)
        return list(words), words[-1]["end"] if words else 0.0

    # Shift timestamps: each word shifts back by total gap removed before it
    shifts = [0.0] * len(words)
    for trim_start, trim_end in gaps:
        removed = trim_end - trim_start
        for j, w in enumerate(words):
            if w["start"] >= trim_end:
                shifts[j] += removed

    adjusted = [
        {"word": w["word"],
         "start": round(w["start"] - shifts[j], 3),
         "end": round(w["end"] - shifts[j], 3)}
        for j, w in enumerate(words)
    ]
    total_removed = sum(e - s for s, e in gaps)
    new_dur = max(adjusted[-1]["end"], total_dur - total_removed) if adjusted else 0.0
    log.info("_trim_long_pauses: %d gaps removed (%.2fs total), duration %.2fs→%.2fs",
             len(gaps), total_removed, total_dur, new_dur)
    from . import runlog
    runlog.event(
        "audio.trim_pauses",
        level="DEBUG",
        gap_count=len(gaps),
        seconds_removed=round(total_removed, 3),
        duration_before_s=round(total_dur, 3),
        duration_after_s=round(new_dur, 3),
    )
    return adjusted, new_dur


def _mock_voiceover(text: str, dest: Path) -> str:
    words_text = re.sub(r'\[[^\]]*\]', '', text).split()
    t = 0.0
    words = []
    for w in words_text:
        dur = max(0.3, len(w) * 0.06)
        words.append({"word": w, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur + 0.05
    total = words[-1]["end"] if words else 0.0

    # Silence mp3
    audio_path = dest / "voiceover.mp3"
    run_ffmpeg(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
         "-t", str(total), "-c:a", "libmp3lame", str(audio_path)],
        capture_output=True,
    )
    words_path = dest / "vo_words.json"
    words_path.write_text(json.dumps({"words": words, "total_duration_s": total}, indent=2))
    return json.dumps({
        "audio_path": str(audio_path),
        "words_path": str(words_path),
        "words": words,
        "total_duration_s": total,
    })
