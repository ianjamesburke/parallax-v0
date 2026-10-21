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
import os
import re
import time
import shutil
from difflib import SequenceMatcher
from pathlib import Path

from .ffmpeg_utils import run_ffmpeg
from .log import get_logger
from .shim import is_test_mode, output_dir

log = get_logger(__name__)

SOCIAL_SEED_VOICE_PROMPT = "A fast documentary narrator revealing a secret: fast-paced social-media-ad delivery, rapid with minimal pauses between lines, highly expressive animated storyteller building intrigue, deep, low, warm, slightly gravelly male voice, punchy emphasis on key words."


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
    verify: bool = False,
    voice_prompt: str | None = None,
) -> dict:
    """Generate voiceover and optionally cap overlong inter-word silences.

    Calls `openrouter.generate_tts` (always Gemini TTS via OpenRouter),
    then optionally caps gaps with bilateral lead-in and crossfades. A true
    ``trim_pauses`` caps gaps at 0.75s; a float sets the cap (minimum 0.4s).
    Standalone CLI synthesis opts out by default to preserve provider audio.
    Speed adjustment is
    handled separately by `audio.speedup` / `stage_speed_adjust`.

    `voice` is an OpenAI voice name (e.g. 'nova', 'shimmer', 'alloy'). See
    `parallax models show tts-mini` for the full list.

    `style` accepts presets from `gemini_tts.STYLE_PRESETS`
    (`rapid_fire`, `fast`, `calm`, `natural`); `style_hint` is freeform.

    Returns dict: {audio_path, words_path, words, total_duration_s}.
    """
    dest = Path(out_dir or str(output_dir()))
    dest.mkdir(parents=True, exist_ok=True)

    if isinstance(trim_pauses, float) and not isinstance(trim_pauses, bool) and trim_pauses < 0.4:
        raise ValueError("trim_pauses gap cap must be at least 0.4s to retain word lead-in")

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

    if voice_model == "seed-audio" and not voice_prompt:
        raise ValueError("seed-audio requires a meaningful voice_prompt")
    if voice_model == "elevenlabs":
        from . import elevenlabs as _elevenlabs
        raw_path, words_with_ends, raw_audio_duration = _elevenlabs.generate_tts(
            text=tts_text,
            voice=voice,
            out_dir=dest,
        )
    else:
        raw_path, words_with_ends, raw_audio_duration = openrouter.generate_tts(
            text=(f'{voice_prompt} He says: "{tts_text}"' if voice_model == "seed-audio" else tts_text),
            alias=voice_model,
            voice=voice,
            out_dir=dest,
            style=style,
            style_hint=style_hint,
        )

    if verify:
        from .whisper_backend import transcribe_wav
        heard = transcribe_wav(str(raw_path), label="TTS verification")
        def norm(v: str) -> list[str]:
            # modest number normalization catches 8/eight and 20%/twenty percent
            v = re.sub(r"20%", "twenty percent", v, flags=re.I)
            v = re.sub(r"\b8\b", "eight", v)
            return re.findall(r"[a-z]+", v.lower())
        wanted, got = norm(text), norm(" ".join(x["word"] for x in heard))
        score = SequenceMatcher(None, wanted, got).ratio()
        if score < .85 or len(got) > max(4, len(wanted) * 1.5):
            keep = Path(os.environ.get("PARALLAX_REJECT_DIR") or dest)
            keep.mkdir(parents=True, exist_ok=True)
            rejected = keep / f"{Path(raw_path).stem}.rejected{Path(raw_path).suffix}"
            shutil.move(str(raw_path), rejected)
            excerpt = " ".join(x["word"] for x in heard[:30])
            raise RuntimeError(f"TTS model did not read the script (word similarity {score:.2f}). Transcript: {excerpt!r}. Bad audio kept at {rejected}. Try dropping --style or another voice model; use --no-verify to override.")
    import shutil as _shutil
    raw_suffix = Path(raw_path).suffix or ".mp3"
    audio_path = dest / f"voiceover{raw_suffix}"
    trim_gap_count = 0
    trim_removed_s = 0.0
    if trim_pauses is False:
        log.info("voiceover: trim_pauses=false — skipping silence removal")
        _shutil.copy2(raw_path, audio_path)
        words_trimmed = list(words_with_ends)
        duration = max(words_trimmed[-1]["end"], raw_audio_duration) if words_trimmed else raw_audio_duration
    elif isinstance(trim_pauses, float) and not isinstance(trim_pauses, bool):
        words_trimmed, duration, trim_gap_count, trim_removed_s = _trim_long_pauses(
            Path(raw_path), list(words_with_ends), audio_path, max_gap_s=trim_pauses,
        )
    else:
        words_trimmed, duration, trim_gap_count, trim_removed_s = _trim_long_pauses(
            Path(raw_path), list(words_with_ends), audio_path,
        )
    words_trimmed = _restore_pronunciations(words_trimmed, pronunciations)

    if Path(raw_path).resolve() != audio_path.resolve():
        Path(raw_path).unlink(missing_ok=True)

    words_path = dest / "vo_words.json"
    words_path.write_text(json.dumps({
        "words": words_trimmed,
        "total_duration_s": duration,
    }, indent=2))

    elapsed = int((time.monotonic() - t0) * 1000)
    log.info("voiceover: done duration=%.2fs elapsed=%dms", duration, elapsed)

    return {
        "raw_audio_path": str(audio_path),
        "audio_path": str(audio_path),
        "words_path": str(words_path),
        "words": words_trimmed,
        "total_duration_s": duration,
        "trim_pauses_gaps": trim_gap_count,
        "trim_pauses_removed_s": round(trim_removed_s, 3),
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
    voice_prompt: str | None = None,
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
        verify=False,  # legacy programmatic wrapper preserves its historical no-local-model contract
        voice_prompt=voice_prompt,
    ))


