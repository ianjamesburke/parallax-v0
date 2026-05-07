"""TTS generation via OpenRouter (gpt-audio-mini and Gemini TTS backends)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .client import (
    _check_key,
    _post,
    _raise_for_credits_or_status,
    _stream_post,
)

_TTS_DEFAULT_VOICE = "nova"
_TTS_DEFAULT_MODEL = "openai/gpt-audio-mini"
_TTS_PCM_SAMPLE_RATE = 24_000
_TTS_PCM_BYTES_PER_SAMPLE = 2  # 16-bit

_GEMINI_TTS_DEFAULT_VOICE = "Kore"

# Matches [tag] tokens anywhere in text — used to strip inline emotional
# tags before sending to backends that don't interpret them (chat_audio).
_EMOTIONAL_TAG_RE = re.compile(r"\[[^\]]+\]")


def strip_emotional_tags(text: str) -> str:
    """Remove inline [emotional] tags and normalize whitespace.

    Used for chat_audio backends (e.g. gpt-audio-mini) that would pronounce
    brackets literally. Gemini TTS (speech backend) receives tags unchanged.
    """
    return re.sub(r" {2,}", " ", _EMOTIONAL_TAG_RE.sub("", text)).strip()


# Style presets prepend a directive to the spoken text. gpt-audio-mini
# follows freeform delivery hints in the user message.
_TTS_STYLE_PRESETS: dict[str, str] = {
    "rapid_fire": (
        "Read this as a rapid-fire commercial — talk fast, no pauses, urgent, "
        "energetic. Speak quickly: "
    ),
    "fast": "Say this quickly, with high energy, like a fast-paced TikTok ad: ",
    "calm": "Read this in a calm, measured, conversational tone: ",
    "natural": "",  # baseline — no directive
}
_TTS_DEFAULT_STYLE = "rapid_fire"


def _tts_resolve_directive(*, style: str | None, style_hint: str | None) -> str:
    if style_hint:
        return style_hint if style_hint.endswith(": ") else style_hint.rstrip() + " "
    if style is None:
        return ""
    if style not in _TTS_STYLE_PRESETS:
        raise ValueError(
            f"Unknown TTS style {style!r}. Available presets: "
            f"{sorted(_TTS_STYLE_PRESETS)}, or pass `style_hint` for a freeform directive."
        )
    return _TTS_STYLE_PRESETS[style]


def _tts_evenly_distributed_words(text: str, duration_s: float) -> list[dict[str, Any]]:
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


def _tts_real(
    text: str,
    *,
    voice: str = _TTS_DEFAULT_VOICE,
    out_dir: Path,
    model: str = _TTS_DEFAULT_MODEL,
    style: str | None = None,
    style_hint: str | None = None,
) -> tuple[Path, list[dict[str, Any]], float]:
    """Synthesize via gpt-audio-mini through OpenRouter. Returns (wav, words, duration)."""
    import base64
    import json as _json
    import time as _time
    import wave

    out_dir.mkdir(parents=True, exist_ok=True)
    _check_key()

    directive = _tts_resolve_directive(style=style, style_hint=style_hint)
    spoken = directive + text if directive else text
    # Anchor the user content so the model says the script literally
    # rather than treating it as conversational input.
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
    with _stream_post("/chat/completions", body, timeout=300.0) as response:
        if response.status_code != 200:
            raise RuntimeError(
                f"OpenRouter TTS request failed ({response.status_code}) for "
                f"model={model!r} voice={voice!r}: "
                f"{response.read().decode('utf-8', 'replace')[:500]}"
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

    safe_voice = "".join(c for c in voice if c.isalnum() or c in ("-", "_")).strip() or "voice"
    wav_path = out_dir / f"openrouter_tts_{safe_voice}_{int(_time.time()*1000)}.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(_TTS_PCM_BYTES_PER_SAMPLE)
        w.setframerate(_TTS_PCM_SAMPLE_RATE)
        w.writeframes(bytes(pcm_bytes))

    duration_s = len(pcm_bytes) / (_TTS_PCM_SAMPLE_RATE * _TTS_PCM_BYTES_PER_SAMPLE)

    # Forced alignment via WhisperX gives ~50ms-precise word boundaries from
    # the produced wav. Falls back to evenly-distributed timings only on
    # alignment failure (loudly logged so it's not silent).
    try:
        from .. import forced_align
        words = forced_align.align_words(wav_path)
    except Exception as exc:
        print(
            f"[WARNING] forced_align failed ({exc}); word timestamps are evenly distributed "
            f"(not real speech timing) — captions may be unsynchronized. "
            f"Run `parallax audio transcribe {wav_path} --output <words_path>` after produce "
            f"to get real word timestamps."
        )
        words = _tts_evenly_distributed_words(transcript or text, duration_s)

    return wav_path, words, duration_s


def _tts_real_speech(
    text: str,
    *,
    voice: str,
    out_dir: Path,
    model: str,
) -> tuple[Path, list[dict[str, Any]], float]:
    """Synthesize via /api/v1/audio/speech (Gemini TTS on OpenRouter).

    Inline [emotional] tags are passed through unchanged — the Gemini model
    interprets them natively for expressive delivery. Use single-word
    gerund/adjective/adverb form: [dramatically], [whispering], [rapidly],
    [excitedly], [softly].

    OpenRouter's /audio/speech only accepts response_format "mp3" or "pcm"
    (verified live 2026-04-30 — "wav" returns ZodError 400). We request "pcm"
    and wrap the raw bytes into a WAV ourselves, consistent with _tts_real.

    Returns (wav_path, words, duration_s). Word timings come from forced
    alignment (WhisperX), falling back to evenly-distributed if alignment
    fails.
    """
    import time as _time
    import wave

    out_dir.mkdir(parents=True, exist_ok=True)
    _check_key()

    body = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": "pcm",
    }

    resp = _post("/audio/speech", body, timeout=120.0)
    _raise_for_credits_or_status(resp)

    if not resp.content:
        raise RuntimeError(
            f"OpenRouter Gemini TTS returned empty audio for {text[:60]!r}"
        )

    safe_voice = "".join(c for c in voice if c.isalnum() or c in ("-", "_")).strip() or "voice"
    wav_path = out_dir / f"openrouter_tts_{safe_voice}_{int(_time.time()*1000)}.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(_TTS_PCM_BYTES_PER_SAMPLE)
        w.setframerate(_TTS_PCM_SAMPLE_RATE)
        w.writeframes(resp.content)

    duration_s = len(resp.content) / (_TTS_PCM_SAMPLE_RATE * _TTS_PCM_BYTES_PER_SAMPLE)

    # Forced alignment for word timestamps, same as chat_audio backend.
    try:
        from .. import forced_align
        words = forced_align.align_words(wav_path)
    except Exception as exc:
        print(
            f"[WARNING] forced_align failed ({exc}); word timestamps are evenly distributed "
            f"(not real speech timing) — captions may be unsynchronized. "
            f"Run `parallax audio transcribe {wav_path} --output <words_path>` after produce "
            f"to get real word timestamps."
        )
        words = _tts_evenly_distributed_words(
            strip_emotional_tags(text),  # strip tags for word-count accuracy
            duration_s,
        )

    return wav_path, words, duration_s
