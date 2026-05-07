"""parallax.whisper_backend — shared WhisperX/faster-whisper backend selection.

Single source of truth for:
- WhisperX availability check
- PARALLAX_WHISPER_MODEL / PARALLAX_WHISPER_DEVICE / PARALLAX_WHISPER_COMPUTE config
- Transcribing a wav to word-level timestamps via either backend

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
    _HAS_WHISPERX = True
except ImportError:
    _whisperx = None  # type: ignore[assignment]
    _HAS_WHISPERX = False


def get_config() -> tuple[str, str, str]:
    """Return (model_name, device, compute_type) from env with defaults."""
    return (
        os.environ.get("PARALLAX_WHISPER_MODEL", _DEFAULT_MODEL),
        os.environ.get("PARALLAX_WHISPER_DEVICE", _DEFAULT_DEVICE),
        os.environ.get("PARALLAX_WHISPER_COMPUTE", _DEFAULT_COMPUTE),
    )


def transcribe_wav(wav_path: str, label: str = "", no_whisperx: bool = False) -> list[dict]:
    """Transcribe a wav file to word-level timestamps.

    Prefers WhisperX (whisper + wav2vec2 forced alignment) when installed.
    Falls back to faster-whisper (less precise timestamps) when WhisperX is
    not installed, or when no_whisperx=True is passed.

    Returns [{"word": str, "start": float, "end": float}, ...].
    Raises RuntimeError if 0 words are produced.
    """
    model_name, device, compute_type = get_config()
    display = label or Path(wav_path).name

    if _HAS_WHISPERX and not no_whisperx:
        return _transcribe_whisperx(wav_path, display, model_name, device, compute_type)
    if not _HAS_WHISPERX:
        log.warning(
            "whisper_backend: WhisperX not installed — falling back to faster-whisper "
            "(timestamps will be less precise). "
            "For better precision: uv tool install 'parallax[whisperx]'"
        )
    else:
        log.info("whisper_backend: --no-whisperx set — using faster-whisper")
    return _transcribe_faster_whisper(wav_path, display, model_name, device, compute_type)


def _transcribe_whisperx(
    wav_path: str, label: str, model_name: str, device: str, compute_type: str
) -> list[dict]:
    assert _whisperx is not None, "whisperx not installed"
    log.info("whisper_backend: transcribing %s with whisperx (%s, %s)", label, model_name, device)
    model = _whisperx.load_model(model_name, device=device, compute_type=compute_type)
    audio = _whisperx.load_audio(wav_path)
    result = model.transcribe(audio, batch_size=8)

    language = result.get("language", "en")
    log.info("whisper_backend: detected language=%s, %d segments", language, len(result.get("segments", [])))

    align_model, metadata = _whisperx.load_align_model(language_code=language, device=device)
    aligned = _whisperx.align(
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


def _transcribe_faster_whisper(
    wav_path: str, label: str, model_name: str, device: str, compute_type: str
) -> list[dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "whisper_backend: faster-whisper is not installed. "
            "Run: uv pip install faster-whisper"
        ) from e

    log.info("whisper_backend: transcribing %s with faster-whisper (%s, %s)", label, model_name, device)
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(wav_path, word_timestamps=True)

    log.info("whisper_backend: detected language=%s", info.language)
    words: list[dict] = []
    for segment in segments:
        for w in (segment.words or []):
            if w.start is None or w.end is None:
                continue
            words.append({
                "word": w.word.strip(),
                "start": round(w.start, 3),
                "end": round(w.end, 3),
            })
    return words