def _trim_long_pauses(
    audio_path: Path,
    words: list[dict],
    out_path: Path,
    max_gap_s: float = 0.75,
) -> tuple[list[dict], float, int, float]:
    """Cap long gaps with lead-in on both sides and a silent crossfade.

    Alignment can place a word's start after its first audible phoneme, so
    joining at the timestamp itself can clip that phoneme. ``cap_pauses``
    retains half of ``max_gap_s`` before the next word (at least 0.2s),
    crossfades inside the retained quiet region, and adjusts word times for
    both the removed gap and crossfade overlap.
    """
    import shutil
    import tempfile

    if max_gap_s < 0.4:
        raise ValueError("trim_pauses gap cap must be at least 0.4s to retain word lead-in")

    probe = run_ffmpeg(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True,
    )
    total_dur = float(probe.stdout.strip()) if probe.stdout.strip() else (words[-1]["end"] if words else 0.0)
    if not any(b["start"] - a["end"] > max_gap_s for a, b in zip(words, words[1:])):
        shutil.copy2(audio_path, out_path)
        return list(words), max(words[-1]["end"], total_dur) if words else total_dur, 0, 0.0

    from .audio import cap_pauses
    with tempfile.TemporaryDirectory() as temp_dir:
        capped_wav = Path(temp_dir) / "capped.wav"
        result = cap_pauses(
            str(audio_path), str(capped_wav), max_gap_s=max_gap_s,
            crossfade_s=0.05, words=words,
        )
        encode = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                  "-i", str(capped_wav)]
        if out_path.suffix.lower() == ".mp3":
            encode += ["-c:a", "libmp3lame", "-b:a", "128k"]
        encode.append(str(out_path))
        run_ffmpeg(encode, check=True, capture_output=True, text=True)

    adjusted = result["adjusted_words"]
    gaps = result["gaps_trimmed"]
    total_removed = result["seconds_removed"]
    final_probe = run_ffmpeg(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(out_path)],
        capture_output=True, text=True,
    )
    new_dur = float(final_probe.stdout.strip()) if final_probe.stdout.strip() else result["new_duration_s"]
    log.info("_trim_long_pauses: %d gaps safely capped (%.2fs removed), duration %.2fs→%.2fs",
             gaps, total_removed, total_dur, new_dur)
    from . import runlog
    runlog.event(
        "audio.trim_pauses",
        level="DEBUG",
        gap_count=gaps,
        seconds_removed=round(total_removed, 3),
        duration_before_s=round(total_dur, 3),
        duration_after_s=round(new_dur, 3),
    )
    return adjusted, new_dur, gaps, total_removed


def mock_words(text: str) -> list[dict]:
    """Deterministic synthetic per-word timings for PARALLAX_TEST_MODE.

    Stands in for forced alignment, which cannot run on the silent stub audio.
    """
    words: list[dict] = []
    t = 0.0
    for w in re.sub(r'\[[^\]]*\]', '', text).split():
        dur = max(0.3, len(w) * 0.06)
        words.append({"word": w, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur + 0.05
    return words


def _mock_voiceover(text: str, dest: Path) -> str:
    words = mock_words(text)
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
        "raw_audio_path": str(audio_path),
        "audio_path": str(audio_path),
        "words_path": str(words_path),
        "words": words,
        "total_duration_s": total,
    })
