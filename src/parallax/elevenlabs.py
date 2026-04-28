"""ElevenLabs synthesis — the only place we call the ElevenLabs API.

Two public entry points:

  - `resolve_voice(voice, api_key)` — turn a voice alias / name / raw ID
    into an ElevenLabs voice_id.
  - `synthesize(text, voice_id, out_dir)` — call the TTS endpoint, write
    `voiceover_raw.mp3`, derive per-word timestamps, and return
    `(audio_path, words, total_duration_s)`.

`openrouter._tts_elevenlabs` is the canonical narrative-facing wrapper
(handles the `voice='eleven:<id>'` escape hatch in `generate_tts`); the
parallax pipeline orchestration in `tools_video.generate_voiceover` adds
atempo + pause-trimming on top. This module owns nothing about pacing —
it returns raw synthesis only.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

from .log import get_logger

log = get_logger("elevenlabs")

# Verified 2026-04-23 against the Scale plan ($299/mo, 1.8M credits, 1 credit/char).
COST_PER_CHAR = 0.000166

# Offline shorthand aliases → ElevenLabs voice IDs. Keeps common voices
# resolvable without a live API call. Anything else flows through
# `resolve_voice`'s name-match path.
VOICE_IDS: dict[str, str] = {
    "george": "JBFqnCBsd6RMkjVDRZzb",
    "rachel": "21m00Tcm4TlvDq8ikWAM",
    "domi": "AZnzlk1XvdvUeBnXmlld",
    "bella": "EXAVITQu4vr4xnSDxMaL",
    "daniel": "onwK4e9ZLuTAKqWW03F9",
    "arnold": "VR6AewLTigWG4xSOukaG",
}

_RAW_ID_MIN_LEN = 18  # ElevenLabs voice IDs are 20 alphanumeric characters.


def resolve_voice(voice: str, api_key: str) -> str:
    """Resolve a voice alias / name / raw ID to an ElevenLabs voice_id.

    Resolution order:
      1. Offline alias dict (e.g. 'george') — no API call.
      2. Looks like a raw voice ID (≥18 alphanumeric chars) — use as-is.
      3. Live name match against `voices.get_all()` (exact, then partial).
    """
    alias_id = VOICE_IDS.get(voice.lower())
    if alias_id:
        return alias_id

    if len(voice) >= _RAW_ID_MIN_LEN and voice.replace("-", "").isalnum():
        return voice

    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        voices = client.voices.get_all().voices
    except Exception as e:
        raise ValueError(
            f"Could not fetch ElevenLabs voice list to resolve {voice!r}: {e}"
        ) from e

    needle = voice.lower()
    for v in voices:
        if (v.name or "").lower() == needle:
            log.info("voice resolved: %r → %s (%s)", voice, v.voice_id, v.name)
            return v.voice_id
    matches = [v for v in voices if needle in (v.name or "").lower()]
    if matches:
        chosen = matches[0]
        log.info("voice resolved (partial): %r → %s (%s)", voice, chosen.voice_id, chosen.name)
        return chosen.voice_id

    raise ValueError(
        f"No ElevenLabs voice found matching {voice!r}. "
        f"Run 'parallax voices --filter {voice}' to search, or pass a raw voice ID."
    )


def synthesize(
    text: str,
    *,
    voice_id: str,
    out_dir: Path,
    api_key: str,
) -> tuple[Path, list[dict], float]:
    """Call ElevenLabs `convert_with_timestamps`, write the raw mp3, and
    derive per-word timestamps from the character alignment.

    Returns `(raw_audio_path, words, total_duration_s)` where
    `words = [{word, start, end}]`. Pacing transforms (atempo,
    pause-trimming) live in `tools_video.generate_voiceover`.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    from elevenlabs.client import ElevenLabs
    client = ElevenLabs(api_key=api_key)
    response = client.text_to_speech.convert_with_timestamps(
        voice_id=voice_id,
        text=text,
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
    )

    audio_b64 = getattr(response, "audio_base_64", None) or getattr(response, "audio_base64", None)
    if not audio_b64:
        raise RuntimeError("ElevenLabs response missing audio data")
    raw_path = out_dir / "voiceover_raw.mp3"
    raw_path.write_bytes(base64.b64decode(audio_b64))

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(raw_path)],
        capture_output=True, text=True,
    )
    raw_duration = float(probe.stdout.strip()) if probe.stdout.strip() else 0.0

    alignment = getattr(response, "alignment", None)
    if alignment is None:
        raise RuntimeError("ElevenLabs response missing alignment data")
    words = _words_from_alignment(
        chars=list(alignment.characters),
        starts=list(alignment.character_start_times_seconds),
        total_duration=raw_duration,
    )
    return raw_path, words, raw_duration


def _words_from_alignment(
    *, chars: list[str], starts: list[float], total_duration: float,
) -> list[dict]:
    """Group character-level timestamps into [{word, start, end}].

    Space characters mark the *acoustic* end of the preceding word — that
    timestamp is what `_trim_long_pauses` needs to detect inter-sentence
    silence accurately. Falls back to the next word's start, then to the
    total duration, when no space follows.
    """
    raw: list[dict] = []
    cur_word = ""
    word_start: float | None = None
    for i, ch in enumerate(chars):
        if ch.strip() == "":
            if cur_word and word_start is not None:
                raw.append({"word": cur_word, "start": word_start, "acoustic_end": starts[i]})
                cur_word = ""
                word_start = None
        else:
            if word_start is None:
                word_start = starts[i]
            cur_word += ch
    if cur_word and word_start is not None:
        raw.append({"word": cur_word, "start": word_start, "acoustic_end": None})

    out: list[dict] = []
    for i, w in enumerate(raw):
        if w.get("acoustic_end") is not None:
            end = w["acoustic_end"]
        elif i + 1 < len(raw):
            end = raw[i + 1]["start"]
        else:
            end = total_duration
        out.append({"word": w["word"], "start": round(w["start"], 3), "end": round(end, 3)})
    return out


def cost_for(text: str) -> float:
    """ElevenLabs cost is per character of input text on the Scale plan."""
    return round(len(text) * COST_PER_CHAR, 4)
