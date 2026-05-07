"""parallax.whisper_backend — WhisperX backend for word-level timestamps.

Single source of truth for:
- PARALLAX_WHISPER_MODEL / PARALLAX_WHISPER_DEVICE / PARALLAX_WHISPER_COMPUTE config
- Transcribing a wav to word-level timestamps via WhisperX

Output shape: [{"word": str, "start": float, "end": float}, ...]
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("parallax.whisper_backend")

_DEFAULT_MODEL = "base.en"
_DEFAULT_DEVICE = "cpu"
_DEFAULT_COMPUTE = "int8"

try:
    import whisperx as _whisperx  # type: ignore[import-untyped]
except ImportError:
    _whisperx = None  # type: ignore[assignment]


def _require_whisperx() -> None:
    if _whisperx is None:
        raise ImportError(
            "whisperx is required but not installed. "
            "Install with: uv tool install 'parallax[whisperx]'"
        )


def get_config() -> tuple[str, str, str]:
    """Return (model_name, device, compute_type) from env with defaults."""
    return (
        os.environ.get("PARALLAX_WHISPER_MODEL", _DEFAULT_MODEL),
        os.environ.get("PARALLAX_WHISPER_DEVICE", _DEFAULT_DEVICE),
        os.environ.get("PARALLAX_WHISPER_COMPUTE", _DEFAULT_COMPUTE),
    )


def transcribe_wav(wav_path: str, label: str = "") -> list[dict]:
    """Transcribe a wav file to word-level timestamps using WhisperX.

    Returns [{"word": str, "start": float, "end": float}, ...].
    Raises RuntimeError if 0 words are produced.
    """
    _require_whisperx()
    model_name, device, compute_type = get_config()
    display = label or Path(wav_path).name
    log.info("whisper_backend: transcribing %s with whisperx (%s, %s)", display, model_name, device)
    model = _whisperx.load_model(model_name, device=device, compute_type=compute_type)  # type: ignore[union-attr]
    audio = _whisperx.load_audio(wav_path)  # type: ignore[union-attr]
    result = model.transcribe(audio, batch_size=8)

    language = result.get("language", "en")
    log.info("whisper_backend: detected language=%s, %d segments", language, len(result.get("segments", [])))

    align_model, metadata = _whisperx.load_align_model(language_code=language, device=device)  # type: ignore[union-attr]
    aligned = _whisperx.align(  # type: ignore[union-attr]
        result["segments"], align_model, metadata, audio, device=device, return_char_alignments=False,
    )

    words: list[dict] = []
    for w in aligned.get("word_segments", []):
        if w.get("start") is None or w.get("end") is None:
            continue
        words.append({
            "word": str(w["word"]).strip(),
            "start": round(float(w["start"]), 3),
            "end": round(float(w["end"]), 3),
        })
    return words
