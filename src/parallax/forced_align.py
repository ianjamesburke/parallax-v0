"""WhisperX forced alignment — produces real per-word timestamps for a wav.

Gemini Flash TTS does not return word-level timing (only raw PCM), and
ad-style scripts often pack 30+ words into 12–15 seconds where evenly
distributed dummy timings drift visibly out of sync. WhisperX runs ASR
(whisper) + wav2vec2 forced alignment to produce ~50 ms-precise word
boundaries — captions and scene transitions then land on the actual
spoken word, not a metronome.

Output is the canonical `vo_words.json` shape used everywhere else:
`[{"word": str, "start": float, "end": float}, ...]` (and downstream
callers may also wrap it in `{"words": [...], "total_duration_s": float}`).

First run downloads the whisper + wav2vec2 models (~1 GB total) into
the HuggingFace cache. CPU inference on a 13 s clip takes ~5–10 s on
Apple Silicon — fine for the captions stage, which runs once per
produce. Errors propagate; the caller decides whether to fall back to
even-distribution timings.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("parallax.forced_align")

_DEFAULT_MODEL = "base.en"  # tighter word-onset detection than tiny.en; ~150MB
_DEFAULT_DEVICE = "cpu"     # macOS Metal/MPS not yet supported by whisperx; CPU is fine
_DEFAULT_COMPUTE = "int8"   # fast + memory-light on CPU


def align_words(wav_path: str | Path) -> list[dict]:
    """Transcribe + align a wav, return `[{word, start, end}, ...]`.

    Uses WhisperX: whisper for transcription, then wav2vec2 forced
    alignment for tight word boundaries. Word strings come from whisper
    (so punctuation may differ slightly from the source script — timing
    is what callers consume).

    Override the model with `PARALLAX_WHISPER_MODEL` (e.g. `base.en`,
    `small.en`) and the device with `PARALLAX_WHISPER_DEVICE` (`cpu`,
    `cuda`). Compute type via `PARALLAX_WHISPER_COMPUTE`.
    """
    import whisperx

    wav = str(Path(wav_path).expanduser().resolve())
    if not Path(wav).is_file():
        raise FileNotFoundError(f"forced_align: wav not found: {wav}")

    model_name = os.environ.get("PARALLAX_WHISPER_MODEL", _DEFAULT_MODEL)
    device = os.environ.get("PARALLAX_WHISPER_DEVICE", _DEFAULT_DEVICE)
    compute_type = os.environ.get("PARALLAX_WHISPER_COMPUTE", _DEFAULT_COMPUTE)

    log.info("forced_align: transcribing %s with whisperx (%s, %s)", Path(wav).name, model_name, device)
    model = whisperx.load_model(model_name, device=device, compute_type=compute_type)
    audio = whisperx.load_audio(wav)
    result = model.transcribe(audio, batch_size=8)

    language = result.get("language", "en")
    log.info("forced_align: whisper detected language=%s, %d segments", language, len(result.get("segments", [])))

    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    aligned = whisperx.align(
        result["segments"],
        align_model,
        metadata,
        audio,
        device=device,
        return_char_alignments=False,
    )

    words: list[dict] = []
    for w in aligned.get("word_segments", []):
        # WhisperX leaves `start`/`end` missing for words it couldn't pin
        # (very short tokens, hesitations). Skip those — they'd otherwise
        # poison downstream scene alignment.
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
