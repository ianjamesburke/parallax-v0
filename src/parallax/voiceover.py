"""Voiceover synthesis with pacing transforms.

`generate_voiceover` calls `openrouter.generate_tts` (always Gemini TTS via
OpenRouter), then runs `_apply_atempo` for speed adjustment and
`_trim_long_pauses` to surgically cut overlong inter-word silence.
`_mock_voiceover` produces a deterministic silent stand-in for
PARALLAX_TEST_MODE.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from .log import get_logger
from .shim import is_test_mode, output_dir

log = get_logger("tools_video")


def generate_voiceover(
    text: str,
    voice: str = "Kore",
    speed: float = 1.0,
    out_dir: str | None = None,
    style: str | None = None,
    style_hint: str | None = None,
) -> str:
    """Generate voiceover and apply pacing transforms.

    Calls `openrouter.generate_tts` (always Gemini TTS via OpenRouter),
    then post-processes with `_apply_atempo` and `_trim_long_pauses`.

    `voice` is a Gemini prebuilt voice name (e.g. 'Kore', 'Puck'). See
    `parallax models show tts-mini` for the full list.

    `speed` is an ffmpeg `atempo` factor applied AFTER synthesis. For
    Gemini, prefer `style` / `style_hint` for pacing; reserve `speed`
    for hard duration targets.

    `style` accepts presets from `gemini_tts.STYLE_PRESETS`
    (`rapid_fire`, `fast`, `calm`, `natural`); `style_hint` is freeform.

    Returns JSON: {audio_path, words_path, words, total_duration_s}.
    """
    dest = Path(out_dir or str(output_dir()))
    dest.mkdir(parents=True, exist_ok=True)

    if is_test_mode():
        return _mock_voiceover(text, dest)

    from . import openrouter

    tts_voice = voice
    tts_alias = "tts-mini"

    t0 = time.monotonic()
    log.info(
        "voiceover: voice=%s speed=%.2f chars=%d style=%s",
        voice, speed, len(text), style or style_hint or "default",
    )

    raw_path, words_with_ends, _raw_duration = openrouter.generate_tts(
        text=text,
        alias=tts_alias,
        voice=tts_voice,
        out_dir=dest,
        style=style,
        style_hint=style_hint,
    )

    raw_suffix = Path(raw_path).suffix or ".mp3"
    audio_path = dest / f"voiceover{raw_suffix}"
    sped_path = dest / f"voiceover_sped_tmp{raw_suffix}"
    if speed and abs(speed - 1.0) > 1e-3:
        words_sped, sped_duration = _apply_atempo(Path(raw_path), words_with_ends, sped_path, speed)
        words_sped, sped_duration = _trim_long_pauses(sped_path, words_sped, audio_path)
        sped_path.unlink(missing_ok=True)
    else:
        # No atempo — copy the raw file under the canonical filename, then
        # still run pause-trimming so silence between words gets cleaned up.
        import shutil as _shutil
        _shutil.copy2(raw_path, sped_path)
        words_sped, sped_duration = _trim_long_pauses(sped_path, list(words_with_ends), audio_path)
        sped_path.unlink(missing_ok=True)

    words_path = dest / "vo_words.json"
    words_path.write_text(json.dumps({
        "words": words_sped,
        "total_duration_s": sped_duration,
    }, indent=2))

    elapsed = int((time.monotonic() - t0) * 1000)
    log.info("voiceover: done duration=%.2fs elapsed=%dms", sped_duration, elapsed)

    return json.dumps({
        "audio_path": str(audio_path),
        "words_path": str(words_path),
        "words": words_sped,
        "total_duration_s": sped_duration,
    })


def _apply_atempo(raw_path: Path, words: list[dict], out_path: Path, speed: float) -> tuple[list[dict], float]:
    result = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
         "-i", str(raw_path), "-af", f"atempo={speed}",
         "-c:a", "libmp3lame", "-b:a", "128k", str(out_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out_path.exists():
        log.warning("atempo failed, using raw audio: %s", result.stderr[:200])
        raw_path.rename(out_path)
        return words, words[-1]["end"] if words else 0.0

    scale = 1.0 / speed
    sped = [
        {"word": w["word"], "start": round(w["start"] * scale, 3), "end": round(w["end"] * scale, 3)}
        for w in words
    ]
    duration = sped[-1]["end"] if sped else 0.0
    return sped, duration


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

    if not gaps:
        shutil.copy2(audio_path, out_path)
        return list(words), words[-1]["end"] if words else 0.0

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True,
    )
    total_dur = float(probe.stdout.strip()) if probe.stdout.strip() else (words[-1]["end"] if words else 0.0)

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

    result = subprocess.run(
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
    new_dur = adjusted[-1]["end"] if adjusted else 0.0
    total_removed = sum(e - s for s, e in gaps)
    log.info("_trim_long_pauses: %d gaps removed (%.2fs total), duration %.2fs→%.2fs",
             len(gaps), total_removed, total_dur, new_dur)
    return adjusted, new_dur


def _mock_voiceover(text: str, dest: Path) -> str:
    words_text = text.split()
    t = 0.0
    words = []
    for w in words_text:
        dur = max(0.3, len(w) * 0.06)
        words.append({"word": w, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur + 0.05
    total = words[-1]["end"] if words else 0.0

    # Silence mp3
    audio_path = dest / "voiceover.mp3"
    subprocess.run(
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
