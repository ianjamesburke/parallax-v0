"""WhisperX forced alignment — produces real per-word timestamps for a wav.

Prefers WhisperX (whisper + wav2vec2 forced alignment, ~50 ms precision) when
installed. Falls back to faster-whisper's native word timestamps when WhisperX
is not available (slightly less precise on short tokens, fine for captions).

Install WhisperX for best results:
    pip install whisperx

Output is the canonical `vo_words.json` shape used everywhere else:
`[{"word": str, "start": float, "end": float}, ...]`
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("parallax.forced_align")

_DEFAULT_MODEL = "base.en"  # tighter word-onset detection than tiny.en; ~150MB
_DEFAULT_DEVICE = "cpu"     # macOS Metal/MPS not yet supported by whisperx; CPU is fine
_DEFAULT_COMPUTE = "int8"   # fast + memory-light on CPU

try:
    import whisperx as _whisperx
    _HAS_WHISPERX = True
except ImportError:
    _whisperx = None  # type: ignore[assignment]
    _HAS_WHISPERX = False


def align_words(wav_path: str | Path) -> list[dict]:
    """Transcribe + align a wav, return `[{word, start, end}, ...]`.

    Uses WhisperX when installed (whisper + wav2vec2 forced alignment).
    Falls back to faster-whisper native word timestamps otherwise.

    Override the model with PARALLAX_WHISPER_MODEL (e.g. base.en, small.en),
    device with PARALLAX_WHISPER_DEVICE (cpu, cuda), compute type with
    PARALLAX_WHISPER_COMPUTE.
    """
    wav = str(Path(wav_path).expanduser().resolve())
    if not Path(wav).is_file():
        raise FileNotFoundError(f"forced_align: wav not found: {wav}")

    model_name = os.environ.get("PARALLAX_WHISPER_MODEL", _DEFAULT_MODEL)
    device = os.environ.get("PARALLAX_WHISPER_DEVICE", _DEFAULT_DEVICE)
    compute_type = os.environ.get("PARALLAX_WHISPER_COMPUTE", _DEFAULT_COMPUTE)

    if _HAS_WHISPERX:
        return _align_whisperx(wav, model_name, device, compute_type)
    else:
        log.warning(
            "forced_align: whisperx not installed — using faster-whisper "
            "(install whisperx for better word-boundary precision)"
        )
        return _align_faster_whisper(wav, model_name, device, compute_type)


def _align_whisperx(wav: str, model_name: str, device: str, compute_type: str) -> list[dict]:
    log.info("forced_align: transcribing %s with whisperx (%s, %s)", Path(wav).name, model_name, device)
    model = _whisperx.load_model(model_name, device=device, compute_type=compute_type)
    audio = _whisperx.load_audio(wav)
    result = model.transcribe(audio, batch_size=8)

    language = result.get("language", "en")
    log.info("forced_align: detected language=%s, %d segments", language, len(result.get("segments", [])))

    align_model, metadata = _whisperx.load_align_model(language_code=language, device=device)
    aligned = _whisperx.align(
        result["segments"],
        align_model,
        metadata,
        audio,
        device=device,
        return_char_alignments=False,
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

    if not words:
        raise RuntimeError(f"forced_align: produced 0 words for {wav}")
    log.info("forced_align: %d words, span %.2f–%.2fs", len(words), words[0]["start"], words[-1]["end"])
    return words


def _align_faster_whisper(wav: str, model_name: str, device: str, compute_type: str) -> list[dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "forced_align: faster-whisper is not installed. "
            "Run: uv pip install faster-whisper"
        ) from e

    log.info("forced_align: transcribing %s with faster-whisper (%s, %s)", Path(wav).name, model_name, device)
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(wav, word_timestamps=True)

    log.info("forced_align: detected language=%s", info.language)
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

    if not words:
        raise RuntimeError(f"forced_align: produced 0 words for {wav}")
    log.info("forced_align: %d words, span %.2f–%.2fs", len(words), words[0]["start"], words[-1]["end"])
    return words
