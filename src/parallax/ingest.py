"""parallax.ingest — index a clip or directory of clips.

Probes duration via ffprobe, runs WhisperX transcription per clip, and emits a
single aggregated `index.json` with per-clip word-level transcripts. A future
agent / planner reads this index to pick clips by spoken content.

Public API: `ingest(target, *, out_path=None, visual=False, estimate=False,
parallel=4) -> IngestResult`.

The visual hook (Gemini Vision frame tagging) is stubbed for Phase 1.6 — pass
`visual=True` and you get NotImplementedError. CLI wiring lives elsewhere.
"""
from __future__ import annotations

import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .audio import transcribe_words
from .log import get_logger

log = get_logger("ingest")

# Audio + video extensions we treat as ingestable clips.
CLIP_EXTS: frozenset[str] = frozenset({
    ".mp4", ".mov", ".mkv", ".m4v", ".avi",
    ".mp3", ".m4a", ".wav", ".aac",
})

INDEX_VERSION = 1


@dataclass
class ClipIndex:
    """Per-clip record in the aggregated index."""
    path: str                                      # absolute path
    duration_s: float
    words: list[dict] = field(default_factory=list)  # [{word, start, end}, ...]
    visual_tags: list[dict] | None = None            # always None in Phase 1.6


@dataclass
class IngestResult:
    """Result of an ingest run."""
    index_path: Path | None       # where index.json was written; None if --estimate
    clips: list[ClipIndex]
    total_duration_s: float
    estimated_cost_usd: float     # 0.0 for now (WhisperX is local; visual is stubbed)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def ingest(
    target: str | Path,
    *,
    out_path: str | Path | None = None,
    visual: bool = False,
    estimate: bool = False,
    parallel: int = 4,
) -> IngestResult:
    """Index a clip or directory of clips.

    Args:
        target: a single video/audio file OR a directory (top-level only — no recursion).
        out_path: override for the output index.json location.
        visual: if True, also tag sampled frames via Gemini Vision. STUBBED in 1.6.
        estimate: if True, only probe durations and return a cost estimate — no
            transcription, no index.json written.
        parallel: max concurrent `transcribe_words` calls. Default 4.

    Returns:
        IngestResult.

    Raises:
        NotImplementedError: if visual=True (deferred to follow-up phase).
        FileNotFoundError: if target does not exist.
        ValueError: if target is a directory with no recognized clip files.
    """
    if visual:
        raise NotImplementedError(
            "visual indexing arrives in a follow-up; rerun without --visual."
        )

    target_path = Path(target).expanduser().resolve()
    if not target_path.exists():
        raise FileNotFoundError(f"ingest: target not found: {target_path}")

    clips, single_file_mode = _discover_clips(target_path)
    log.info("ingest: discovered %d clip(s) under %s", len(clips), target_path)

    # Probe durations up front — needed for both estimate and real paths.
    durations: dict[Path, float] = {}
    for clip in clips:
        durations[clip] = _probe_duration(clip)

    total_duration = round(sum(durations.values()), 3)

    if estimate:
        # WhisperX runs locally and the visual path is stubbed, so cost is $0.00
        # for now. The flag still earns its keep by surfacing total footage
        # length without paying any transcription wall-clock cost.
        clip_records = [
            ClipIndex(path=str(p), duration_s=round(durations[p], 3), words=[])
            for p in clips
        ]
        log.info(
            "ingest: estimate-only — %d clip(s), %.2fs total, $0.00",
            len(clips), total_duration,
        )
        return IngestResult(
            index_path=None,
            clips=clip_records,
            total_duration_s=total_duration,
            estimated_cost_usd=0.0,
        )

    # Real path: parallel transcription, then aggregate.
    clip_records = _transcribe_all(clips, durations, parallel=parallel)

    index_path = _resolve_out_path(target_path, out_path, single_file_mode)
    _write_index(index_path, clip_records, total_duration)
    log.info("ingest: wrote %s (%d clip(s), %.2fs)", index_path, len(clip_records), total_duration)

    return IngestResult(
        index_path=index_path,
        clips=clip_records,
        total_duration_s=total_duration,
        estimated_cost_usd=0.0,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _discover_clips(target: Path) -> tuple[list[Path], bool]:
    """Return (sorted clip paths, single_file_mode).

    Single-file mode means target itself is a clip — output goes next to it.
    Directory mode globs the top level for recognized extensions.
    """
    if target.is_file():
        if target.suffix.lower() not in CLIP_EXTS:
            raise ValueError(
                f"ingest: {target} is not a recognized clip "
                f"(expected one of {sorted(CLIP_EXTS)})"
            )
        return [target], True

    if target.is_dir():
        clips = sorted(
            p for p in target.iterdir()
            if p.is_file() and p.suffix.lower() in CLIP_EXTS
        )
        if not clips:
            raise ValueError(f"ingest: no recognized clip extensions in {target}")
        return clips, False

    # Path exists but is neither file nor dir (socket, fifo, etc.)
    raise ValueError(f"ingest: target is neither file nor directory: {target}")


from .ffmpeg_utils import probe_duration as _ffmpeg_probe_duration


def _probe_duration(path: Path) -> float:
    """Return clip duration in seconds via ffprobe; 0.0 on failure (logged)."""
    result = _ffmpeg_probe_duration(path)
    if result is None:
        log.warning("ingest: failed to probe duration for %s", path)
        return 0.0
    if result == 0.0:
        log.warning("ingest: ffprobe returned empty duration for %s", path)
    return result


def _transcribe_all(
    clips: list[Path],
    durations: dict[Path, float],
    *,
    parallel: int,
) -> list[ClipIndex]:
    """Run transcribe_words on every clip in parallel; return ClipIndex list in input order."""
    results: list[ClipIndex | None] = [None] * len(clips)

    with tempfile.TemporaryDirectory(prefix="parallax-ingest-") as tmp_dir:
        tmp_root = Path(tmp_dir)

        def _job(idx: int, clip: Path) -> tuple[int, ClipIndex]:
            log.info("ingest: transcribing [%d/%d] %s", idx + 1, len(clips), clip.name)
            words_out = tmp_root / f"clip_{idx:04d}.words.json"
            try:
                words = transcribe_words(str(clip), str(words_out))
            except Exception as e:
                # Surface the failure with enough context to diagnose; one bad
                # clip shouldn't poison the whole batch — record empty words
                # and log loudly so the human notices.
                log.error("ingest: transcribe_words failed for %s: %s", clip, e)
                words = []
            return idx, ClipIndex(
                path=str(clip),
                duration_s=round(durations[clip], 3),
                words=words,
            )

        with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
            futures = [pool.submit(_job, i, clip) for i, clip in enumerate(clips)]
            for fut in futures:
                idx, record = fut.result()
                results[idx] = record

    # All slots are filled by the executor walk above.
    return [r for r in results if r is not None]


def _resolve_out_path(
    target: Path,
    out_path: str | Path | None,
    single_file_mode: bool,
) -> Path:
    """Determine where index.json should be written."""
    if out_path is not None:
        return Path(out_path).expanduser().resolve()
    if single_file_mode:
        return target.with_suffix(target.suffix + ".index.json")
    return target / "index.json"


def _write_index(index_path: Path, clips: list[ClipIndex], total_duration: float) -> None:
    """Serialize the aggregated index to disk."""
    payload = {
        "version": INDEX_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_duration_s": total_duration,
        "clips": [
            {
                "path": c.path,
                "duration_s": c.duration_s,
                "words": c.words,
            }
            for c in clips
        ],
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(payload, indent=2))
