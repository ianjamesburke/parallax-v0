"""Google Gemini Flash Preview TTS — primary voiceover synthesizer.

OpenRouter does not currently host Gemini TTS (verified live 2026-04-28
against `/api/v1/models` and the audio-output filter — only Lyria music
and OpenAI gpt-audio surfaces). For TTS we call Google's Gemini API
directly via the `google-genai` SDK.

Returns the same `(audio_path, words, total_duration_s)` contract as
`elevenlabs.synthesize` so callers stay agnostic. Per-word timestamps
are evenly distributed across total duration — Gemini's
`generate_content(response_modalities=["AUDIO"])` does NOT return word
alignment, only raw PCM. For tighter caption sync, layer a forced
aligner (e.g. whisper) on top of the produced wav, or use
`voice='eleven:<id>'` which provides native alignment.

Audio format: 24 kHz mono 16-bit PCM, wrapped in a WAV header.

Voices: Gemini exposes ~30 prebuilt voices (Kore, Puck, Zephyr, Charon,
Fenrir, Leda, Orus, Aoede, Callirrhoe, Autonoe, Enceladus, Iapetus,
Umbriel, Algieba, Despina, Erinome, Algenib, Rasalgethi, Laomedeia,
Achernar, Alnilam, Schedar, Gacrux, Pulcherrima, Achird, Zubenelgenubi,
Vindemiatrix, Sadachbia, Sadaltager, Sulafat). Default is "Kore".
"""

from __future__ import annotations

import os
import time as _time
import wave
from pathlib import Path

DEFAULT_VOICE = "Kore"
DEFAULT_MODEL = "gemini-2.5-flash-preview-tts"
PCM_SAMPLE_RATE = 24_000
PCM_BYTES_PER_SAMPLE = 2  # 16-bit


# Style presets that prepend a directive line to the spoken text. Verified
# 2026-04-28: `rapid_fire` shipped 36% faster than the bare prompt for the
# same script (8.5 s vs 13.1 s). Gemini Flash TTS does NOT expose a numeric
# speed/rate parameter — speech control is prompt-based only.
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
    """Synthesize speech via Gemini Flash TTS. Returns (wav_path, words, duration).

    `style` is a preset name from `STYLE_PRESETS` (e.g. 'rapid_fire'). It
    prepends a directive line to the prompt so Gemini delivers the text
    in the requested cadence/energy. Pass `style_hint` for a freeform
    custom directive instead. The two are mutually exclusive — `style_hint`
    wins if both are supplied.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    key = api_key or os.environ.get("AI_VIDEO_GEMINI_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "Gemini TTS requires AI_VIDEO_GEMINI_KEY or GEMINI_API_KEY. "
            "Set it, or pass voice='eleven:<voice_id>' to use ElevenLabs instead."
        )

    directive = _resolve_directive(style=style, style_hint=style_hint)
    spoken = directive + text if directive else text

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)
    response = client.models.generate_content(
        model=model,
        contents=spoken,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice),
                ),
            ),
        ),
    )

    candidates = response.candidates or []
    if not candidates:
        raise RuntimeError(f"Gemini TTS returned no candidates for {text[:60]!r}")
    content = candidates[0].content
    parts = (content.parts if content else None) or []
    pcm_bytes: bytes | None = None
    for p in parts:
        if p.inline_data and p.inline_data.data:
            pcm_bytes = p.inline_data.data
            break
    if not pcm_bytes:
        raise RuntimeError(f"Gemini TTS response had no inline audio data for {text[:60]!r}")

    wav_path = out_dir / f"gemini_tts_{voice}_{int(_time.time()*1000)}.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(PCM_BYTES_PER_SAMPLE)
        w.setframerate(PCM_SAMPLE_RATE)
        w.writeframes(pcm_bytes)

    duration_s = len(pcm_bytes) / (PCM_SAMPLE_RATE * PCM_BYTES_PER_SAMPLE)

    # Forced alignment via WhisperX gives ~50ms-precise word boundaries from the
    # produced wav. Even-distribution timings drift visibly out of sync on ad
    # copy where pacing is uneven. We fall back to even distribution only if
    # WhisperX itself fails (model download issue, unsupported audio, etc.) so
    # captions still appear — but log the failure loudly because the result
    # will look like the old metronome behaviour.
    try:
        from . import forced_align
        words = forced_align.align_words(wav_path)
    except Exception as exc:
        import logging
        logging.getLogger("parallax.gemini_tts").warning(
            "forced_align failed (%s); falling back to evenly-distributed word timings",
            exc,
        )
        words = _evenly_distributed_words(text, duration_s)

    return wav_path, words, duration_s


def _resolve_directive(*, style: str | None, style_hint: str | None) -> str:
    """Pick the directive prefix. `style_hint` (freeform) wins over `style` (preset).

    Unknown style names raise loudly — silently falling back to natural would
    mask plan YAML typos that the user expects to change pacing.
    """
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
    """Split `text` on whitespace and assign each word an even slice of duration.

    Gemini does not expose word-level timestamps. This is a crude approximation
    suitable for caption sync at sentence granularity. For per-word accuracy,
    run forced alignment (whisper) on the produced wav.
    """
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
