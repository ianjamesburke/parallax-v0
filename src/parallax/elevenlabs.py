"""ElevenLabs TTS provider.

Calls /v1/text-to-speech/{voice_id}/with-timestamps for audio + character-level
alignment, then aggregates characters into word-level timestamps.

API key: ELEVENLABS_API_KEY env var (fallback: ELEVEN_LABS_API_KEY).
Default voice: Rachel (21m00Tcm4TlvDq8ikWAM). Pass any ElevenLabs voice ID
as the `voice` argument to override.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

_API_BASE = "https://api.elevenlabs.io/v1"
_DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel
_DEFAULT_MODEL = "eleven_multilingual_v2"

# Known OpenRouter-style voice names that shouldn't be sent as ElevenLabs IDs.
_OPENROUTER_VOICE_NAMES = {
    "nova", "shimmer", "alloy", "echo", "fable", "onyx",
    "Kore", "Puck", "Charon", "Fenrir", "Aoede",
}


def _api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVEN_LABS_API_KEY")
    if not key:
        raise RuntimeError(
            "ELEVENLABS_API_KEY is required for ElevenLabs TTS. "
            "Set it in your environment or .env file. "
            "Get one at https://elevenlabs.io → Profile → API Keys."
        )
    return key


def _resolve_voice_id(voice: str) -> str:
    """Return voice_id to send to ElevenLabs.

    If `voice` looks like an OpenRouter alias (known name or short word),
    fall back to the default voice. Otherwise treat it as an ElevenLabs
    voice ID and pass through directly.
    """
    if voice in _OPENROUTER_VOICE_NAMES or not voice or len(voice) < 10:
        return _DEFAULT_VOICE_ID
    return voice


def _chars_to_words(
    chars: list[str],
    starts: list[float],
    ends: list[float],
) -> list[dict[str, Any]]:
    """Aggregate character-level ElevenLabs alignment into word-level timestamps."""
    words: list[dict[str, Any]] = []
    cur_chars: list[str] = []
    cur_start: float | None = None
    cur_end: float = 0.0

    for ch, s, e in zip(chars, starts, ends):
        if ch in (" ", "\n", "\t"):
            if cur_chars:
                words.append({
                    "word": "".join(cur_chars),
                    "start": round(cur_start, 3),  # type: ignore[arg-type]
                    "end": round(cur_end, 3),
                })
                cur_chars = []
                cur_start = None
        else:
            if cur_start is None:
                cur_start = s
            cur_chars.append(ch)
            cur_end = e

    if cur_chars and cur_start is not None:
        words.append({
            "word": "".join(cur_chars),
            "start": round(cur_start, 3),
            "end": round(cur_end, 3),
        })

    return words


def list_voices(category: str = "premade") -> list[dict]:
    """Fetch available ElevenLabs voices from the v2 API, paginating until exhausted."""
    import httpx

    key = _api_key()
    headers = {"xi-api-key": key}
    url = "https://api.elevenlabs.io/v2/voices"
    params: dict = {"category": category}
    voices: list[dict] = []

    while True:
        resp = httpx.get(url, headers=headers, params=params, timeout=30.0)
        if resp.status_code != 200:
            raise RuntimeError(
                f"ElevenLabs voices request failed ({resp.status_code}): {resp.text[:500]}"
            )
        data = resp.json()
        for v in data.get("voices", []):
            labels = v.get("labels") or {}
            voices.append({
                "voice_id": v.get("voice_id", ""),
                "name": v.get("name", ""),
                "description": v.get("description") or "",
                "labels": labels,
            })
        if not data.get("has_more"):
            break
        params["next_page_token"] = data["next_page_token"]

    return voices


def generate_tts(
    text: str,
    *,
    voice: str = _DEFAULT_VOICE_ID,
    out_dir: Path,
    model: str = _DEFAULT_MODEL,
) -> tuple[Path, list[dict[str, Any]], float]:
    """Synthesize text via ElevenLabs. Returns (audio_path, words, duration_s)."""
    import httpx

    key = _api_key()
    voice_id = _resolve_voice_id(voice)

    url = f"{_API_BASE}/text-to-speech/{voice_id}/with-timestamps"
    headers = {
        "xi-api-key": key,
        "Content-Type": "application/json",
    }
    body = {
        "text": text,
        "model_id": model,
        "output_format": "mp3_44100_128",
    }

    resp = httpx.post(url, headers=headers, json=body, timeout=120.0)
    if resp.status_code != 200:
        raise RuntimeError(
            f"ElevenLabs TTS request failed ({resp.status_code}) "
            f"voice_id={voice_id!r} model={model!r}: "
            f"{resp.text[:500]}"
        )

    data = resp.json()
    audio_bytes = base64.b64decode(data["audio_base64"])

    alignment = data.get("alignment") or {}
    chars = alignment.get("characters", [])
    char_starts = alignment.get("character_start_times_seconds", [])
    char_ends = alignment.get("character_end_times_seconds", [])

    words = _chars_to_words(chars, char_starts, char_ends)
    duration_s = words[-1]["end"] if words else len(audio_bytes) / (44100 * 2)

    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / f"elevenlabs_{voice_id[:8]}_{int(time.time()*1000)}.mp3"
    audio_path.write_bytes(audio_bytes)

    return audio_path, words, duration_s
