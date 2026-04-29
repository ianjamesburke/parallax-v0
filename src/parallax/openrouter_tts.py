"""OpenAI gpt-audio-mini TTS via OpenRouter (chat-completions audio modality).

OpenRouter exposes OpenAI's gpt-audio-mini through `/api/v1/chat/completions`
with `modalities=["text", "audio"]` and `audio={"voice": ..., "format":
"pcm16"}`. Audio output is delivered in SSE chunks; we accumulate the
base64-encoded pcm16 frames, write a 24kHz mono WAV, and run forced
alignment on it for word timestamps.

Drop-in replacement for the previous `gemini_tts.synthesize` — same return
contract `(audio_path, words, duration_s)` so callers stay agnostic.
"""

from __future__ import annotations

import base64
import json as _json
import os
import time as _time
import wave
from pathlib import Path

import httpx

DEFAULT_VOICE = "nova"
DEFAULT_MODEL = "openai/gpt-audio-mini"
PCM_SAMPLE_RATE = 24_000
PCM_BYTES_PER_SAMPLE = 2  # 16-bit

_OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_OPENROUTER_HEADERS_EXTRA = {
    "HTTP-Referer": "https://github.com/ianjamesburke/parallax-v0",
    "X-Title": "parallax",
}


# Style presets prepend a directive to the spoken text. gpt-audio-mini
# follows freeform delivery hints in the user message.
STYLE_PRESETS: dict[str, str] = {
    "rapid_fire": (
        "Read this as a rapid-fire commercial — talk fast, no pauses, urgent, "
        "energetic. Speak quickly: "
    ),
    "fast": "Say this quickly, with high energy, like a fast-paced TikTok ad: ",
    "calm": "Read this in a calm, measured, conversational tone: ",
    "natural": "",  # baseline — no directive
}
DEFAULT_STYLE = "rapid_fire"


def synthesize(
    text: str,
    *,
    voice: str = DEFAULT_VOICE,
    out_dir: Path,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    style: str | None = None,
    style_hint: str | None = None,
) -> tuple[Path, list[dict], float]:
    """Synthesize via gpt-audio-mini through OpenRouter. Returns (wav, words, duration)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is required for TTS. Set it, or "
            "PARALLAX_TEST_MODE=1 for stubs."
        )

    directive = _resolve_directive(style=style, style_hint=style_hint)
    spoken = directive + text if directive else text
    # The model treats user content as conversation, so anchor it with an
    # explicit "say exactly" framing — without this it sometimes paraphrases
    # short prompts.
    user_message = f"Say this exactly, with no preamble or commentary: {spoken}"

    body = {
        "model": model,
        "messages": [{"role": "user", "content": user_message}],
        "modalities": ["text", "audio"],
        "audio": {"voice": voice, "format": "pcm16"},
        "stream": True,
    }

    pcm_bytes = bytearray()
    transcript = ""
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        **_OPENROUTER_HEADERS_EXTRA,
    }
    with httpx.stream(
        "POST", _OPENROUTER_ENDPOINT, headers=headers, json=body, timeout=300.0
    ) as response:
        if response.status_code != 200:
            raise RuntimeError(
                f"OpenRouter TTS request failed ({response.status_code}) for "
                f"model={model!r} voice={voice!r}: {response.read().decode('utf-8', 'replace')[:500]}"
            )
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            try:
                event = _json.loads(payload)
            except _json.JSONDecodeError:
                continue
            choices = event.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            audio = delta.get("audio")
            if not isinstance(audio, dict):
                continue
            data_b64 = audio.get("data")
            if data_b64:
                pcm_bytes.extend(base64.b64decode(data_b64))
            tx = audio.get("transcript")
            if tx:
                transcript += tx

    if not pcm_bytes:
        raise RuntimeError(
            f"OpenRouter TTS stream produced no audio for {text[:60]!r}"
        )

    wav_path = out_dir / f"openrouter_tts_{voice}_{int(_time.time()*1000)}.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(PCM_BYTES_PER_SAMPLE)
        w.setframerate(PCM_SAMPLE_RATE)
        w.writeframes(bytes(pcm_bytes))

    duration_s = len(pcm_bytes) / (PCM_SAMPLE_RATE * PCM_BYTES_PER_SAMPLE)

    # Forced alignment via WhisperX gives ~50ms-precise word boundaries from the
    # produced wav. Falls back to evenly-distributed timings only on alignment
    # failure (so captions still appear, but with a loud warning).
    try:
        from . import forced_align
        words = forced_align.align_words(wav_path)
    except Exception as exc:
        import logging
        logging.getLogger("parallax.openrouter_tts").warning(
            "forced_align failed (%s); falling back to evenly-distributed word timings",
            exc,
        )
        # Use the spoken text (or the model's transcript if it differs) for
        # the even-distribution fallback.
        words = _evenly_distributed_words(transcript or text, duration_s)

    return wav_path, words, duration_s


def _resolve_directive(*, style: str | None, style_hint: str | None) -> str:
    if style_hint:
        return style_hint if style_hint.endswith(": ") else style_hint.rstrip() + " "
    if style is None:
        return ""
    if style not in STYLE_PRESETS:
        raise ValueError(
            f"Unknown TTS style {style!r}. Available presets: "
            f"{sorted(STYLE_PRESETS)}, or pass `style_hint` for a freeform directive."
        )
    return STYLE_PRESETS[style]


def _evenly_distributed_words(text: str, duration_s: float) -> list[dict]:
    tokens = text.split()
    if not tokens or duration_s <= 0:
        return []
    per_word = duration_s / len(tokens)
    return [
        {
            "word": t,
            "start": round(i * per_word, 3),
            "end": round((i + 1) * per_word, 3),
        }
        for i, t in enumerate(tokens)
    ]
