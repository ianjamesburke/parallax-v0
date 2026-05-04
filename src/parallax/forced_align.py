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
from pathlib import Path

from . import whisper_backend

log = logging.getLogger("parallax.forced_align")


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

    words = whisper_backend.transcribe_wav(wav, label=Path(wav).name)

    if not words:
        raise RuntimeError(f"forced_align: produced 0 words for {wav}")
    log.info("forced_align: %d words, span %.2f–%.2fs", len(words), words[0]["start"], words[-1]["end"])
    return words
