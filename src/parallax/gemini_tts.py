"""Gemini Flash TTS via OpenRouter — primary voiceover synthesizer.

Routes Google's Gemini 2.5 Flash Preview TTS through OpenRouter's
OpenAI-compatible audio endpoint at `/api/v1/audio/speech`. Returns the
same `(audio_path, words, total_duration_s)` contract the rest of the
pipeline relies on.

Per-word alignment runs through `forced_align.align_words()` on the
saved mp3. If forced alignment fails (whisper model unavailable,
unsupported decode), we fall back to evenly distributed timings and log
a warning — captions still render, but they'll look metronomic.

Audio format: mp3 (OpenRouter returns the raw audio bytes for the
requested `response_format`).

Voices: Gemini exposes ~30 prebuilt voices (Kore, Puck, Zephyr, Charon,
Fenrir, Leda, Orus, Aoede, Callirrhoe, Autonoe, Enceladus, Iapetus,
Umbriel, Algieba, Despina, Erinome, Algenib, Rasalgethi, Laomedeia,
Achernar, Alnilam, Schedar, Gacrux, Pulcherrima, Achird, Zubenelgenubi,
Vindemiatrix, Sadachbia, Sadaltager, Sulafat). Default is "Kore".
"""

from __future__ import annotations

import os
import subprocess
import time as _time
from pathlib import Path

DEFAULT_VOICE = "Kore"
DEFAULT_MODEL = "google/gemini-2.5-flash-preview-tts"
_OPENROUTER_TTS_ENDPOINT = "https://openrouter.ai/api/v1/audio/speech"
_OPENROUTER_HEADERS_EXTRA = {
    "HTTP-Referer": "https://github.com/ianjamesburke/parallax-v0",
    "X-Title": "parallax",
}


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
    """Synthesize speech via Gemini Flash TTS through OpenRouter.

    Returns (mp3_path, words, duration). `style` is a preset name from
    `STYLE_PRESETS` (e.g. 'rapid_fire'); it prepends a directive line so
    Gemini delivers the text in the requested cadence/energy. Pass
    `style_hint` for a freeform custom directive — `style_hint` wins over
    `style` if both are supplied.
    """
    import httpx

    out_dir.mkdir(parents=True, exist_ok=True)
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "Gemini TTS requires OPENROUTER_API_KEY for real-mode runs. "
            "Set it, or export PARALLAX_TEST_MODE=1 to use stubs."
        )

    directive = _resolve_directive(style=style, style_hint=style_hint)
    spoken = directive + text if directive else text

    body = {
        "model": model,
        "voice": voice,
        "input": spoken,
        "response_format": "mp3",
    }
    resp = httpx.post(
        _OPENROUTER_TTS_ENDPOINT,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            **_OPENROUTER_HEADERS_EXTRA,
        },
        json=body,
        timeout=120.0,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"OpenRouter TTS request failed ({resp.status_code}) for "
            f"model={model!r} voice={voice!r}: {resp.text[:300]}"
        )
    audio_bytes = resp.content
    if not audio_bytes:
        raise RuntimeError(
            f"OpenRouter TTS returned empty body for model={model!r} voice={voice!r}"
        )

    mp3_path = out_dir / f"openrouter_tts_{voice}_{int(_time.time()*1000)}.mp3"
    mp3_path.write_bytes(audio_bytes)

    duration_s = _probe_duration(mp3_path)
    if duration_s <= 0:
        raise RuntimeError(
            f"OpenRouter TTS produced an unprobeable file at {mp3_path}; "
            f"response was {len(audio_bytes)} bytes"
        )

    # Forced alignment via WhisperX gives ~50ms-precise word boundaries from the
    # produced audio. Even-distribution timings drift visibly out of sync on ad
    # copy where pacing is uneven. We fall back to even distribution only if
    # WhisperX itself fails (model download issue, unsupported audio, etc.) so
    # captions still appear — but log the failure loudly because the result
    # will look like the old metronome behaviour.
    try:
        from . import forced_align
        words = forced_align.align_words(mp3_path)
    except Exception as exc:
        import logging
        logging.getLogger("parallax.gemini_tts").warning(
            "forced_align failed (%s); falling back to evenly-distributed word timings",
            exc,
        )
        words = _evenly_distributed_words(text, duration_s)

    return mp3_path, words, duration_s


def _probe_duration(path: Path) -> float:
    """ffprobe → seconds. Returns 0.0 on any failure (caller handles)."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=False,
        )
        out = result.stdout.strip()
        return float(out) if out else 0.0
    except Exception:
        return 0.0


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

    Used as a fallback when forced alignment fails. For per-word accuracy,
    forced_align should be the path that succeeds.
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
