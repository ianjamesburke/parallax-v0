"""`parallax cut` — script-anchored clip assembly, voiceover cache, manifest render & editor export.

Given a folder of clips, a script, and an agent-supplied (or name-inferred)
mapping, produces:
  1. Voiceover (with idempotent raw + processed cache) and word timings
  2. Script-token alignment and midpoint-of-gap scene boundary plan
  3. Project `manifest.yaml`
  4. Rendered captioned MP4 (`render_from_manifest`)
  5. Portable editor package (`assets/timeline.xml`, `timeline.fcpxml`, SRT)
     from the SAME manifest, verified for `manifest==xml` consistency.

Zero model calls for visual matching: when a mapping is missing and cannot be
unambiguously inferred from clip names/markers, exits with code 2 and writes a
pre-filled `cut-map.template.yaml` alongside a "MAPPING NEEDED" report.
"""

from __future__ import annotations

import difflib
import hashlib
import inspect
import json
import math
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import audio as _audio_mod
from .captions import burn_captions
from .captions.chunker import _fixed_chunk_words, _smart_chunk_words
from .editor_export import _build_srt, export_editor_package
from .ffmpeg_utils import (
    ENCODE_PRESETS,
    drive_safe_encode_args,
    even_dimensions,
    parse_resolution,
    probe_audio_duration,
    probe_duration,
    probe_resolution,
    run_ffmpeg,
)
from .log import get_logger
from .models import TTS_MODELS
from .preflight import print_estimate
from .settings import _ASPECT_TO_RESOLUTION
from .shim import is_test_mode
from .voiceover import (
    ElevenLabsVoiceProvider,
    OpenRouterVoiceProvider,
    VoiceProvider,
    VoiceSynthesisResult,
    coerce_synthesis_result,
    expand_numbers_and_symbols,
    get_voice_provider,
    int_to_words,
    normalize_text_for_verify,
    ordinal_to_words,
)

log = get_logger(__name__)

VO_PROCESS_VERSION = "cut-vo-v2"
VO_CHUNK_BASE_WPM = 150.0
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}

_NUMBER_WORDS: dict[str, str] = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "seventy": "70",
    "eighty": "80",
    "ninety": "90",
}


class CutError(Exception):
    """Raised when `parallax cut` cannot proceed (exit code 1 or 2)."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 2,
        report: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.report = report or {}


# ---------------------------------------------------------------------------
# Natural sort & clip discovery
# ---------------------------------------------------------------------------


def _natural_key(text: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def discover_clips(clips_dir: str | Path, *, probe: bool = True) -> list[dict[str, Any]]:
    """Discover video clips in `clips_dir`, sorted in natural filename order."""
    cdir = Path(clips_dir).expanduser().resolve()
    if not cdir.is_dir():
        raise CutError(f"Clips directory not found: {cdir}", exit_code=1)

    files = sorted(
        (
            p
            for p in cdir.iterdir()
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS and not p.name.startswith(".")
        ),
        key=lambda p: _natural_key(p.name),
    )
    if not files:
        raise CutError(f"No video clips found in {cdir}", exit_code=1)

    out: list[dict[str, Any]] = []
    for p in files:
        dur = 0.0
        res_str = "unknown"
        w, h = 0, 0
        if probe:
            d = probe_duration(p)
            dur = round(float(d), 4) if (d is not None and d > 0) else 0.0
            wh = probe_resolution(p)
            if wh:
                w, h = wh
                res_str = f"{w}x{h}"
        out.append(
            {
                "name": p.name,
                "stem": p.stem,
                "path": str(p),
                "duration_s": dur,
                "resolution": res_str,
                "width": w,
                "height": h,
            }
        )
    return out


def resolve_clip_reference(ref: str, clips: list[dict[str, Any]], clips_dir: Path | None = None) -> dict[str, Any] | None:
    """Resolve a clip name, stem, or relative path against discovered clips."""
    ref_clean = str(ref or "").strip()
    if not ref_clean:
        return None
    ref_path = Path(ref_clean)
    ref_lower = ref_clean.lower()
    ref_stem_lower = ref_path.stem.lower()

    # 1. Exact name or stem match
    for c in clips:
        if c["name"] == ref_clean or c["stem"] == ref_clean or c["path"] == ref_clean:
            return c
    # 2. Case-insensitive name or stem match
    for c in clips:
        if c["name"].lower() == ref_lower or c["stem"].lower() == ref_stem_lower or c["stem"].lower() == ref_lower:
            return c
    # 3. Relative path inside clips_dir
    if clips_dir is not None:
        cand = (clips_dir / ref_path).resolve()
        for c in clips:
            if Path(c["path"]).resolve() == cand:
                return c
    return None


def _extract_clip_numbers(clips: list[dict[str, Any]]) -> list[int] | None:
    """Return an unambiguous integer per clip if all clips are numbered, else None."""
    if not clips:
        return None
    all_nums = [[int(x) for x in re.findall(r"\d+", c["stem"])] for c in clips]
    if any(not nums for nums in all_nums):
        return None
    # If every stem has a single number, check uniqueness
    if all(len(nums) == 1 for nums in all_nums):
        vals = [nums[0] for nums in all_nums]
        return vals if len(set(vals)) == len(vals) else None
    # Try first number in each stem, then last number
    firsts = [nums[0] for nums in all_nums]
    if len(set(firsts)) == len(firsts):
        return firsts
    lasts = [nums[-1] for nums in all_nums]
    if len(set(lasts)) == len(lasts):
        return lasts
    return None


# ---------------------------------------------------------------------------
# Script tokenisation, sentence/paragraph splitting, and inline markers
# ---------------------------------------------------------------------------


def norm_token(word: str) -> str:
    """Normalise a word token to lowercase [a-z0-9] for alignment & anchor matching."""
    cleaned = word.replace("—", " ").replace("–", " ").lower()
    return re.sub(r"[^a-z0-9]+", "", cleaned)


def tokenize_script(text: str) -> list[str]:
    """Tokenise script on whitespace AND em/en dashes while keeping punctuation for captions.

    Em-dash-joined words like ``"limestone—the"`` become two tokens:
    ``["limestone—", "the"]``.
    """
    raw_tokens = re.findall(r"\S+", text)
    tokens: list[str] = []
    for tok in raw_tokens:
        parts = re.split(r"(?<=[—–])|(?=[—–])", tok)
        merged: list[str] = []
        for p in parts:
            if not p:
                continue
            if p in ("—", "–") and merged:
                merged[-1] += p
            else:
                merged.append(p)
        for m in merged:
            if norm_token(m):
                tokens.append(m)
            elif tokens:
                tokens[-1] += m
    return tokens


def split_script_sentences(text: str) -> list[str]:
    """Split script text into non-empty sentences in reading order."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    sentences: list[str] = []
    for para in paragraphs:
        # Collapse single newlines inside a paragraph unless lines lack terminal punctuation
        lines = [ln.strip() for ln in para.splitlines() if ln.strip()]
        if len(lines) > 1 and all(not re.search(r"[.!?][\"'”’)]*$", ln) for ln in lines[:-1]):
            for ln in lines:
                sentences.append(ln)
            continue
        joined = " ".join(lines)
        parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", joined) if s.strip()]
        sentences.extend(parts)
    return sentences


def split_script_paragraphs(text: str) -> list[str]:
    """Split script into paragraphs (blank-line-separated, or newline-separated if no blank lines)."""
    stripped = text.strip()
    if not stripped:
        return []
    if re.search(r"\n\s*\n", stripped):
        return [" ".join(p.split()) for p in re.split(r"\n\s*\n", stripped) if p.strip()]
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    return lines


def strip_script_brackets(text: str) -> str:
    """Strip inline `[...]` markers/brackets from script text while preserving paragraph breaks."""
    no_brackets = re.sub(r"\[[^\]]*\]", " ", text)
    paras = [
        " ".join(p.split())
        for p in re.split(r"\n\s*\n", no_brackets.strip())
        if p.strip()
    ]
    return "\n\n".join(paras)


def estimate_speech_seconds(
    text: str,
    *,
    speed: float = 1.15,
    base_wpm: float = VO_CHUNK_BASE_WPM,
) -> float:
    """Estimate spoken duration in seconds for `text` using `VO_CHUNK_BASE_WPM * speed`."""
    words_count = len(tokenize_script(text))
    if words_count == 0:
        return 0.0
    effective_wpm = max(30.0, float(base_wpm) * max(0.5, float(speed)))
    return (words_count * 60.0) / effective_wpm


def chunk_script(
    script_text: str,
    *,
    max_chunk_seconds: float = 60.0,
    speed: float = 1.15,
    base_wpm: float = VO_CHUNK_BASE_WPM,
) -> list[str]:
    """Split spoken script into chunks with a soft cap of `max_chunk_seconds` of speech.

    Rules:
      - `max_chunk_seconds <= 0` returns a single chunk (`[clean_script]`).
      - Starts a new chunk ONLY at a paragraph start or sentence start (NEVER mid-sentence).
      - Prefers paragraph boundaries when within the cap: packs whole paragraphs together
        while `estimate_speech_seconds(chunk + para) <= max_chunk_seconds`, flushing at the
        paragraph boundary before starting the next paragraph.
      - When a single paragraph exceeds `max_chunk_seconds`, splits that paragraph at
        sentence boundaries.
      - A single sentence longer than `max_chunk_seconds` stays whole in its own chunk.
    """
    cleaned = strip_script_brackets(script_text)
    if not cleaned.strip():
        return []
    if max_chunk_seconds is None or float(max_chunk_seconds) <= 0:
        return [" ".join(cleaned.split())]

    cap = float(max_chunk_seconds)
    paragraphs = split_script_paragraphs(cleaned)
    chunks: list[str] = []
    cur_paras: list[str] = []

    for para in paragraphs:
        para_clean = " ".join(para.split())
        if not para_clean:
            continue
        cand_text = "\n\n".join(cur_paras + [para_clean])
        if estimate_speech_seconds(cand_text, speed=speed, base_wpm=base_wpm) <= cap:
            cur_paras.append(para_clean)
            continue

        # Adding `para_clean` as a whole would exceed `cap` -> flush at paragraph boundary first
        if cur_paras:
            chunks.append("\n\n".join(cur_paras))
            cur_paras = []

        if estimate_speech_seconds(para_clean, speed=speed, base_wpm=base_wpm) <= cap:
            cur_paras = [para_clean]
            continue

        # Single paragraph exceeds `cap` -> split at sentence boundaries (never mid-sentence)
        sents = split_script_sentences(para_clean)
        cur_sents: list[str] = []
        for sent in sents:
            sent_clean = " ".join(sent.split())
            if not sent_clean:
                continue
            if not cur_sents:
                cur_sents = [sent_clean]
            else:
                cand_sent = " ".join(cur_sents + [sent_clean])
                if estimate_speech_seconds(cand_sent, speed=speed, base_wpm=base_wpm) <= cap:
                    cur_sents.append(sent_clean)
                else:
                    chunks.append(" ".join(cur_sents))
                    cur_sents = [sent_clean]

            # A single sentence longer than `cap` stays whole in its own chunk
            if len(cur_sents) == 1 and estimate_speech_seconds(cur_sents[0], speed=speed, base_wpm=base_wpm) > cap:
                chunks.append(cur_sents[0])
                cur_sents = []

        if cur_sents:
            chunks.append(" ".join(cur_sents))

    if cur_paras:
        chunks.append("\n\n".join(cur_paras))

    return chunks


def suggested_anchor_for_text(text: str, max_words: int = 4) -> str:
    """Return the opening 1..max_words tokens of a sentence or paragraph."""
    toks = tokenize_script(text)
    if not toks:
        return text.strip()[:32]
    return " ".join(toks[: min(max_words, len(toks))])


def parse_inline_markers(
    script_text: str,
    clips: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]] | None, list[str]]:
    """Detect and strip inline clip markers like `[Scene_03]`, `[scene 3]`, or `Scene 3:`.

    Returns `(clean_script_text, inferred_segments_or_None, errors)`.
    """
    clip_nums = _extract_clip_numbers(clips)
    num_to_clip: dict[int, dict[str, Any]] = {}
    if clip_nums is not None:
        for num, clip in zip(clip_nums, clips):
            num_to_clip[num] = clip

    def _match_marker_label(label: str) -> dict[str, Any] | None:
        lbl = label.strip()
        direct = resolve_clip_reference(lbl, clips)
        if direct is not None:
            return direct
        # Normalized e.g. "scene 3" -> "scene_3" / number 3
        m = re.match(r"^(?:scene|clip|shot)[-_ ]*(\d+)$", lbl, flags=re.IGNORECASE)
        if m:
            num = int(m.group(1))
            if num in num_to_clip:
                return num_to_clip[num]
            for c in clips:
                nums = [int(x) for x in re.findall(r"\d+", c["stem"])]
                if nums and nums[-1] == num:
                    return c
        return None

    # Pattern matches either `[Label]` or line/sentence-start `Scene 3:` / `<ClipStem>:`
    marker_pattern = re.compile(
        r"(?:\[\s*([A-Za-z0-9_.\-]+(?:\s+\d+)?)\s*\])"
        r"|(?:(?:^|(?<=\n)|(?<=[.!?]\s))\s*((?:scene|clip|shot)[-_ ]*\d+|[A-Za-z0-9_.\-]+)\s*:\s*)",
        flags=re.IGNORECASE,
    )

    matches: list[tuple[int, int, str, dict[str, Any] | None]] = []
    for m in marker_pattern.finditer(script_text):
        bracket_lbl = m.group(1)
        colon_lbl = m.group(2)
        raw_lbl = (bracket_lbl or colon_lbl or "").strip()
        if not raw_lbl:
            continue
        resolved = _match_marker_label(raw_lbl)
        is_explicit_tag = bracket_lbl is not None or bool(
            re.match(r"^(?:scene|clip|shot)[-_ ]*\d+$", raw_lbl, flags=re.IGNORECASE)
        )
        if resolved is not None or is_explicit_tag:
            matches.append((m.start(), m.end(), raw_lbl, resolved))

    if not matches:
        return script_text, None, []

    errors: list[str] = []
    segments: list[dict[str, Any]] = []
    clean_parts: list[str] = []

    # Any leading text before the first marker
    first_start = matches[0][0]
    leading_text = script_text[:first_start].strip()
    if leading_text:
        clean_parts.append(leading_text)

    for idx, (m_start, m_end, raw_lbl, resolved) in enumerate(matches):
        next_start = matches[idx + 1][0] if idx + 1 < len(matches) else len(script_text)
        body = script_text[m_end:next_start].strip()
        if resolved is None:
            errors.append(
                f"Inline marker {raw_lbl!r} does not match any clip in --clips "
                f"(available: {', '.join(c['name'] for c in clips)})"
            )
            continue
        if not body:
            errors.append(f"Inline marker {raw_lbl!r} has no spoken script text following it.")
            continue
        clean_parts.append(body)
        anchor = suggested_anchor_for_text(body, max_words=4)
        segments.append(
            {
                "anchor": anchor,
                "clip": resolved["name"],
                "inferred_from": f"inline marker [{raw_lbl}] -> {resolved['name']}",
            }
        )

    clean_script = "\n\n".join(clean_parts)
    return clean_script, segments, errors


# ---------------------------------------------------------------------------
# Alignment: script tokens <-> ASR / TTS words
# ---------------------------------------------------------------------------


def _expand_asr_norm_words(asr_words: list[dict[str, Any]]) -> list[str]:
    """Normalise ASR words for SequenceMatcher."""
    return [norm_token(str(w.get("word", ""))) for w in asr_words]


def _coalesce_number_words_for_matching(
    script_tokens: list[str],
    script_norm: list[str],
    asr_words: list[dict[str, Any]],
    asr_norm: list[str],
) -> tuple[list[str], dict[int, int]]:
    """Coalesce multi-word spoken numbers/symbols in ASR ('ninety', 'seven', 'percent' -> '97')
    to match numeric/symbol script tokens ('97%', '$5', '1,000', '45', '21st').

    Returns `(coalesced_asr_norm, span_end_by_asr_idx)` where `span_end_by_asr_idx[j]`
    records the inclusive last ASR word index `j_end` of a coalesced multi-word phrase starting at `j`.
    """
    # Build mapping from expanded spoken tuple -> canonical script_norm token
    phrase_to_sn: dict[tuple[str, ...], str] = {}
    for raw_tok, sn_tok in zip(script_tokens, script_norm):
        if any(ch.isdigit() for ch in raw_tok) or "%" in raw_tok or "$" in raw_tok:
            exp = tuple(normalize_text_for_verify(raw_tok))
            if exp:
                phrase_to_sn[exp] = sn_tok
            # Also register without trailing unit ("percent"/"dollars") in case script has "97%" and ASR has "percent" as next token
            if len(exp) >= 2 and exp[-1] in ("percent", "dollar", "dollars"):
                phrase_to_sn[exp[:-1]] = sn_tok

    if not phrase_to_sn:
        return list(asr_norm), {}

    # Expand each ASR word into its normalized word tuple (e.g. "ninety-seven" -> ("ninety", "seven"))
    asr_expanded: list[list[str]] = [
        normalize_text_for_verify(str(w.get("word", ""))) or [norm_token(str(w.get("word", "")))]
        for w in asr_words
    ]

    out = list(asr_norm)
    span_end_by_idx: dict[int, int] = {}
    n = len(out)
    i = 0
    max_span = min(6, n)
    while i < n:
        matched_len = 0
        matched_sn: str | None = None
        for span_len in range(min(max_span, n - i), 0, -1):
            flat: list[str] = []
            for k in range(i, i + span_len):
                flat.extend(asr_expanded[k])
            cand = tuple(flat)
            if cand in phrase_to_sn:
                matched_len = span_len
                matched_sn = phrase_to_sn[cand]
                break
        if matched_sn is not None and matched_len > 0:
            out[i] = matched_sn
            if matched_len > 1:
                span_end_by_idx[i] = i + matched_len - 1
            i += matched_len
        else:
            i += 1
    return out, span_end_by_idx


def align_script_words(
    script_text: str | list[str],
    asr_words: list[dict[str, Any]],
    audio_duration: float | None = None,
) -> dict[str, Any]:
    """Align script tokens to ASR/TTS word timings using `difflib.SequenceMatcher`.

    Returns a dict with:
      - `script_tokens`: list[str]
      - `words`: list[{word, start, end, matched}] (one entry per script token, with script text)
      - `amap`: list[int | None] mapping script token index -> ASR word index
      - `unmatched_tokens`: list[{index, token}] for script tokens not directly matched
      - `fuzzy_matches`: list[{index, script_token, asr_word}] for fuzzy/replaced tokens
    """
    tokens = tokenize_script(script_text) if isinstance(script_text, str) else list(script_text)
    n = len(tokens)
    if audio_duration is None or audio_duration <= 0:
        audio_duration = max((float(w.get("end", 0.0)) for w in asr_words), default=0.0)
    audio_dur = max(0.0, float(audio_duration))

    if n == 0:
        return {
            "script_tokens": [],
            "words": [],
            "amap": [],
            "unmatched_tokens": [],
            "fuzzy_matches": [],
        }

    if not asr_words:
        # Evenly space across audio_dur by character weight
        weights = [max(1, len(norm_token(t))) for t in tokens]
        tot_w = sum(weights) or 1
        cur = 0.0
        words_out = []
        for idx, (tok, w) in enumerate(zip(tokens, weights)):
            nxt = cur + audio_dur * (w / tot_w)
            words_out.append({"word": tok, "start": round(cur, 4), "end": round(nxt, 4), "matched": False})
            cur = nxt
        return {
            "script_tokens": tokens,
            "words": words_out,
            "amap": [None] * n,
            "unmatched_tokens": [{"index": i, "token": t} for i, t in enumerate(tokens)],
            "fuzzy_matches": [],
        }

    sn = [norm_token(t) for t in tokens]
    wn_raw = _expand_asr_norm_words(asr_words)
    wn, span_end_by_idx = _coalesce_number_words_for_matching(tokens, sn, asr_words, wn_raw)

    sm = difflib.SequenceMatcher(None, sn, wn, autojunk=False)
    amap: list[int | None] = [None] * n
    fuzzy_matches: list[dict[str, Any]] = []
    unmatched_tokens: list[dict[str, Any]] = []
    timed: list[dict[str, Any] | None] = [None] * n

    opcodes = sm.get_opcodes()
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            for k in range(i2 - i1):
                si = i1 + k
                wi = j1 + k
                amap[si] = wi
                w_start = float(asr_words[wi]["start"])
                w_end = float(asr_words[wi]["end"])
                if wi in span_end_by_idx and span_end_by_idx[wi] < len(asr_words):
                    w_end = max(w_end, float(asr_words[span_end_by_idx[wi]]["end"]))
                timed[si] = {
                    "word": tokens[si],
                    "start": round(w_start, 4),
                    "end": round(max(w_start, w_end), 4),
                    "matched": True,
                }
        elif tag == "replace" and j2 > j1:
            # Distribute script tokens i1..i2 across the ASR span j1..j2 while preserving surrounding silence gaps!
            span_start = float(asr_words[j1]["start"])
            span_end = max(span_start, float(asr_words[j2 - 1]["end"]))
            span_dur = max(0.02 * (i2 - i1), span_end - span_start)
            weights = [max(1, len(sn[si])) for si in range(i1, i2)]
            tot_w = sum(weights) or 1
            cur = span_start
            asr_joined = " ".join(str(asr_words[j].get("word", "")) for j in range(j1, j2))
            for k, si in enumerate(range(i1, i2)):
                # Check if 1-to-1 fuzzy match or number match
                wi = j1 + min(k, j2 - j1 - 1)
                sim = difflib.SequenceMatcher(None, sn[si], wn[wi]).ratio()
                if i2 - i1 == j2 - j1:
                    w_s = float(asr_words[j1 + k]["start"])
                    w_e = float(asr_words[j1 + k]["end"])
                else:
                    w_s = cur
                    w_e = cur + span_dur * (weights[k] / tot_w)
                    cur = w_e
                if sim >= 0.55 or (sn[si].isdigit() and any(ch.isdigit() for ch in wn[wi])):
                    amap[si] = wi
                    fuzzy_matches.append(
                        {
                            "index": si,
                            "script_token": tokens[si],
                            "asr_word": asr_joined if (j2 - j1 > 1) else str(asr_words[wi].get("word", "")),
                            "similarity": round(sim, 3),
                        }
                    )
                else:
                    unmatched_tokens.append({"index": si, "token": tokens[si]})
                timed[si] = {
                    "word": tokens[si],
                    "start": round(w_s, 4),
                    "end": round(max(w_s, w_e), 4),
                    "matched": amap[si] is not None,
                }
        else:
            # tag == 'delete' (j1 == j2): no ASR words in this slot; mark for neighbour interpolation
            for si in range(i1, i2):
                unmatched_tokens.append({"index": si, "token": tokens[si]})

    # Interpolate any remaining `None` entries (from 'delete' opcodes) by character length between neighbours
    i = 0
    while i < n:
        if timed[i] is not None:
            i += 1
            continue
        j = i
        while j < n and timed[j] is None:
            j += 1
        prev_end = float(timed[i - 1]["end"]) if i > 0 and timed[i - 1] is not None else 0.0
        next_start = float(timed[j]["start"]) if j < n and timed[j] is not None else audio_dur
        if next_start < prev_end:
            next_start = prev_end
        span = max(0.04 * (j - i), next_start - prev_end)
        weights = [max(1, len(sn[k])) for k in range(i, j)]
        tot_w = sum(weights) or 1
        cur = prev_end
        for k in range(i, j):
            w_dur = span * (weights[k - i] / tot_w)
            timed[k] = {
                "word": tokens[k],
                "start": round(cur, 4),
                "end": round(min(max(audio_dur, cur + w_dur), cur + w_dur), 4),
                "matched": False,
            }
            cur += w_dur
        i = j

    words_final: list[dict[str, Any]] = [t for t in timed if t is not None]
    return {
        "script_tokens": tokens,
        "words": words_final,
        "amap": amap,
        "unmatched_tokens": unmatched_tokens,
        "fuzzy_matches": fuzzy_matches,
    }


# ---------------------------------------------------------------------------
# Anchor location & candidate sentence suggestions
# ---------------------------------------------------------------------------


def rank_candidate_sentences(anchor: str, script_text: str, limit: int = 3) -> list[str]:
    """Return the closest candidate sentences from `script_text` for a missing anchor."""
    sentences = split_script_sentences(script_text)
    if not sentences:
        return []
    anchor_norm = " ".join(norm_token(t) for t in tokenize_script(anchor) if norm_token(t))
    anchor_toks = set(anchor_norm.split())

    scored: list[tuple[float, int, str]] = []
    for idx, sent in enumerate(sentences):
        sent_tokens = [norm_token(t) for t in tokenize_script(sent) if norm_token(t)]
        sent_norm = " ".join(sent_tokens)
        head_norm = " ".join(sent_tokens[: max(len(anchor_toks) + 2, 5)])
        overlap = len(anchor_toks & set(sent_tokens)) / max(1, len(anchor_toks))
        sim_head = difflib.SequenceMatcher(None, anchor_norm, head_norm).ratio()
        sim_full = difflib.SequenceMatcher(None, anchor_norm, sent_norm).ratio()
        score = max(sim_head, sim_full * 0.85) + 0.35 * overlap
        scored.append((score, idx, f"[sentence {idx + 1}] {sent}"))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[:limit]]


def locate_anchors(
    script_text: str,
    aligned_words: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    *,
    fuzzy_word_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Locate each segment's `anchor` forward-only (monotonically) in the script tokens.

    Validates ALL segments at once (does not stop on first failure).
    Returns:
      - `located`: list of dicts with `token_index`, `start_s`, `matched_text`, `fuzzy`
      - `errors`: list of human-readable validation error strings
      - `error_details`: list of structured error dicts
      - `warnings`: list of warnings (e.g. first anchor > 0 extended to 0)
      - `fuzzy_anchors`: list of fuzzy-matched anchor records
    """
    tokens = [str(w["word"]) for w in aligned_words]
    sn = [norm_token(t) for t in tokens]
    fuzzy_by_idx = {int(f["index"]): f for f in (fuzzy_word_matches or [])}

    pos = 0
    located: list[dict[str, Any]] = []
    errors: list[str] = []
    error_details: list[dict[str, Any]] = []
    warnings: list[str] = []
    fuzzy_anchors: list[dict[str, Any]] = []

    for seg_idx, seg in enumerate(segments):
        anchor_raw = str(seg.get("anchor") or "").strip()
        an = [norm_token(t) for t in tokenize_script(anchor_raw) if norm_token(t)]
        if not an:
            msg = f"Segment {seg_idx}: anchor {anchor_raw!r} contains no matchable words."
            errors.append(msg)
            error_details.append({"segment": seg_idx, "type": "empty_anchor", "anchor": anchor_raw, "message": msg})
            continue

        m = len(an)
        found: int | None = None
        is_fuzzy_anchor = False

        # 1. Exact forward search from `pos`
        for i in range(pos, len(sn) - m + 1):
            if sn[i : i + m] == an:
                found = i
                break

        # 2. Check if exact match exists BEFORE `pos` (non-monotonic anchor!)
        earlier: int | None = None
        if found is None and pos > 0:
            for i in range(0, min(pos, len(sn) - m + 1)):
                if sn[i : i + m] == an:
                    earlier = i
                    break

        if earlier is not None:
            prev_anchor = segments[seg_idx - 1].get("anchor") if seg_idx > 0 else ""
            msg = (
                f"Segment {seg_idx}: non-monotonic anchor {anchor_raw!r} found at word index {earlier}, "
                f"which is before previous anchor {prev_anchor!r} (at word index {pos - 1}). "
                f"Segments must be in script order."
            )
            errors.append(msg)
            error_details.append(
                {
                    "segment": seg_idx,
                    "type": "non_monotonic_anchor",
                    "anchor": anchor_raw,
                    "found_at_index": earlier,
                    "expected_after_index": pos - 1,
                    "message": msg,
                }
            )
            continue

        # 3. Try fuzzy forward search from `pos` if exact match not found anywhere
        if found is None:
            best_ratio = 0.0
            best_idx: int | None = None
            target_str = " ".join(an)
            for i in range(pos, len(sn) - m + 1):
                cand_tokens = sn[i : i + m]
                cand_str = " ".join(cand_tokens)
                ratio = difflib.SequenceMatcher(None, target_str, cand_str).ratio()
                tok_ratios = [
                    difflib.SequenceMatcher(None, a_tok, c_tok).ratio()
                    for a_tok, c_tok in zip(an, cand_tokens)
                ]
                if ratio > best_ratio and min(tok_ratios, default=0.0) >= 0.60:
                    best_ratio = ratio
                    best_idx = i
            if best_idx is not None and best_ratio >= 0.72:
                found = best_idx
                is_fuzzy_anchor = True

        if found is None:
            candidates = rank_candidate_sentences(anchor_raw, script_text, limit=3)
            cand_str = "; ".join(candidates) if candidates else "none"
            msg = (
                f"Segment {seg_idx}: anchor {anchor_raw!r} not found in script "
                f"(searched from word index {pos}). Closest candidate sentences: {cand_str}"
            )
            errors.append(msg)
            error_details.append(
                {
                    "segment": seg_idx,
                    "type": "anchor_not_found",
                    "anchor": anchor_raw,
                    "candidates": candidates,
                    "message": msg,
                }
            )
            continue

        if seg_idx == 0 and found > 0:
            warn_msg = (
                f"First anchor {anchor_raw!r} matched at word index {found} "
                f"(after {' '.join(tokens[:found])!r}); first segment extended to 0.0s automatically."
            )
            warnings.append(warn_msg)

        matched_phrase = " ".join(tokens[found : found + m])
        asr_fuzzy = next((fuzzy_by_idx[k] for k in range(found, min(len(tokens), found + m)) if k in fuzzy_by_idx), None)
        if is_fuzzy_anchor or asr_fuzzy is not None:
            fuzzy_anchors.append(
                {
                    "segment": seg_idx,
                    "anchor": anchor_raw,
                    "matched_script": matched_phrase,
                    "token_index": found,
                    "reason": "fuzzy_script_match" if is_fuzzy_anchor else "fuzzy_asr_alignment",
                }
            )

        located.append(
            {
                "segment": seg_idx,
                "anchor": anchor_raw,
                "clip": seg["clip"],
                "in": seg.get("in"),
                "note": seg.get("note"),
                "token_index": found,
                "start_s": float(aligned_words[found]["start"]),
                "matched_text": matched_phrase,
                "fuzzy": is_fuzzy_anchor or (asr_fuzzy is not None),
            }
        )
        # Advance monotonic cursor past the start of this anchor so subsequent identical phrases resolve in order
        pos = found + 1

    return {
        "located": located,
        "errors": errors,
        "error_details": error_details,
        "warnings": warnings,
        "fuzzy_anchors": fuzzy_anchors,
    }


# ---------------------------------------------------------------------------
# Mapping resolution: --map FILE -> name inference -> MAPPING NEEDED (exit 2)
# ---------------------------------------------------------------------------


def write_cut_map_template(
    template_path: Path,
    script_text: str,
    clips: list[dict[str, Any]],
) -> Path:
    """Write a pre-filled `cut-map.template.yaml` with suggested anchors for every sentence."""
    template_path.parent.mkdir(parents=True, exist_ok=True)
    sentences = split_script_sentences(script_text)
    segments_list = []
    for idx, sent in enumerate(sentences, start=1):
        segments_list.append(
            {
                "anchor": suggested_anchor_for_text(sent, max_words=4),
                "clip": "",
                "note": f"Sentence {idx}: {sent}",
            }
        )
    header = (
        "# cut-map.template.yaml — fill in `clip:` for each segment (or merge/delete rows),\n"
        "# save as `cut-map.yaml`, and rerun `parallax cut` with `--map`.\n"
        f"# Available clips ({len(clips)}): {', '.join(c['name'] for c in clips)}\n"
    )
    payload = {"version": 1, "segments": segments_list}
    body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    template_path.write_text(header + body, encoding="utf-8")
    return template_path


def load_and_validate_map_file(
    map_path: str | Path,
    script_text: str,
    clips: list[dict[str, Any]],
    clips_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """Load `--map FILE` (YAML or JSON) and validate clip names & anchors ALL AT ONCE.

    Returns `(resolved_segments, errors, error_details)`.
    """
    mpath = Path(map_path).expanduser().resolve()
    if not mpath.is_file():
        msg = f"Mapping file not found: {mpath}"
        return [], [msg], [{"type": "map_not_found", "path": str(mpath), "message": msg}]

    try:
        raw_text = mpath.read_text(encoding="utf-8")
        data = json.loads(raw_text) if mpath.suffix.lower() == ".json" else yaml.safe_load(raw_text)
    except Exception as exc:
        msg = f"Failed to parse mapping file {mpath}: {exc}"
        return [], [msg], [{"type": "map_parse_error", "path": str(mpath), "message": msg}]

    if not isinstance(data, dict) or not isinstance(data.get("segments"), list) or not data["segments"]:
        msg = f"Mapping file {mpath} must contain a non-empty 'segments' list."
        return [], [msg], [{"type": "invalid_map_schema", "path": str(mpath), "message": msg}]

    errors: list[str] = []
    error_details: list[dict[str, Any]] = []
    resolved_segments: list[dict[str, Any]] = []
    allowed_keys = {"anchor", "clip", "in", "note"}
    avail_names = [c["name"] for c in clips]

    for idx, item in enumerate(data["segments"]):
        if not isinstance(item, dict):
            msg = f"Segment {idx}: expected a mapping object, got {type(item).__name__}."
            errors.append(msg)
            error_details.append({"segment": idx, "type": "invalid_segment", "message": msg})
            continue

        extra = sorted(set(item.keys()) - allowed_keys)
        if extra:
            msg = f"Segment {idx}: unknown key(s) {extra}; allowed keys are anchor, clip, in, note."
            errors.append(msg)
            error_details.append({"segment": idx, "type": "unknown_keys", "keys": extra, "message": msg})

        anchor_val = str(item.get("anchor") or "").strip()
        clip_val = str(item.get("clip") or "").strip()

        if not anchor_val:
            msg = f"Segment {idx}: missing or empty 'anchor'."
            errors.append(msg)
            error_details.append({"segment": idx, "type": "empty_anchor", "message": msg})

        matched_clip = resolve_clip_reference(clip_val, clips, clips_dir=clips_dir) if clip_val else None
        if not clip_val:
            msg = f"Segment {idx} (anchor {anchor_val!r}): missing or empty 'clip' (available: {', '.join(avail_names)})."
            errors.append(msg)
            error_details.append({"segment": idx, "type": "empty_clip", "anchor": anchor_val, "message": msg})
        elif matched_clip is None:
            msg = (
                f"Segment {idx} (anchor {anchor_val!r}): unknown clip {clip_val!r} "
                f"(available clips: {', '.join(avail_names)})."
            )
            errors.append(msg)
            error_details.append(
                {
                    "segment": idx,
                    "type": "unknown_clip",
                    "clip": clip_val,
                    "available_clips": avail_names,
                    "message": msg,
                }
            )

        in_val = item.get("in")
        in_float: float | None = None
        if in_val is not None:
            try:
                in_float = float(in_val)
                if in_float < 0:
                    raise ValueError("negative in-point")
                if matched_clip and matched_clip["duration_s"] > 0 and in_float >= matched_clip["duration_s"]:
                    msg = (
                        f"Segment {idx} ({matched_clip['name']}): in-point {in_float:.2f}s "
                        f"is >= clip duration {matched_clip['duration_s']:.2f}s."
                    )
                    errors.append(msg)
                    error_details.append({"segment": idx, "type": "invalid_in_point", "in": in_float, "message": msg})
            except (TypeError, ValueError):
                msg = f"Segment {idx}: invalid 'in' value {in_val!r}; expected non-negative seconds."
                errors.append(msg)
                error_details.append({"segment": idx, "type": "invalid_in_point", "in": in_val, "message": msg})

        resolved_segments.append(
            {
                "anchor": anchor_val,
                "clip": matched_clip["name"] if matched_clip else clip_val,
                "in": in_float,
                "note": item.get("note"),
            }
        )

    # Also validate all anchors monotonically against script tokens right now so ALL errors are reported at once
    tokens = tokenize_script(script_text)
    dummy_words = [{"word": t, "start": float(i), "end": float(i + 0.5), "matched": True} for i, t in enumerate(tokens)]
    anchor_check = locate_anchors(script_text, dummy_words, resolved_segments)
    errors.extend(anchor_check["errors"])
    error_details.extend(anchor_check["error_details"])

    return resolved_segments, errors, error_details


def resolve_mapping(
    *,
    script_text: str,
    clips: list[dict[str, Any]],
    project_dir: Path,
    map_path: str | Path | None = None,
    clips_dir: Path | None = None,
    script_path: str | Path | None = None,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve clip-to-script mapping via: (a) `--map`, (b) name inference, or (c) MAPPING NEEDED.

    Raises `CutError(exit_code=2)` with full report on validation failure or when mapping is needed.
    """
    # Check for inline script markers first so marker text is always stripped from spoken/caption script
    clean_script, marker_segments, marker_errors = parse_inline_markers(script_text, clips)

    # (a) Explicit --map FILE
    if map_path is not None:
        segments, errors, error_details = load_and_validate_map_file(
            map_path, clean_script, clips, clips_dir=clips_dir
        )
        if errors:
            report = {
                "status": "invalid_mapping",
                "map_path": str(Path(map_path).expanduser().resolve()),
                "errors": errors,
                "error_details": error_details,
                "clips": clips,
            }
            msg_lines = ["MAPPING VALIDATION FAILED:"] + [f"  - {e}" for e in errors]
            raise CutError("\n".join(msg_lines), exit_code=2, report=report)
        return {
            "source": "map_file",
            "map_path": str(Path(map_path).expanduser().resolve()),
            "clean_script": clean_script,
            "segments": segments,
            "inference_log": [f"loaded {len(segments)} segment(s) from {Path(map_path).name}"],
            "rules_tried": [],
        }

    # (b) No --map: infer from names
    rules_tried: list[dict[str, str]] = []

    # Rule (i): inline script markers (`[Scene_03]`, `[scene 3]`, `Scene 3:`)
    if marker_segments is not None or marker_errors:
        if marker_errors:
            report = {
                "status": "invalid_mapping",
                "errors": marker_errors,
                "clips": clips,
            }
            msg_lines = ["INLINE MARKER VALIDATION FAILED:"] + [f"  - {e}" for e in marker_errors]
            raise CutError("\n".join(msg_lines), exit_code=2, report=report)
        assert marker_segments is not None
        inf_log = [f"inferred: {s.get('inferred_from', s['clip'])}" for s in marker_segments]
        return {
            "source": "inferred_inline_markers",
            "map_path": None,
            "clean_script": clean_script,
            "segments": marker_segments,
            "inference_log": inf_log,
            "rules_tried": [{"rule": "inline_markers", "result": f"matched {len(marker_segments)} inline markers"}],
        }
    rules_tried.append(
        {
            "rule": "inline_markers",
            "reason": "no inline [Scene_X] or 'Scene X:' markers found in script",
        }
    )

    clip_nums = _extract_clip_numbers(clips)
    paragraphs = split_script_paragraphs(clean_script)
    sentences = split_script_sentences(clean_script)

    if clip_nums is None:
        rules_tried.append(
            {
                "rule": "paragraphs",
                "reason": f"clips are not unambiguously numbered ({', '.join(c['name'] for c in clips)})",
            }
        )
        rules_tried.append(
            {
                "rule": "sentences",
                "reason": f"clips are not unambiguously numbered ({', '.join(c['name'] for c in clips)})",
            }
        )
    else:
        # Sort clips by their extracted number (natural sort already orders them, but ensure numeric order)
        ordered_clips = [c for _, c in sorted(zip(clip_nums, clips), key=lambda pair: pair[0])]

        # Rule (ii): paragraph count == clip count
        if len(paragraphs) == len(ordered_clips) and len(paragraphs) > 0:
            segments = []
            inf_log = []
            for k, (para, clip) in enumerate(zip(paragraphs, ordered_clips), start=1):
                anchor = suggested_anchor_for_text(para, max_words=4)
                segments.append({"anchor": anchor, "clip": clip["name"]})
                inf_log.append(f"inferred: paragraph {k} -> {clip['name']}")
            return {
                "source": "inferred_paragraphs",
                "map_path": None,
                "clean_script": clean_script,
                "segments": segments,
                "inference_log": inf_log,
                "rules_tried": rules_tried + [{"rule": "paragraphs", "result": f"matched {len(paragraphs)} paragraphs to {len(ordered_clips)} numbered clips"}],
            }
        rules_tried.append(
            {
                "rule": "paragraphs",
                "reason": f"paragraph count ({len(paragraphs)}) != clip count ({len(ordered_clips)})",
            }
        )

        # Rule (iii): sentence count == clip count
        if len(sentences) == len(ordered_clips) and len(sentences) > 0:
            segments = []
            inf_log = []
            for k, (sent, clip) in enumerate(zip(sentences, ordered_clips), start=1):
                anchor = suggested_anchor_for_text(sent, max_words=4)
                segments.append({"anchor": anchor, "clip": clip["name"]})
                inf_log.append(f"inferred: sentence {k} -> {clip['name']}")
            return {
                "source": "inferred_sentences",
                "map_path": None,
                "clean_script": clean_script,
                "segments": segments,
                "inference_log": inf_log,
                "rules_tried": rules_tried + [{"rule": "sentences", "result": f"matched {len(sentences)} sentences to {len(ordered_clips)} numbered clips"}],
            }
        rules_tried.append(
            {
                "rule": "sentences",
                "reason": f"sentence count ({len(sentences)}) != clip count ({len(ordered_clips)})",
            }
        )

    # (c) Otherwise FAIL with exit code 2 and a "MAPPING NEEDED" report + template file
    template_path = write_cut_map_template(project_dir / "cut-map.template.yaml", clean_script, clips)
    target_map_path = project_dir / "cut-map.yaml"
    s_arg = str(script_path) if script_path else "<script.txt>"
    c_arg = str(clips_dir) if clips_dir else "<clips_dir>"
    o_arg = str(out_path) if out_path else "<out.mp4>"
    rerun_cmd = f"parallax cut --script {s_arg} --clips {c_arg} --map {target_map_path} --out {o_arg}"

    sentence_entries = [
        {
            "number": idx,
            "suggested_anchor": suggested_anchor_for_text(sent, max_words=4),
            "text": sent,
        }
        for idx, sent in enumerate(sentences, start=1)
    ]
    clips_report = [{**c, "unmatched": True} for c in clips]

    report = {
        "status": "mapping_needed",
        "message": "MAPPING NEEDED: could not infer clip-to-script mapping automatically.",
        "clips": clips_report,
        "sentences": sentence_entries,
        "rules_tried": rules_tried,
        "template_path": str(template_path),
        "target_map_path": str(target_map_path),
        "rerun_command": rerun_cmd,
    }

    lines = [
        "MAPPING NEEDED",
        "Could not infer clip-to-script mapping automatically (no model calls are made for visual matching).",
        "",
        f"Clips ({len(clips_report)}):",
    ]
    for c in clips_report:
        lines.append(
            f"  - {c['name']}  duration={c['duration_s']:.2f}s  resolution={c['resolution']}  [unmatched]"
        )
    lines.append("")
    lines.append(f"Script sentences ({len(sentence_entries)}):")
    for s in sentence_entries:
        lines.append(f"  {s['number']:2d}. anchor: {s['suggested_anchor']!r}  — {s['text']}")
    lines.append("")
    lines.append("Inference rules tried:")
    for r in rules_tried:
        lines.append(f"  - {r['rule']}: {r['reason']}")
    lines.append("")
    lines.append(f"Template written: {template_path}")
    lines.append(f"Write mapping to: {target_map_path}")
    lines.append(f"Then rerun:       {rerun_cmd}")

    raise CutError("\n".join(lines), exit_code=2, report=report)


# ---------------------------------------------------------------------------
# Pure-function planning (`plan_cut`)
# ---------------------------------------------------------------------------


def plan_cut(
    *,
    script_text: str,
    words: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    clip_durations: dict[str, float],
    fps: int = 24,
    audio_duration: float | None = None,
    max_clip_s: float | None = None,
    allow_hold: bool = False,
) -> dict[str, Any]:
    """Pure planning function (no ffmpeg or audio I/O).

    Computes:
      1. Script token -> word timing alignment (with character-weighted interpolation)
      2. Monotonic forward anchor matching
      3. Midpoint-of-gap boundaries (segment 0 at 0.0, last at `audio_duration`)
      4. Per-clip capacity caps with iterative neighbour redistribution (`i-1` then `i+1`)
      5. Frame-snapped boundaries (`1/fps` grid, `last_edge >= audio_duration`, within 1 frame)
      6. Reuse-with-trim continuation (restarts at 0 only if remainder is too short, with warning)
      7. Overrun detection, unused clips, reused clips, fuzzy anchors, and interpolated tokens.
    """
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps}")
    if not segments:
        raise ValueError("segments must be non-empty")

    if audio_duration is None or audio_duration <= 0:
        audio_duration = max((float(w.get("end", 0.0)) for w in words), default=0.0)
    audio_dur = max(float(len(segments)) / fps, float(audio_duration))

    alignment = align_script_words(script_text, words, audio_duration=audio_dur)
    aligned_words = alignment["words"]
    script_tokens = alignment["script_tokens"]

    anchor_res = locate_anchors(
        script_text,
        aligned_words,
        segments,
        fuzzy_word_matches=alignment["fuzzy_matches"],
    )
    if anchor_res["errors"]:
        raise CutError(
            "\n".join(["MAPPING VALIDATION FAILED:"] + [f"  - {e}" for e in anchor_res["errors"]]),
            exit_code=2,
            report={
                "status": "invalid_mapping",
                "errors": anchor_res["errors"],
                "error_details": anchor_res["error_details"],
            },
        )

    located = anchor_res["located"]
    warnings: list[str] = list(anchor_res["warnings"])
    n = len(located)

    # 1. Raw boundaries: midpoint of silence gap before each segment's anchor word
    raw_bounds: list[float] = [0.0]
    for i in range(1, n):
        tok_idx = int(located[i]["token_index"])
        anchor_start = float(aligned_words[tok_idx]["start"])
        prev_end = float(aligned_words[tok_idx - 1]["end"]) if tok_idx > 0 else 0.0
        if anchor_start > prev_end:
            boundary = 0.5 * (prev_end + anchor_start)
        else:
            boundary = anchor_start
        raw_bounds.append(max(raw_bounds[-1], min(boundary, audio_dur)))
    raw_bounds.append(audio_dur)

    # 2. Per-segment usable capacity caps
    caps: list[float] = []
    for i, seg in enumerate(located):
        clip_name = seg["clip"]
        raw_clip_dur = float(clip_durations.get(clip_name, 0.0))
        usable = min(raw_clip_dur, float(max_clip_s)) if (max_clip_s is not None and raw_clip_dur > 0) else (
            float(max_clip_s) if max_clip_s is not None else raw_clip_dur
        )
        if seg.get("in") is not None:
            usable = max(0.0, usable - float(seg["in"]))
        caps.append(usable)

    # 3. Iterative neighbour redistribution (i-1 then i+1)
    bounds = list(raw_bounds)
    for _ in range(300):
        changed = False
        for i in range(n):
            slot_dur = bounds[i + 1] - bounds[i]
            cap_i = caps[i]
            if cap_i > 0 and slot_dur > cap_i + 1e-4:
                excess = slot_dur - cap_i
                if i > 0:
                    prev_room = max(0.0, caps[i - 1] - (bounds[i] - bounds[i - 1]))
                    shift = min(excess, prev_room)
                    if shift > 1e-4:
                        bounds[i] += shift
                        excess -= shift
                        changed = True
                if excess > 1e-4 and i < n - 1:
                    next_room = max(0.0, caps[i + 1] - (bounds[i + 2] - bounds[i + 1]))
                    shift = min(excess, next_room)
                    if shift > 1e-4:
                        bounds[i + 1] -= shift
                        changed = True
        if not changed:
            break

    # 4. Snap boundaries to frame grid (last edge >= audio_dur, within 1 frame)
    total_frames = max(n, int(math.ceil(audio_dur * fps - 1e-6)))
    edge_frames: list[int] = [0] * (n + 1)
    edge_frames[0] = 0
    edge_frames[n] = total_frames
    for i in range(1, n):
        edge_frames[i] = int(round(bounds[i] * fps))
    for i in range(1, n):
        edge_frames[i] = max(edge_frames[i - 1] + 1, edge_frames[i])
    for i in range(n - 1, 0, -1):
        edge_frames[i] = min(edge_frames[i], edge_frames[i + 1] - 1)

    # Integer-frame redistribution pass so frame rounding never introduces avoidable 1-frame overruns
    cap_frames = [max(1, int(math.floor(c * fps + 1e-6))) if c > 0 else total_frames for c in caps]
    for _ in range(100):
        changed = False
        for i in range(n):
            f_dur = edge_frames[i + 1] - edge_frames[i]
            if f_dur > cap_frames[i]:
                f_excess = f_dur - cap_frames[i]
                if i > 0:
                    prev_f_room = max(0, cap_frames[i - 1] - (edge_frames[i] - edge_frames[i - 1]))
                    f_shift = min(f_excess, prev_f_room)
                    if f_shift > 0 and (edge_frames[i + 1] - (edge_frames[i] + f_shift)) >= 1:
                        edge_frames[i] += f_shift
                        f_excess -= f_shift
                        changed = True
                if f_excess > 0 and i < n - 1:
                    next_f_room = max(0, cap_frames[i + 1] - (edge_frames[i + 2] - edge_frames[i + 1]))
                    f_shift = min(f_excess, next_f_room)
                    if f_shift > 0 and (edge_frames[i + 1] - f_shift - edge_frames[i]) >= 1:
                        edge_frames[i + 1] -= f_shift
                        changed = True
        if not changed:
            break

    edges = [round(f / float(fps), 6) for f in edge_frames]
    slots = [round((edge_frames[i + 1] - edge_frames[i]) / float(fps), 6) for i in range(n)]

    # 5. Reuse policy & trim continuation + overrun detection
    used_cursor: dict[str, float] = {}
    use_counts: dict[str, int] = {}
    reused_clips: list[dict[str, Any]] = []
    overruns: list[dict[str, Any]] = []
    planned_scenes: list[dict[str, Any]] = []

    for i, seg in enumerate(located):
        clip_name = seg["clip"]
        slot = slots[i]
        slot_f = edge_frames[i + 1] - edge_frames[i]
        clip_dur = float(clip_durations.get(clip_name, 0.0))
        cap_i = caps[i]
        prev_uses = use_counts.get(clip_name, 0)
        restarted = False

        if seg.get("in") is not None:
            ts = float(seg["in"])
        else:
            ts = used_cursor.get(clip_name, 0.0)
            if prev_uses > 0 and clip_dur > 0 and (ts + slot > clip_dur + 1e-4):
                ts = 0.0
                restarted = True
                warnings.append(
                    f"Segment {i} ({clip_name}): remainder too short for {slot:.2f}s slot; "
                    f"restarting at 0.0s (repeated footage)."
                )

        if clip_dur > 0:
            ts = max(0.0, min(ts, max(0.0, clip_dur - slot)))
        else:
            ts = max(0.0, ts)

        used_cursor[clip_name] = ts + slot
        use_counts[clip_name] = prev_uses + 1

        if prev_uses > 0:
            reused_clips.append(
                {
                    "segment": i,
                    "clip": clip_name,
                    "use_number": prev_uses + 1,
                    "trim_start_s": round(ts, 4),
                    "trim_end_s": round(ts + slot, 4),
                    "restarted_at_zero": restarted,
                }
            )

        # Check for overrun against usable capacity
        effective_cap = min(cap_i, clip_dur) if (cap_i > 0 and clip_dur > 0) else (cap_i or clip_dur)
        if effective_cap > 0 and slot > effective_cap + (0.5 / fps):
            overrun_rec = {
                "segment": i,
                "anchor": seg["anchor"],
                "clip": clip_name,
                "slot_s": round(slot, 4),
                "usable_s": round(effective_cap, 4),
                "excess_s": round(slot - effective_cap, 4),
                "suggestion": (
                    f"Add a mapping entry for another clip (or reuse a clip) inside segment {i} "
                    f"({seg['anchor']!r}), or pass --allow-hold to freeze the last frame."
                ),
            }
            overruns.append(overrun_rec)
            if allow_hold:
                warnings.append(
                    f"Segment {i} ({clip_name}): slot {slot:.2f}s exceeds usable {effective_cap:.2f}s "
                    f"by {slot - effective_cap:.2f}s; holding last frame (--allow-hold)."
                )

        # Spoken text slice for this segment
        tok_start = 0 if i == 0 else int(located[i]["token_index"])
        tok_end = int(located[i + 1]["token_index"]) if i + 1 < n else len(script_tokens)
        vo_slice = " ".join(script_tokens[tok_start:tok_end])

        planned_scenes.append(
            {
                "index": i,
                "anchor": seg["anchor"],
                "clip": clip_name,
                "start_s": round(edges[i], 4),
                "end_s": round(edges[i + 1], 4),
                "duration_s": round(slot, 4),
                "start_frame": edge_frames[i],
                "end_frame": edge_frames[i + 1],
                "duration_frames": slot_f,
                "clip_trim_start_s": round(ts, 4),
                "clip_duration_s": round(clip_dur, 4),
                "usable_cap_s": round(effective_cap, 4),
                "scene_type": "clip",
                "vo_text": vo_slice,
            }
        )

    all_clip_names = list(clip_durations.keys())
    used_set = set(use_counts.keys())
    unused_clips = [c for c in all_clip_names if c not in used_set]

    return {
        "fps": fps,
        "audio_duration_s": round(audio_dur, 4),
        "video_duration_s": round(edges[-1], 4),
        "total_frames": total_frames,
        "raw_boundaries_s": [round(b, 4) for b in raw_bounds],
        "edges_s": edges,
        "slots_s": slots,
        "scenes": planned_scenes,
        "unused_clips": unused_clips,
        "reused_clips": reused_clips,
        "reuse_count": len(reused_clips),
        "overruns": overruns,
        "fuzzy_anchors": anchor_res["fuzzy_anchors"],
        "unmatched_script_tokens": alignment["unmatched_tokens"],
        "fuzzy_word_matches": alignment["fuzzy_matches"],
        "script_words": aligned_words,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Voiceover post-processing & two-tier idempotent cache
# ---------------------------------------------------------------------------


def compute_vo_cache_keys(
    *,
    script_text: str,
    voice: str,
    voice_model: str,
    style: str | None,
    speed: float,
    max_sentence_silence: float,
    vo_chunk_seconds: float = 60.0,
    vo_gap: float = 0.25,
    vo_lufs: float = -16.0,
    vo_lufs_tolerance: float = 1.5,
    vo_normalize: bool = True,
) -> tuple[str, str]:
    """Return `(raw_key, processed_key)` sha256 hex digests for the voiceover cache."""
    norm_script = " ".join(script_text.split())
    raw_payload = json.dumps(
        {
            "script": norm_script,
            "voice": voice or "",
            "voice_model": voice_model or "",
            "style": style or "",
            "vo_chunk_seconds": round(float(vo_chunk_seconds), 4),
        },
        sort_keys=True,
    )
    raw_key = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()

    proc_payload = json.dumps(
        {
            "script": norm_script,
            "voice": voice or "",
            "voice_model": voice_model or "",
            "style": style or "",
            "speed": round(float(speed), 4),
            "max_sentence_silence": round(float(max_sentence_silence), 4),
            "vo_chunk_seconds": round(float(vo_chunk_seconds), 4),
            "vo_gap": round(float(vo_gap), 4),
            "vo_lufs": round(float(vo_lufs), 2) if vo_normalize else None,
            "vo_normalize": bool(vo_normalize),
            "version": VO_PROCESS_VERSION,
        },
        sort_keys=True,
    )
    proc_key = hashlib.sha256(proc_payload.encode("utf-8")).hexdigest()
    return raw_key, proc_key


def compute_chunk_cache_keys(
    *,
    chunk_text: str,
    voice: str,
    voice_model: str,
    style: str | None,
    speed: float,
    max_sentence_silence: float,
    vo_lufs: float = -16.0,
    vo_normalize: bool = True,
    min_padding_s: float | str | None = 0.15,
) -> tuple[str, str]:
    """Return `(raw_chunk_key, proc_chunk_key)` sha256 hex digests for a single VO chunk.

    `raw_chunk_key` is `sha256(f"{norm_text}|{voice}|{voice_model}|{style}")`.
    """
    norm_chunk = " ".join(chunk_text.split())
    raw_str = f"{norm_chunk}|{voice or ''}|{voice_model or ''}|{style or ''}"
    raw_chunk_key = hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    proc_payload = json.dumps(
        {
            "chunk": norm_chunk,
            "voice": voice or "",
            "voice_model": voice_model or "",
            "style": style or "",
            "speed": round(float(speed), 4),
            "max_sentence_silence": round(float(max_sentence_silence), 4),
            "vo_lufs": round(float(vo_lufs), 2) if vo_normalize else None,
            "vo_normalize": bool(vo_normalize),
            "padding": str(min_padding_s),
            "version": VO_PROCESS_VERSION,
        },
        sort_keys=True,
    )
    proc_chunk_key = hashlib.sha256(proc_payload.encode("utf-8")).hexdigest()
    return raw_chunk_key, proc_chunk_key


PAUSE_TRIM_NOISE_DB = -35.0
PAUSE_TRIM_MIN_SILENCE_S = 0.12
PAUSE_TRIM_DEFAULT_PADDING_S = 0.15
PAUSE_TRIM_ESCALATION_LEVELS: tuple[float | None, ...] = (0.15, 0.30, 0.50, None)
LONG_TOKEN_THRESHOLD_S = 0.60


def detect_energy_silences(
    audio_path: str | Path,
    *,
    noise_db: float = PAUSE_TRIM_NOISE_DB,
    min_silence_s: float = PAUSE_TRIM_MIN_SILENCE_S,
    total_duration_s: float | None = None,
) -> list[dict[str, float]]:
    """Detect real silence intervals by energy via ffmpeg `silencedetect` (`noise=-35dB:d=0.12`)."""
    p = Path(audio_path)
    if not p.is_file():
        return []
    res = run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(p),
            "-af",
            f"silencedetect=noise={float(noise_db)}dB:d={float(min_silence_s)}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return []

    silences: list[dict[str, float]] = []
    cur_start: float | None = None
    for line in (res.stderr or "").splitlines():
        if "silence_start:" in line:
            try:
                cur_start = float(line.split("silence_start:")[1].strip().split()[0])
            except (ValueError, IndexError):
                cur_start = None
        elif "silence_end:" in line and cur_start is not None:
            try:
                parts = line.split("silence_end:")[1].split("|")
                end_t = float(parts[0].strip().split()[0])
                dur_t = (
                    float(parts[1].split(":")[1].strip().split()[0])
                    if len(parts) > 1 and ":" in parts[1]
                    else max(0.0, end_t - cur_start)
                )
                if dur_t >= float(min_silence_s) - 1e-3 and end_t > cur_start:
                    silences.append(
                        {
                            "start": round(cur_start, 4),
                            "end": round(end_t, 4),
                            "duration": round(dur_t, 4),
                        }
                    )
            except (ValueError, IndexError):
                pass
            cur_start = None

    if cur_start is not None and total_duration_s is not None and float(total_duration_s) > cur_start:
        dur_t = float(total_duration_s) - cur_start
        if dur_t >= float(min_silence_s) - 1e-3:
            silences.append(
                {
                    "start": round(cur_start, 4),
                    "end": round(float(total_duration_s), 4),
                    "duration": round(dur_t, 4),
                }
            )
    return silences


def flag_long_aligned_tokens(
    raw_words: list[dict[str, Any]],
    *,
    threshold_s: float = LONG_TOKEN_THRESHOLD_S,
) -> list[dict[str, Any]]:
    """Return metadata for tokens whose aligned span exceeds `threshold_s` (default 0.6s)."""
    flagged: list[dict[str, Any]] = []
    for idx, w in enumerate(raw_words):
        s = float(w.get("start", 0.0))
        e = float(w.get("end", s))
        dur = e - s
        if dur > float(threshold_s) + 1e-6:
            flagged.append(
                {
                    "index": idx,
                    "word": str(w.get("word", "")),
                    "start": round(s, 4),
                    "end": round(e, 4),
                    "duration_s": round(dur, 4),
                }
            )
    return flagged


def compute_naive_alignment_cuts(
    raw_words: list[dict[str, Any]],
    *,
    max_sentence_silence: float = 0.25,
) -> list[tuple[float, float]]:
    """Naive alignment-timestamp-only cut intervals (used as baseline reference in tests)."""
    keep_gap = max(0.0, float(max_sentence_silence))
    if not raw_words or keep_gap <= 0:
        return []
    threshold = max(keep_gap + 0.05, keep_gap * 1.2)
    half = keep_gap / 2.0
    cuts: list[tuple[float, float]] = []
    first_start = float(raw_words[0].get("start", 0.0))
    if first_start > keep_gap:
        cuts.append((0.0, round(max(0.0, first_start - half), 4)))
    for i in range(len(raw_words) - 1):
        g0 = float(raw_words[i].get("end", 0.0))
        g1 = float(raw_words[i + 1].get("start", g0))
        if g1 - g0 > threshold:
            cuts.append((round(g0 + half, 4), round(g1 - half, 4)))
    return cuts


def compute_energy_pause_cuts(
    raw_words: list[dict[str, Any]],
    energy_silences: list[dict[str, Any]],
    *,
    max_sentence_silence: float = 0.25,
    min_padding_s: float | str | None = PAUSE_TRIM_DEFAULT_PADDING_S,
    long_token_threshold_s: float = LONG_TOKEN_THRESHOLD_S,
) -> dict[str, Any]:
    """Compute safe silence cuts centered at the MIDDLE of real energy-silence intervals
    that lie inside alignment gaps.

    Rules:
      - Never cuts using alignment timestamps alone: a gap with no energy-silence is untouched.
      - Keeps `>= min_padding_s` (default 0.15s, escalation 0.15 -> 0.30 -> 0.50 -> None/off)
        and `>= max_sentence_silence / 2` on each side of the kept speech inside the real silence.
      - Cuts symmetrically around the midpoint `(es0 + es1) / 2` of the energy-silence interval
        `[es0, es1]` within the alignment gap.
      - If an adjacent token is aligned `> long_token_threshold_s` (0.6s), widens the margin `2x`
        for that gap.
    """
    long_tokens = flag_long_aligned_tokens(raw_words, threshold_s=long_token_threshold_s)
    long_indices = {item["index"] for item in long_tokens}

    if (
        min_padding_s is None
        or (isinstance(min_padding_s, str) and min_padding_s.strip().lower() in ("off", "none"))
        or float(max_sentence_silence) <= 0
        or not raw_words
        or not energy_silences
    ):
        return {
            "cuts": [],
            "long_tokens": long_tokens,
            "padding_s": None,
        }

    keep_gap = max(0.0, float(max_sentence_silence))
    base_pad = max(PAUSE_TRIM_DEFAULT_PADDING_S, float(min_padding_s), keep_gap / 2.0)

    # Build candidate alignment gaps: (g0, g1, adjacent_token_indices)
    candidate_gaps: list[tuple[float, float, tuple[int, ...]]] = []
    first_start = float(raw_words[0].get("start", 0.0))
    if first_start > 0:
        candidate_gaps.append((0.0, first_start, (0,)))
    for i in range(len(raw_words) - 1):
        g0 = float(raw_words[i].get("end", 0.0))
        g1 = float(raw_words[i + 1].get("start", g0))
        if g1 > g0:
            candidate_gaps.append((g0, g1, (i, i + 1)))

    cuts: list[tuple[float, float]] = []
    for g0, g1, adj_idxs in candidate_gaps:
        is_adj_long = any(idx in long_indices for idx in adj_idxs)
        side_pad = base_pad * (2.0 if is_adj_long else 1.0)

        # Find the longest energy-silence interval intersecting this alignment gap
        best_es: tuple[float, float] | None = None
        best_len = 0.0
        for sil in energy_silences:
            s0 = float(sil.get("start", 0.0))
            s1 = float(sil.get("end", s0))
            es0 = max(s0, g0)
            es1 = min(s1, g1)
            if es1 - es0 > best_len:
                best_len = es1 - es0
                best_es = (es0, es1)

        if best_es is None:
            continue
        es0, es1 = best_es
        # Only cut around the midpoint of [es0, es1] if at least side_pad remains on EACH side
        if (es1 - es0) <= (2.0 * side_pad + 0.02):
            continue
        mid = 0.5 * (es0 + es1)
        half_cut = 0.5 * (es1 - es0) - side_pad
        c0 = round(mid - half_cut, 4)
        c1 = round(mid + half_cut, 4)
        if c1 > c0 + 1e-4:
            cuts.append((c0, c1))

    return {
        "cuts": cuts,
        "long_tokens": long_tokens,
        "padding_s": round(base_pad, 4),
    }


def measure_audio_loudness_lufs(
    audio_path: str | Path,
    *,
    target_lufs: float = -16.0,
    tp: float = -1.5,
    lra: float = 11.0,
) -> dict[str, float] | None:
    """Measure integrated loudness (LUFS), true peak, LRA, and threshold via ffmpeg `loudnorm` pass 1."""
    p = Path(audio_path)
    if not p.is_file():
        return None
    res = run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(p),
            "-af",
            f"loudnorm=I={float(target_lufs)}:TP={float(tp)}:LRA={float(lra)}:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return None
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", res.stderr or "", re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        in_i = float(data["input_i"])
        in_tp = float(data["input_tp"])
        in_lra = float(data["input_lra"])
        in_th = float(data["input_thresh"])
        t_off = float(data["target_offset"])
        if not math.isfinite(in_i) or in_i <= -70.0:
            return None
        return {
            "input_i": round(in_i, 2),
            "input_tp": round(in_tp, 2),
            "input_lra": round(in_lra, 2),
            "input_thresh": round(in_th, 2),
            "target_offset": round(t_off, 2),
        }
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def normalize_audio_loudness(
    in_path: str | Path,
    out_path: str | Path,
    *,
    target_lufs: float = -16.0,
    tp: float = -1.5,
    lra: float = 11.0,
) -> dict[str, Any]:
    """Two-pass (measured-linear) ffmpeg `loudnorm` normalisation to `target_lufs` (default -16 LUFS)."""
    in_p = Path(in_path)
    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    stats = measure_audio_loudness_lufs(in_p, target_lufs=target_lufs, tp=tp, lra=lra)
    if stats is None:
        if in_p.is_file() and in_p.resolve() != out_p.resolve():
            shutil.copy2(in_p, out_p)
        return {"normalized": False, "target_lufs": float(target_lufs), "input_lufs": None}

    af = (
        f"loudnorm=I={float(target_lufs)}:TP={float(tp)}:LRA={float(lra)}:linear=true:"
        f"measured_I={stats['input_i']}:"
        f"measured_TP={stats['input_tp']}:"
        f"measured_LRA={stats['input_lra']}:"
        f"measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}"
    )
    suffix = out_p.suffix.lower()
    if suffix == ".wav":
        codec_args = ["-c:a", "pcm_s16le", "-ar", "44100"]
    elif suffix in (".m4a", ".aac"):
        codec_args = ["-c:a", "aac", "-b:a", "192k", "-ar", "44100"]
    else:
        codec_args = ["-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100"]

    with tempfile.TemporaryDirectory(prefix="parallax-loudnorm-") as td:
        tmp_out = Path(td) / f"norm{suffix or '.mp3'}"
        res = run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(in_p),
                "-af",
                af,
                *codec_args,
                str(tmp_out),
            ],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and tmp_out.is_file():
            shutil.copy2(tmp_out, out_p)
            return {
                "normalized": True,
                "target_lufs": float(target_lufs),
                "input_lufs": stats["input_i"],
            }

    if in_p.resolve() != out_p.resolve():
        shutil.copy2(in_p, out_p)
    return {"normalized": False, "target_lufs": float(target_lufs), "input_lufs": stats["input_i"]}


def _preview_chunk_text(text: str, max_len: int = 80) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def analyze_voiceover_loudness(
    audio_path: str | Path,
    *,
    chunks: list[Any] | None = None,
    target_lufs: float = -16.0,
    tolerance_lu: float = 1.5,
    vo_gap: float = 0.25,
    out_json_path: str | Path | None = None,
) -> dict[str, Any]:
    """Measure integrated loudness (LUFS) per chunk (after normalisation) and for the final
    concatenated audio, check chunk consistency against `mean_chunk_lufs ± tolerance_lu`, and
    optionally write `<project>/voiceover.loudness.json`.
    """
    p = Path(audio_path)
    final_dur = probe_audio_duration(p) if p.is_file() else None
    final_stats = (
        measure_audio_loudness_lufs(p, target_lufs=target_lufs)
        if (final_dur is not None and final_dur > 0)
        else None
    )
    final_lufs: float | None = final_stats["input_i"] if final_stats is not None else None

    chunk_entries: list[dict[str, Any]] = []
    gap = max(0.0, float(vo_gap))

    if not chunks:
        if p.is_file():
            chunk_entries.append(
                {
                    "index": 0,
                    "text_preview": "",
                    "start_s": 0.0,
                    "end_s": round(float(final_dur or 0.0), 2),
                    "integrated_lufs": final_lufs,
                }
            )
    else:
        cursor_s = 0.0
        n_chunks = len(chunks)
        for idx, raw_ch in enumerate(chunks):
            if isinstance(raw_ch, (str, Path)):
                ch_path = Path(raw_ch)
                c_idx = idx
                preview = ch_path.name
                probed_c_dur = probe_audio_duration(ch_path) if ch_path.is_file() else None
                c_dur = float(probed_c_dur or 0.0)
                start_s = round(cursor_s, 2)
                end_s = round(cursor_s + c_dur, 2)
                c_stats = (
                    measure_audio_loudness_lufs(ch_path, target_lufs=target_lufs)
                    if (probed_c_dur is not None and probed_c_dur > 0)
                    else None
                )
                c_lufs = c_stats["input_i"] if c_stats is not None else None
            elif isinstance(raw_ch, dict):
                c_idx = int(raw_ch.get("index", raw_ch.get("chunk_index", idx)))
                preview = _preview_chunk_text(raw_ch.get("text_preview") or raw_ch.get("text") or "")
                ch_audio = raw_ch.get("audio_path")
                ch_path = Path(ch_audio) if ch_audio else None
                probed_c_dur = probe_audio_duration(ch_path) if (ch_path is not None and ch_path.is_file()) else None
                c_dur = float(
                    raw_ch.get("total_duration_s")
                    or raw_ch.get("duration_s")
                    or probed_c_dur
                    or 0.0
                )
                start_s = (
                    round(float(raw_ch["start_s"]), 2)
                    if raw_ch.get("start_s") is not None
                    else round(cursor_s, 2)
                )
                end_s = (
                    round(float(raw_ch["end_s"]), 2)
                    if raw_ch.get("end_s") is not None
                    else round(start_s + c_dur, 2)
                )
                raw_lufs = raw_ch.get("integrated_lufs")
                if isinstance(raw_lufs, (int, float)) and math.isfinite(float(raw_lufs)):
                    c_lufs = round(float(raw_lufs), 2)
                elif ch_path is not None and probed_c_dur is not None and probed_c_dur > 0:
                    if n_chunks == 1 and final_lufs is not None and p.is_file() and ch_path.resolve() == p.resolve():
                        c_lufs = final_lufs
                    else:
                        c_stats = measure_audio_loudness_lufs(ch_path, target_lufs=target_lufs)
                        c_lufs = c_stats["input_i"] if c_stats is not None else None
                else:
                    c_lufs = None
            else:
                continue

            cursor_s = end_s + (gap if idx < n_chunks - 1 else 0.0)
            chunk_entries.append(
                {
                    "index": c_idx,
                    "text_preview": preview,
                    "start_s": start_s,
                    "end_s": end_s,
                    "integrated_lufs": c_lufs,
                }
            )

    measured_vals = [
        float(c["integrated_lufs"])
        for c in chunk_entries
        if isinstance(c.get("integrated_lufs"), (int, float)) and math.isfinite(float(c["integrated_lufs"]))
    ]
    if final_lufs is None and len(measured_vals) == 1:
        final_lufs = round(measured_vals[0], 2)

    mean_chunk_lufs: float | None = (
        round(sum(measured_vals) / len(measured_vals), 2) if measured_vals else final_lufs
    )
    tol = round(float(tolerance_lu), 2)
    deviations: list[dict[str, Any]] = []
    warnings: list[str] = []
    max_dev = 0.0

    if mean_chunk_lufs is not None and measured_vals:
        candidates: list[tuple[dict[str, Any], float, float]] = []
        for c in chunk_entries:
            c_lufs = c.get("integrated_lufs")
            if not isinstance(c_lufs, (int, float)) or not math.isfinite(float(c_lufs)):
                continue
            delta = round(float(c_lufs) - mean_chunk_lufs, 2)
            abs_delta = round(abs(float(c_lufs) - mean_chunk_lufs), 2)
            if abs_delta > max_dev:
                max_dev = abs_delta
            if abs_delta > float(tolerance_lu):
                candidates.append((c, delta, abs_delta))

        if len(measured_vals) >= 3 and len(candidates) > 1:
            worst_c, worst_delta, worst_abs = max(candidates, key=lambda item: item[2])
            rest_vals = [
                float(c["integrated_lufs"])
                for c in chunk_entries
                if c is not worst_c
                and isinstance(c.get("integrated_lufs"), (int, float))
                and math.isfinite(float(c["integrated_lufs"]))
            ]
            if rest_vals:
                rest_mean = sum(rest_vals) / len(rest_vals)
                if all(abs(v - rest_mean) <= float(tolerance_lu) for v in rest_vals):
                    candidates = [(worst_c, worst_delta, worst_abs)]

        for c, delta, abs_delta in candidates:
            c_lufs = float(c["integrated_lufs"])
            c_idx = int(c["index"])
            preview = str(c.get("text_preview") or "")
            deviations.append(
                {
                    "index": c_idx,
                    "chunk_index": c_idx,
                    "text_preview": preview,
                    "start_s": c["start_s"],
                    "end_s": c["end_s"],
                    "integrated_lufs": round(c_lufs, 2),
                    "mean_chunk_lufs": mean_chunk_lufs,
                    "delta_lu": delta,
                    "abs_delta_lu": abs_delta,
                    "tolerance_lu": tol,
                }
            )
            sign = "+" if delta >= 0 else ""
            preview_str = f" ({preview!r})" if preview else ""
            warnings.append(
                f"voiceover loudness inconsistency: chunk {c_idx}{preview_str} is "
                f"{c_lufs:.1f} LUFS ({sign}{delta:.1f} LU vs mean "
                f"{mean_chunk_lufs:.1f} LUFS, exceeds ±{float(tolerance_lu):.1f} LU tolerance)"
            )

    report: dict[str, Any] = {
        "target_lufs": round(float(target_lufs), 2),
        "chunks": chunk_entries,
        "final_lufs": final_lufs,
        "mean_chunk_lufs": mean_chunk_lufs,
        "max_deviation_lu": round(max_dev, 2),
        "tolerance_lu": tol,
        "deviations": deviations,
        "warnings": warnings,
    }
    if out_json_path is not None:
        out_p = Path(out_json_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def process_voiceover_audio(
    *,
    raw_audio_path: str | Path,
    raw_words: list[dict[str, Any]],
    out_audio_path: str | Path,
    speed: float = 1.15,
    max_sentence_silence: float = 0.25,
    raw_duration_s: float | None = None,
    min_padding_s: float | str | None = PAUSE_TRIM_DEFAULT_PADDING_S,
    noise_db: float = PAUSE_TRIM_NOISE_DB,
    min_silence_s: float = PAUSE_TRIM_MIN_SILENCE_S,
    energy_silences: list[dict[str, Any]] | None = None,
    vo_lufs: float = -16.0,
    vo_normalize: bool = False,
) -> dict[str, Any]:
    """Energy-based pause trim + speed adjustment + word-timing remap.

    Detects real silence with ffmpeg `silencedetect` (`noise=-35dB:d=0.12`) and cuts
    only at the middle of an energy-silence interval inside an alignment gap, keeping
    `>= min_padding_s` (default 0.15s; 2x around tokens aligned `> 0.6s`) on each side.
    """
    raw_p = Path(raw_audio_path)
    out_p = Path(out_audio_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    probed_dur = probe_audio_duration(raw_p)
    last_word_end = max((float(w.get("end", 0.0)) for w in raw_words), default=0.0)
    orig_dur = float(
        probed_dur
        if (probed_dur is not None and probed_dur > 0)
        else (raw_duration_s if (raw_duration_s is not None and raw_duration_s > 0) else max(1.0, last_word_end))
    )

    if energy_silences is None:
        if probed_dur is not None and probed_dur > 0:
            energy_silences = detect_energy_silences(
                raw_p,
                noise_db=noise_db,
                min_silence_s=min_silence_s,
                total_duration_s=orig_dur,
            )
        else:
            energy_silences = []

    cut_info = compute_energy_pause_cuts(
        raw_words,
        energy_silences,
        max_sentence_silence=max_sentence_silence,
        min_padding_s=min_padding_s,
    )
    cuts: list[tuple[float, float]] = cut_info["cuts"]
    long_tokens: list[dict[str, Any]] = cut_info["long_tokens"]

    timeline_end = max(orig_dur, last_word_end)
    kept: list[tuple[float, float]] = []
    cur = 0.0
    for c0, c1 in cuts:
        if c0 > cur + 1e-4:
            kept.append((cur, c0))
        cur = max(cur, c1)
    if timeline_end > cur + 1e-4:
        kept.append((cur, timeline_end))
    if not kept:
        kept = [(0.0, max(0.1, timeline_end))]

    def _remap_t(t: float) -> float:
        acc = 0.0
        for s, e in kept:
            if t < s:
                return acc
            if t <= e:
                return acc + (t - s)
            acc += e - s
        return acc

    spd = float(speed) if speed and speed > 0 else 1.0
    remapped_words: list[dict[str, Any]] = []
    for w in raw_words:
        ws = _remap_t(float(w.get("start", 0.0))) / spd
        we = _remap_t(float(w.get("end", ws))) / spd
        remapped_words.append(
            {
                "word": str(w.get("word", "")),
                "start": round(ws, 4),
                "end": round(max(ws + 0.01, we), 4),
            }
        )

    expected_dur = sum(e - s for s, e in kept) / spd

    # Perform audio processing with ffmpeg if input is a real media stream
    can_trim_media = probed_dur is not None and probed_dur >= max(0.05, last_word_end - 0.25)
    with tempfile.TemporaryDirectory(prefix="parallax-cut-vo-") as td:
        stage_audio = Path(td) / f"trimmed{raw_p.suffix or '.mp3'}"
        trimmed_ok = False
        if len(kept) > 1 and can_trim_media:
            fade = 0.008
            fc_parts: list[str] = []
            for idx, (s, e) in enumerate(kept):
                d = max(0.02, e - s)
                fo = max(0.0, d - fade)
                fc_parts.append(
                    f"[0:a]atrim=start={s:.4f}:end={e:.4f},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:st=0:d={fade},afade=t=out:st={fo:.4f}:d={fade}[a{idx}]"
                )
            inputs_concat = "".join(f"[a{idx}]" for idx in range(len(kept)))
            fc = ";".join(fc_parts) + f";{inputs_concat}concat=n={len(kept)}:v=0:a=1[out]"
            res = run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(raw_p),
                    "-filter_complex",
                    fc,
                    "-map",
                    "[out]",
                    str(stage_audio),
                ],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0 and stage_audio.is_file():
                trimmed_ok = True

        if not trimmed_ok:
            shutil.copy2(raw_p, stage_audio)

        if abs(spd - 1.0) > 1e-3 and probed_dur is not None and probed_dur > 0:
            try:
                _audio_mod.speedup(stage_audio, out_p, spd)
            except Exception:
                shutil.copy2(stage_audio, out_p)
        else:
            shutil.copy2(stage_audio, out_p)

        loudnorm_info: dict[str, Any] = {"normalized": False, "target_lufs": float(vo_lufs)}
        if vo_normalize and probed_dur is not None and probed_dur > 0:
            loudnorm_info = normalize_audio_loudness(out_p, out_p, target_lufs=vo_lufs)
        post_lufs_stats = (
            measure_audio_loudness_lufs(out_p, target_lufs=vo_lufs)
            if (probed_dur is not None and probed_dur > 0)
            else None
        )
        integrated_lufs = post_lufs_stats["input_i"] if post_lufs_stats is not None else None
        loudnorm_info["output_lufs"] = integrated_lufs

    final_probed = probe_audio_duration(out_p)
    final_dur = float(final_probed) if (final_probed is not None and final_probed > 0) else round(expected_dur, 4)

    return {
        "audio_path": str(out_p),
        "words": remapped_words,
        "total_duration_s": round(final_dur, 4),
        "cuts_count": len(cuts),
        "cuts": cuts,
        "kept_intervals": kept,
        "energy_silences": energy_silences,
        "long_tokens": long_tokens,
        "padding_s": cut_info["padding_s"],
        "speed": spd,
        "loudnorm": loudnorm_info,
        "integrated_lufs": integrated_lufs,
    }


def _invoke_voice_provider(
    provider: Any,
    text: str,
    voice: str,
    style: str | None,
    out_dir: str | Path,
    default_cost_usd: float = 0.0,
) -> VoiceSynthesisResult:
    """Invoke `provider.synthesize(...)` with or without `out_dir` kwarg and normalize its return value."""
    fn = getattr(provider, "synthesize", provider)
    kwargs: dict[str, Any] = {}
    try:
        sig = inspect.signature(fn)
        params = sig.parameters
        if "out_dir" in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            kwargs["out_dir"] = str(out_dir)
    except (ValueError, TypeError):
        pass
    raw_res = fn(text, voice, style, **kwargs)
    return coerce_synthesis_result(raw_res, default_cost_usd=default_cost_usd)


def concatenate_chunk_voiceovers(
    proc_chunks: list[dict[str, Any]],
    out_audio_path: str | Path,
    *,
    vo_gap: float = 0.25,
) -> dict[str, Any]:
    """Concatenate processed voiceover chunks with `vo_gap` silence and offset word timings."""
    out_p = Path(out_audio_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    gap = max(0.0, float(vo_gap))

    if not proc_chunks:
        return {"audio_path": str(out_p), "words": [], "total_duration_s": 0.0}

    if len(proc_chunks) == 1:
        c0 = proc_chunks[0]
        src_p = Path(c0["audio_path"])
        if src_p.is_file() and src_p.resolve() != out_p.resolve():
            shutil.copy2(src_p, out_p)
        words_0 = [
            {
                "word": str(w.get("word", "")),
                "start": round(float(w.get("start", 0.0)), 4),
                "end": round(float(w.get("end", 0.0)), 4),
            }
            for w in (c0.get("words") or [])
        ]
        dur_0 = float(c0.get("total_duration_s") or c0.get("duration_s") or 0.0)
        return {
            "audio_path": str(out_p),
            "words": words_0,
            "total_duration_s": round(dur_0, 4),
        }

    combined_words: list[dict[str, Any]] = []
    offset_s = 0.0
    n_chunks = len(proc_chunks)
    chunk_paths: list[Path] = []
    all_real_media = True

    for idx, ch in enumerate(proc_chunks):
        cp = Path(ch["audio_path"])
        chunk_paths.append(cp)
        probed_d = probe_audio_duration(cp) if cp.is_file() else None
        if probed_d is None or probed_d <= 0:
            all_real_media = False
        c_words = ch.get("words") or []
        c_dur = float(
            probed_d
            if (probed_d is not None and probed_d > 0)
            else (
                ch.get("total_duration_s")
                or ch.get("duration_s")
                or max((float(w.get("end", 0.0)) for w in c_words), default=1.0)
            )
        )
        for w in c_words:
            ws = float(w.get("start", 0.0)) + offset_s
            we = float(w.get("end", 0.0)) + offset_s
            combined_words.append(
                {
                    "word": str(w.get("word", "")),
                    "start": round(ws, 4),
                    "end": round(max(ws + 0.01, we), 4),
                }
            )
        offset_s += c_dur + (gap if idx < n_chunks - 1 else 0.0)

    if all_real_media:
        cmd: list[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        fc_parts: list[str] = []
        for idx, cp in enumerate(chunk_paths):
            cmd += ["-i", str(cp)]
            if idx < n_chunks - 1 and gap > 0:
                fc_parts.append(f"[{idx}:a]aresample=44100,apad=pad_dur={gap:.4f}[c{idx}]")
            else:
                fc_parts.append(f"[{idx}:a]aresample=44100[c{idx}]")
        concat_in = "".join(f"[c{idx}]" for idx in range(n_chunks))
        fc = ";".join(fc_parts) + f";{concat_in}concat=n={n_chunks}:v=0:a=1[out]"
        cmd += ["-filter_complex", fc, "-map", "[out]", str(out_p)]
        res = run_ffmpeg(cmd, capture_output=True, text=True)
        if res.returncode != 0 or not out_p.is_file():
            shutil.copy2(chunk_paths[0], out_p)
    else:
        if chunk_paths[0].is_file():
            shutil.copy2(chunk_paths[0], out_p)

    probed_total = probe_audio_duration(out_p) if all_real_media else None
    total_dur = float(probed_total) if (probed_total is not None and probed_total > 0) else offset_s
    return {
        "audio_path": str(out_p),
        "words": combined_words,
        "total_duration_s": round(total_dur, 4),
    }


def diff_script_vs_transcript(
    script_text: str,
    heard_words: list[dict[str, Any]] | str,
    *,
    min_similarity: float = 0.97,
    max_missing_run: int = 2,
) -> dict[str, Any]:
    """Compare normalized script words against normalized transcript words via `difflib`.

    Flags missing/extra word runs with approximate timestamps and returns `passed=False`
    when any missing run has `>= max_missing_run` (2) words or similarity `< min_similarity` (0.97).
    """
    expected = normalize_text_for_verify(script_text)
    actual: list[str] = []
    actual_times: list[tuple[float, float]] = []

    if isinstance(heard_words, str):
        for w in normalize_text_for_verify(heard_words):
            actual.append(w)
            actual_times.append((0.0, 0.0))
    else:
        for hw in heard_words or []:
            if isinstance(hw, str):
                norm_toks = normalize_text_for_verify(hw)
                for nt in norm_toks:
                    actual.append(nt)
                    actual_times.append((0.0, 0.0))
            else:
                w_str = str(hw.get("word", ""))
                ws = float(hw.get("start", 0.0) or 0.0)
                we = float(hw.get("end", ws) or ws)
                norm_toks = normalize_text_for_verify(w_str)
                for nt in norm_toks:
                    actual.append(nt)
                    actual_times.append((ws, we))

    if not expected:
        return {
            "status": "passed",
            "passed": True,
            "skipped": False,
            "similarity": 1.0,
            "expected_words": [],
            "actual_words": actual,
            "missing_runs": [],
            "extra_runs": [],
            "max_missing_run": 0,
        }

    sm = difflib.SequenceMatcher(None, expected, actual, autojunk=False)
    similarity = round(float(sm.ratio()), 4)
    missing_runs: list[dict[str, Any]] = []
    extra_runs: list[dict[str, Any]] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if j2 > j1 and actual_times:
            t_start = actual_times[j1][0]
            t_end = actual_times[j2 - 1][1]
        elif actual_times:
            t_start = actual_times[j1 - 1][1] if j1 > 0 else 0.0
            t_end = actual_times[j1][0] if j1 < len(actual_times) else t_start
        else:
            t_start, t_end = 0.0, 0.0

        if i2 > i1:
            m_toks = expected[i1:i2]
            missing_runs.append(
                {
                    "words": m_toks,
                    "text": " ".join(m_toks),
                    "count": len(m_toks),
                    "start_s": round(t_start, 3),
                    "end_s": round(t_end, 3),
                    "script_span": [i1, i2],
                    "replaced_by": actual[j1:j2] if j2 > j1 else [],
                }
            )
        if j2 > j1:
            e_toks = actual[j1:j2]
            extra_runs.append(
                {
                    "words": e_toks,
                    "text": " ".join(e_toks),
                    "count": len(e_toks),
                    "start_s": round(t_start, 3),
                    "end_s": round(t_end, 3),
                }
            )

    max_missing_len = max((r["count"] for r in missing_runs), default=0)
    passed = bool(max_missing_len < int(max_missing_run) and similarity >= float(min_similarity))
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "skipped": False,
        "similarity": similarity,
        "expected_words": expected,
        "actual_words": actual,
        "missing_runs": missing_runs,
        "extra_runs": extra_runs,
        "max_missing_run": max_missing_len,
    }


def verify_audio_against_script(
    audio_path: str | Path,
    script_text: str,
    *,
    transcriber: Any | None = None,
    min_similarity: float = 0.97,
    max_missing_run: int = 2,
    label: str = "",
) -> dict[str, Any]:
    """Transcribe `audio_path` via `whisper_backend.transcribe_wav` (or `transcriber`) and verify
    against `script_text`. If whisperx is unavailable, returns a `status='skipped'` report without crashing.
    """
    fn = transcriber
    if fn is None:
        from . import whisper_backend as _wb

        fn = _wb.transcribe_wav

    try:
        kwargs: dict[str, Any] = {}
        try:
            sig = inspect.signature(fn)
            if "label" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                kwargs["label"] = label
        except (ValueError, TypeError):
            pass
        heard = fn(str(audio_path), **kwargs)
    except Exception as exc:
        return {
            "status": "skipped",
            "passed": True,
            "skipped": True,
            "note": f"verification skipped ({exc})",
            "similarity": 1.0,
            "expected_words": normalize_text_for_verify(script_text),
            "actual_words": [],
            "missing_runs": [],
            "extra_runs": [],
            "max_missing_run": 0,
        }

    return diff_script_vs_transcript(
        script_text,
        heard,
        min_similarity=min_similarity,
        max_missing_run=max_missing_run,
    )


def _trim_introduced_mismatch(raw_verify: dict[str, Any], proc_verify: dict[str, Any]) -> bool:
    """Return True if `proc_verify` has a missing word run or failure not present in `raw_verify`."""
    if raw_verify.get("skipped") or proc_verify.get("skipped"):
        return False
    raw_missing = {tuple(r["words"]) for r in (raw_verify.get("missing_runs") or [])}
    proc_missing = [tuple(r["words"]) for r in (proc_verify.get("missing_runs") or [])]
    if any(m not in raw_missing for m in proc_missing):
        return True
    if raw_verify.get("passed", True) and not proc_verify.get("passed", True):
        return True
    return False


def _format_residual_mismatch_summary(residual: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in residual:
        loc = f"chunk {item['chunk_index']}" if item.get("chunk_index") is not None else "final"
        runs = item.get("missing_runs") or []
        if runs:
            run_strs = [f"missing '{r['text']}' (@{r['start_s']:.2f}s)" for r in runs]
            parts.append(f"{loc} (sim={item.get('similarity', 0.0):.3f}: {', '.join(run_strs)})")
        else:
            parts.append(f"{loc} (sim={item.get('similarity', 0.0):.3f})")
    return "; ".join(parts)


def resolve_or_generate_voiceover(
    *,
    clean_script: str,
    project_dir: Path,
    audio_path: str | Path | None = None,
    words_path: str | Path | None = None,
    process_audio: bool = False,
    voice: str = "nova",
    voice_model: str = "tts-mini",
    style: str | None = None,
    speed: float = 1.15,
    max_sentence_silence: float = 0.25,
    vo_chunk_seconds: float = 60.0,
    vo_gap: float = 0.25,
    vo_lufs: float = -16.0,
    vo_lufs_tolerance: float = 1.5,
    vo_normalize: bool = True,
    vo_verify: bool = True,
    vo_retries: int = 2,
    strict_vo: bool = False,
    provider: Any | None = None,
    transcriber: Any | None = None,
    force: bool = False,
    quiet: bool = False,
) -> dict[str, Any]:
    """Obtain voiceover audio + word timings from supplied files, cache, or chunked TTS.

    Implements per-chunk raw (`<project>/cache/vo/chunks/<sha256>.raw.{mp3,json}`) and
    processed (`<sha256>.proc.{mp3,json}`) caches, script-vs-audio verification with
    per-chunk regeneration retries (`--vo-retries`), trim-margin escalation on trim-induced
    mismatches, and per-chunk loudness consistency reporting.
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    loudness_json_path = project_dir / "voiceover.loudness.json"
    verify_json_path = project_dir / "voiceover.verify.json"

    # Path 1: User supplied --audio
    if audio_path is not None:
        src_audio = Path(audio_path).expanduser().resolve()
        if not src_audio.is_file():
            raise CutError(f"Supplied audio file not found: {src_audio}", exit_code=1)

        if words_path is not None:
            src_words = Path(words_path).expanduser().resolve()
            if not src_words.is_file():
                raise CutError(f"Supplied words file not found: {src_words}", exit_code=1)
            payload = json.loads(src_words.read_text(encoding="utf-8"))
            raw_words = payload if isinstance(payload, list) else list(payload.get("words") or [])
        else:
            # Optional forced alignment via forced_align
            try:
                from .forced_align import align_words

                raw_words = align_words(str(src_audio), clean_script)
            except Exception as exc:
                raise CutError(
                    f"Audio was supplied without --words and forced alignment (whisperx) is unavailable: {exc}\n"
                    f"Supply word timings via `--words FILE.json`.",
                    exit_code=1,
                ) from exc

        ext = src_audio.suffix.lower() or ".mp3"
        dst_audio = project_dir / f"voiceover{ext}"
        trim_escalations: list[dict[str, Any]] = []
        residual_mismatches: list[dict[str, Any]] = []

        if process_audio:
            raw_copy = project_dir / f"voiceover.provider-raw{ext}"
            if src_audio != raw_copy:
                shutil.copy2(src_audio, raw_copy)

            raw_verify = (
                verify_audio_against_script(raw_copy, clean_script, transcriber=transcriber, label="supplied raw")
                if vo_verify
                else {"status": "disabled", "passed": True, "skipped": True, "missing_runs": [], "extra_runs": []}
            )

            proc = process_voiceover_audio(
                raw_audio_path=raw_copy,
                raw_words=raw_words,
                out_audio_path=dst_audio,
                speed=speed,
                max_sentence_silence=max_sentence_silence,
                min_padding_s=PAUSE_TRIM_ESCALATION_LEVELS[0],
                vo_lufs=vo_lufs,
                vo_normalize=vo_normalize,
            )

            final_verify = (
                verify_audio_against_script(dst_audio, clean_script, transcriber=transcriber, label="supplied trimmed")
                if vo_verify
                else {"status": "disabled", "passed": True, "skipped": True, "missing_runs": [], "extra_runs": []}
            )

            if vo_verify and proc["cuts_count"] > 0 and _trim_introduced_mismatch(raw_verify, final_verify):
                for esc_pad in PAUSE_TRIM_ESCALATION_LEVELS[1:]:
                    proc = process_voiceover_audio(
                        raw_audio_path=raw_copy,
                        raw_words=raw_words,
                        out_audio_path=dst_audio,
                        speed=speed,
                        max_sentence_silence=max_sentence_silence,
                        min_padding_s=esc_pad,
                        vo_lufs=vo_lufs,
                        vo_normalize=vo_normalize,
                    )
                    final_verify = verify_audio_against_script(
                        dst_audio,
                        clean_script,
                        transcriber=transcriber,
                        label=f"supplied trimmed (padding={esc_pad})",
                    )
                    trim_escalations.append(
                        {
                            "chunk_index": 0,
                            "padding_s": esc_pad,
                            "passed": final_verify.get("passed", True),
                        }
                    )
                    if not _trim_introduced_mismatch(raw_verify, final_verify):
                        break

            if vo_verify and not final_verify.get("skipped") and not final_verify.get("passed", True):
                residual_mismatches.append({"chunk_index": None, **final_verify})

            verify_report = {
                "enabled": bool(vo_verify),
                "status": final_verify.get("status", "disabled"),
                "skipped": bool(final_verify.get("skipped", False)),
                "note": final_verify.get("note"),
                "passed": len(residual_mismatches) == 0,
                "retries_used": 0,
                "trim_escalations": trim_escalations,
                "residual_mismatches": residual_mismatches,
                "final": final_verify,
            }
            verify_json_path.write_text(json.dumps(verify_report, indent=2), encoding="utf-8")
            loudness_report = analyze_voiceover_loudness(
                dst_audio,
                chunks=[
                    {
                        "index": 0,
                        "text": clean_script,
                        "start_s": 0.0,
                        "end_s": proc["total_duration_s"],
                        "audio_path": str(dst_audio),
                        "integrated_lufs": proc.get("integrated_lufs"),
                    }
                ],
                target_lufs=vo_lufs,
                tolerance_lu=vo_lufs_tolerance,
                vo_gap=vo_gap,
                out_json_path=loudness_json_path,
            )
            if strict_vo and residual_mismatches:
                raise CutError(
                    f"Voiceover verification failed (--strict-vo): {_format_residual_mismatch_summary(residual_mismatches)}",
                    exit_code=1,
                    report={"status": "vo_verify_failed", "verification": verify_report},
                )
            return {
                "source": "supplied",
                "audio_path": str(dst_audio),
                "raw_audio_path": str(raw_copy),
                "words": proc["words"],
                "duration_s": proc["total_duration_s"],
                "cost_usd": 0.0,
                "tts_calls": 0,
                "chunks_count": 1,
                "loudness": loudness_report,
                "loudness_report_path": str(loudness_json_path),
                "verification": verify_report,
                "verify_report_path": str(verify_json_path),
            }
        else:
            if src_audio != dst_audio:
                shutil.copy2(src_audio, dst_audio)
            dur = probe_audio_duration(dst_audio)
            if dur is None or dur <= 0:
                dur = max((float(w.get("end", 0.0)) for w in raw_words), default=1.0)
            final_verify = (
                verify_audio_against_script(dst_audio, clean_script, transcriber=transcriber, label="supplied audio")
                if vo_verify
                else {"status": "disabled", "passed": True, "skipped": True, "missing_runs": [], "extra_runs": []}
            )
            if vo_verify and not final_verify.get("skipped") and not final_verify.get("passed", True):
                residual_mismatches.append({"chunk_index": None, **final_verify})
            verify_report = {
                "enabled": bool(vo_verify),
                "status": final_verify.get("status", "disabled"),
                "skipped": bool(final_verify.get("skipped", False)),
                "note": final_verify.get("note"),
                "passed": len(residual_mismatches) == 0,
                "retries_used": 0,
                "trim_escalations": [],
                "residual_mismatches": residual_mismatches,
                "final": final_verify,
            }
            verify_json_path.write_text(json.dumps(verify_report, indent=2), encoding="utf-8")
            loudness_report = analyze_voiceover_loudness(
                dst_audio,
                chunks=[
                    {
                        "index": 0,
                        "text": clean_script,
                        "start_s": 0.0,
                        "end_s": float(dur),
                        "audio_path": str(dst_audio),
                    }
                ],
                target_lufs=vo_lufs,
                tolerance_lu=vo_lufs_tolerance,
                vo_gap=vo_gap,
                out_json_path=loudness_json_path,
            )
            if strict_vo and residual_mismatches:
                raise CutError(
                    f"Voiceover verification failed (--strict-vo): {_format_residual_mismatch_summary(residual_mismatches)}",
                    exit_code=1,
                    report={"status": "vo_verify_failed", "verification": verify_report},
                )
            return {
                "source": "supplied",
                "audio_path": str(dst_audio),
                "raw_audio_path": str(src_audio),
                "words": raw_words,
                "duration_s": round(float(dur), 4),
                "cost_usd": 0.0,
                "tts_calls": 0,
                "chunks_count": 1,
                "loudness": loudness_report,
                "loudness_report_path": str(loudness_json_path),
                "verification": verify_report,
                "verify_report_path": str(verify_json_path),
            }

    # Path 2: Chunked TTS with per-chunk raw + processed cache under <project>/cache/vo/chunks/
    cache_dir = project_dir / "cache" / "vo"
    chunks_cache_dir = cache_dir / "chunks"
    chunks_cache_dir.mkdir(parents=True, exist_ok=True)

    raw_key, proc_key = compute_vo_cache_keys(
        script_text=clean_script,
        voice=voice,
        voice_model=voice_model,
        style=style,
        speed=speed,
        max_sentence_silence=max_sentence_silence,
        vo_chunk_seconds=vo_chunk_seconds,
        vo_gap=vo_gap,
        vo_lufs=vo_lufs,
        vo_normalize=vo_normalize,
    )

    proc_audio_cache = cache_dir / f"{proc_key}.mp3"
    proc_words_cache = cache_dir / f"{proc_key}.words.json"
    raw_audio_cache = cache_dir / f"raw_{raw_key}.mp3"
    raw_words_cache = cache_dir / f"raw_{raw_key}.words.json"

    dst_audio = project_dir / "voiceover.mp3"
    dst_raw_audio = project_dir / "voiceover.provider-raw.mp3"

    chunks = chunk_script(clean_script, max_chunk_seconds=vo_chunk_seconds, speed=speed)
    if not chunks:
        chunks = [" ".join(clean_script.split())]

    # Tier 1: Full-script processed cache hit
    if not force and proc_audio_cache.is_file() and proc_words_cache.is_file():
        if not quiet:
            print("voiceover: cached (no TTS call)", flush=True)
        shutil.copy2(proc_audio_cache, dst_audio)
        if raw_audio_cache.is_file():
            shutil.copy2(raw_audio_cache, dst_raw_audio)
        cached_payload = json.loads(proc_words_cache.read_text(encoding="utf-8"))
        cached_words = cached_payload if isinstance(cached_payload, list) else list(cached_payload.get("words") or [])
        dur = cached_payload.get("total_duration_s") if isinstance(cached_payload, dict) else None
        if not dur:
            dur = probe_audio_duration(dst_audio) or max((float(w.get("end", 0.0)) for w in cached_words), default=1.0)
        cached_proc_chunks: list[dict[str, Any]] = []
        for chunk_idx, chunk_text in enumerate(chunks):
            _, c_proc_key = compute_chunk_cache_keys(
                chunk_text=chunk_text,
                voice=voice,
                voice_model=voice_model,
                style=style,
                speed=speed,
                max_sentence_silence=max_sentence_silence,
                vo_lufs=vo_lufs,
                vo_normalize=vo_normalize,
            )
            c_proc_mp3 = chunks_cache_dir / f"{c_proc_key}.proc.mp3"
            c_proc_json = chunks_cache_dir / f"{c_proc_key}.proc.json"
            cp_dur: float | None = None
            cp_lufs: float | None = None
            if c_proc_json.is_file():
                cp_data = json.loads(c_proc_json.read_text(encoding="utf-8"))
                if isinstance(cp_data, dict):
                    cp_dur = cp_data.get("total_duration_s")
                    cp_lufs = cp_data.get("integrated_lufs")
            if cp_dur is None and c_proc_mp3.is_file():
                cp_dur = probe_audio_duration(c_proc_mp3)
            cached_proc_chunks.append(
                {
                    "chunk_index": chunk_idx,
                    "text": chunk_text,
                    "audio_path": (
                        str(c_proc_mp3)
                        if c_proc_mp3.is_file()
                        else (str(dst_audio) if len(chunks) == 1 else None)
                    ),
                    "total_duration_s": (
                        float(cp_dur)
                        if cp_dur is not None
                        else (float(dur) if len(chunks) == 1 else 0.0)
                    ),
                    "integrated_lufs": cp_lufs,
                }
            )
        loudness_report = analyze_voiceover_loudness(
            dst_audio,
            chunks=cached_proc_chunks,
            target_lufs=vo_lufs,
            tolerance_lu=vo_lufs_tolerance,
            vo_gap=vo_gap,
            out_json_path=loudness_json_path,
        )
        verify_report = (
            json.loads(verify_json_path.read_text(encoding="utf-8"))
            if verify_json_path.is_file()
            else {"enabled": bool(vo_verify), "status": "cached", "skipped": True, "passed": True}
        )
        return {
            "source": "cached",
            "cache_tier": "processed",
            "cache_key": proc_key,
            "raw_cache_key": raw_key,
            "audio_path": str(dst_audio),
            "raw_audio_path": str(dst_raw_audio) if dst_raw_audio.is_file() else str(dst_audio),
            "words": cached_words,
            "duration_s": round(float(dur), 4),
            "cost_usd": 0.0,
            "tts_calls": 0,
            "chunks_count": len(chunks),
            "loudness": loudness_report,
            "loudness_report_path": str(loudness_json_path),
            "verification": verify_report,
            "verify_report_path": str(verify_json_path) if verify_json_path.is_file() else None,
        }

    # Resolve VoiceProvider lazily only if needed
    spec = TTS_MODELS.get(voice_model)

    def _get_provider() -> Any:
        nonlocal provider
        if provider is None:
            from . import voiceover as _vo_mod

            factory = (
                get_voice_provider
                if get_voice_provider is not _vo_mod.get_voice_provider
                else _vo_mod.get_voice_provider
            )
            provider = factory(voice_model=voice_model)
        return provider

    proc_chunks: list[dict[str, Any]] = []
    tts_calls = 0
    retries_used = 0
    total_cost_usd = 0.0
    any_raw_reprocessed = False
    chunk_verify_reports: list[dict[str, Any]] = []
    trim_escalations = []
    residual_mismatches = []
    max_retries = max(0, int(vo_retries))

    for chunk_idx, chunk_text in enumerate(chunks):
        c_raw_key, c_proc_key = compute_chunk_cache_keys(
            chunk_text=chunk_text,
            voice=voice,
            voice_model=voice_model,
            style=style,
            speed=speed,
            max_sentence_silence=max_sentence_silence,
            vo_lufs=vo_lufs,
            vo_normalize=vo_normalize,
        )
        c_raw_mp3 = chunks_cache_dir / f"{c_raw_key}.raw.mp3"
        c_raw_json = chunks_cache_dir / f"{c_raw_key}.raw.json"
        c_proc_mp3 = chunks_cache_dir / f"{c_proc_key}.proc.mp3"
        c_proc_json = chunks_cache_dir / f"{c_proc_key}.proc.json"

        # Per-chunk Tier 1: processed chunk cache hit
        if not force and c_proc_mp3.is_file() and c_proc_json.is_file():
            cp_data = json.loads(c_proc_json.read_text(encoding="utf-8"))
            cp_words = cp_data if isinstance(cp_data, list) else list(cp_data.get("words") or [])
            cp_dur = float(
                (cp_data.get("total_duration_s") if isinstance(cp_data, dict) else None)
                or probe_audio_duration(c_proc_mp3)
                or max((float(w.get("end", 0.0)) for w in cp_words), default=1.0)
            )
            cp_lufs = cp_data.get("integrated_lufs") if isinstance(cp_data, dict) else None
            if chunk_idx == 0 and c_raw_mp3.is_file():
                shutil.copy2(c_raw_mp3, dst_raw_audio)
            proc_chunks.append(
                {
                    "audio_path": str(c_proc_mp3),
                    "words": cp_words,
                    "total_duration_s": round(cp_dur, 4),
                    "chunk_index": chunk_idx,
                    "text": chunk_text,
                    "integrated_lufs": cp_lufs,
                    "raw_key": c_raw_key,
                    "proc_key": c_proc_key,
                }
            )
            continue

        # Per-chunk Tier 2: raw chunk cache hit (or legacy single-chunk raw_<raw_key> cache hit)
        if not force and not (c_raw_mp3.is_file() and c_raw_json.is_file()):
            if len(chunks) == 1 and raw_audio_cache.is_file() and raw_words_cache.is_file():
                shutil.copy2(raw_audio_cache, c_raw_mp3)
                shutil.copy2(raw_words_cache, c_raw_json)

        def _synth_into_raw_cache() -> tuple[list[dict[str, Any]], float]:
            nonlocal tts_calls, total_cost_usd
            chunk_est = float(spec.estimate_tts_usd(len(chunk_text)) if spec else 0.0)
            if not is_test_mode() and not quiet:
                print_estimate(
                    f"parallax cut --voice-model {voice_model} (chunk {chunk_idx + 1}/{len(chunks)})",
                    chunk_est,
                )
            prov = _get_provider()
            with tempfile.TemporaryDirectory(prefix="parallax-cut-tts-") as tmp_dir:
                synth = _invoke_voice_provider(
                    prov,
                    chunk_text,
                    voice,
                    style,
                    tmp_dir,
                    default_cost_usd=chunk_est,
                )
                tts_calls += 1
                total_cost_usd += float(synth.cost_usd)
                raw_src = Path(synth.audio_path)
                r_words = list(synth.words)
                r_dur = float(synth.duration_s or (r_words[-1]["end"] if r_words else 0.0))
                shutil.copy2(raw_src, c_raw_mp3)
                c_raw_json.write_text(
                    json.dumps({"words": r_words, "total_duration_s": r_dur}, indent=2),
                    encoding="utf-8",
                )
                if len(chunks) == 1:
                    shutil.copy2(raw_src, raw_audio_cache)
                    raw_words_cache.write_text(
                        json.dumps({"words": r_words, "total_duration_s": r_dur}, indent=2),
                        encoding="utf-8",
                    )
                return r_words, r_dur

        synthesized_this_chunk = False
        if not force and c_raw_mp3.is_file() and c_raw_json.is_file():
            any_raw_reprocessed = True
            raw_payload = json.loads(c_raw_json.read_text(encoding="utf-8"))
            raw_words = raw_payload if isinstance(raw_payload, list) else list(raw_payload.get("words") or [])
            raw_dur = float(
                (raw_payload.get("total_duration_s") if isinstance(raw_payload, dict) else None) or 0.0
            )
        else:
            raw_words, raw_dur = _synth_into_raw_cache()
            synthesized_this_chunk = True

        # Per-chunk verification + retries (only when vo_verify is enabled)
        raw_verify: dict[str, Any] = {
            "status": "disabled",
            "passed": True,
            "skipped": True,
            "missing_runs": [],
            "extra_runs": [],
        }
        chunk_retries = 0
        if vo_verify:
            raw_verify = verify_audio_against_script(
                c_raw_mp3,
                chunk_text,
                transcriber=transcriber,
                label=f"chunk {chunk_idx} raw",
            )
            while (
                not raw_verify.get("skipped")
                and not raw_verify.get("passed", True)
                and chunk_retries < max_retries
                and (synthesized_this_chunk or provider is not None)
            ):
                chunk_retries += 1
                retries_used += 1
                # Preserve rejected raw audio under cache with `.rejected` suffix and bypass raw cache entry
                rej_mp3 = chunks_cache_dir / f"{c_raw_key}.raw.mp3.rejected"
                rej_attempt_mp3 = chunks_cache_dir / f"{c_raw_key}.attempt{chunk_retries}.raw.mp3.rejected"
                if c_raw_mp3.is_file():
                    shutil.copy2(c_raw_mp3, rej_mp3)
                    shutil.copy2(c_raw_mp3, rej_attempt_mp3)
                    c_raw_mp3.unlink(missing_ok=True)
                c_raw_json.unlink(missing_ok=True)

                raw_words, raw_dur = _synth_into_raw_cache()
                raw_verify = verify_audio_against_script(
                    c_raw_mp3,
                    chunk_text,
                    transcriber=transcriber,
                    label=f"chunk {chunk_idx} retry {chunk_retries}",
                )

        if chunk_idx == 0 and c_raw_mp3.is_file():
            shutil.copy2(c_raw_mp3, dst_raw_audio)

        proc_c = process_voiceover_audio(
            raw_audio_path=c_raw_mp3,
            raw_words=raw_words,
            out_audio_path=c_proc_mp3,
            speed=speed,
            max_sentence_silence=max_sentence_silence,
            raw_duration_s=raw_dur if raw_dur > 0 else None,
            min_padding_s=PAUSE_TRIM_ESCALATION_LEVELS[0],
            vo_lufs=vo_lufs,
            vo_normalize=vo_normalize,
        )

        proc_verify = raw_verify
        if vo_verify and not raw_verify.get("skipped"):
            if proc_c["cuts_count"] > 0:
                proc_verify = verify_audio_against_script(
                    c_proc_mp3,
                    chunk_text,
                    transcriber=transcriber,
                    label=f"chunk {chunk_idx} processed",
                )
                if _trim_introduced_mismatch(raw_verify, proc_verify):
                    for esc_pad in PAUSE_TRIM_ESCALATION_LEVELS[1:]:
                        proc_c = process_voiceover_audio(
                            raw_audio_path=c_raw_mp3,
                            raw_words=raw_words,
                            out_audio_path=c_proc_mp3,
                            speed=speed,
                            max_sentence_silence=max_sentence_silence,
                            raw_duration_s=raw_dur if raw_dur > 0 else None,
                            min_padding_s=esc_pad,
                            vo_lufs=vo_lufs,
                            vo_normalize=vo_normalize,
                        )
                        proc_verify = verify_audio_against_script(
                            c_proc_mp3,
                            chunk_text,
                            transcriber=transcriber,
                            label=f"chunk {chunk_idx} processed (padding={esc_pad})",
                        )
                        trim_escalations.append(
                            {
                                "chunk_index": chunk_idx,
                                "padding_s": esc_pad,
                                "passed": proc_verify.get("passed", True),
                            }
                        )
                        if not _trim_introduced_mismatch(raw_verify, proc_verify):
                            break

            chunk_rep = {
                "chunk_index": chunk_idx,
                "retries": chunk_retries,
                **proc_verify,
            }
            chunk_verify_reports.append(chunk_rep)
            if not proc_verify.get("passed", True):
                residual_mismatches.append(chunk_rep)
        elif vo_verify and raw_verify.get("skipped"):
            chunk_verify_reports.append({"chunk_index": chunk_idx, "retries": 0, **raw_verify})

        c_proc_json.write_text(
            json.dumps(
                {
                    "words": proc_c["words"],
                    "total_duration_s": proc_c["total_duration_s"],
                    "integrated_lufs": proc_c.get("integrated_lufs"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        proc_chunks.append(
            {
                "audio_path": str(c_proc_mp3),
                "words": proc_c["words"],
                "total_duration_s": proc_c["total_duration_s"],
                "chunk_index": chunk_idx,
                "text": chunk_text,
                "integrated_lufs": proc_c.get("integrated_lufs"),
                "raw_key": c_raw_key,
                "proc_key": c_proc_key,
            }
        )

    combined = concatenate_chunk_voiceovers(proc_chunks, proc_audio_cache, vo_gap=vo_gap)
    proc_words_cache.write_text(
        json.dumps({"words": combined["words"], "total_duration_s": combined["total_duration_s"]}, indent=2),
        encoding="utf-8",
    )
    if proc_audio_cache.is_file():
        shutil.copy2(proc_audio_cache, dst_audio)

    # Final verification on combined processed voiceover
    if vo_verify:
        if len(chunks) == 1 and chunk_verify_reports:
            final_verify = dict(chunk_verify_reports[0])
        else:
            final_verify = verify_audio_against_script(
                dst_audio,
                clean_script,
                transcriber=transcriber,
                label="final voiceover",
            )
            if (
                not final_verify.get("skipped")
                and not final_verify.get("passed", True)
                and not residual_mismatches
            ):
                residual_mismatches.append({"chunk_index": None, **final_verify})
    else:
        final_verify = {"status": "disabled", "passed": True, "skipped": True, "missing_runs": [], "extra_runs": []}

    verify_skipped = bool(final_verify.get("skipped", False))
    verify_report = {
        "enabled": bool(vo_verify),
        "status": (
            "skipped"
            if verify_skipped
            else ("passed" if len(residual_mismatches) == 0 else "failed")
        ),
        "skipped": verify_skipped,
        "note": final_verify.get("note"),
        "passed": len(residual_mismatches) == 0,
        "retries_used": retries_used,
        "trim_escalations": trim_escalations,
        "residual_mismatches": residual_mismatches,
        "chunks": chunk_verify_reports,
        "final": final_verify,
    }
    verify_json_path.write_text(json.dumps(verify_report, indent=2), encoding="utf-8")
    loudness_report = analyze_voiceover_loudness(
        dst_audio,
        chunks=proc_chunks,
        target_lufs=vo_lufs,
        tolerance_lu=vo_lufs_tolerance,
        vo_gap=vo_gap,
        out_json_path=loudness_json_path,
    )

    if strict_vo and residual_mismatches:
        raise CutError(
            f"Voiceover verification failed (--strict-vo): {_format_residual_mismatch_summary(residual_mismatches)}",
            exit_code=1,
            report={
                "status": "vo_verify_failed",
                "verification": verify_report,
                "tts_calls": tts_calls,
                "tts_cost_usd": round(float(total_cost_usd), 6),
            },
        )

    if tts_calls == 0:
        if not quiet:
            msg = (
                "voiceover: cached (no TTS call) [re-processing from raw cache]"
                if any_raw_reprocessed
                else "voiceover: cached (no TTS call)"
            )
            print(msg, flush=True)
        return {
            "source": "cached",
            "cache_tier": "raw" if any_raw_reprocessed else "processed",
            "cache_key": proc_key,
            "raw_cache_key": raw_key,
            "audio_path": str(dst_audio),
            "raw_audio_path": str(dst_raw_audio) if dst_raw_audio.is_file() else str(dst_audio),
            "words": combined["words"],
            "duration_s": combined["total_duration_s"],
            "cost_usd": 0.0,
            "tts_calls": 0,
            "retries_used": retries_used,
            "chunks_count": len(chunks),
            "loudness": loudness_report,
            "loudness_report_path": str(loudness_json_path),
            "verification": verify_report,
            "verify_report_path": str(verify_json_path),
        }

    return {
        "source": "tts",
        "cache_tier": "miss",
        "cache_key": proc_key,
        "raw_cache_key": raw_key,
        "audio_path": str(dst_audio),
        "raw_audio_path": str(dst_raw_audio) if dst_raw_audio.is_file() else str(dst_audio),
        "words": combined["words"],
        "duration_s": combined["total_duration_s"],
        "cost_usd": round(float(total_cost_usd), 6),
        "tts_calls": tts_calls,
        "retries_used": retries_used,
        "chunks_count": len(chunks),
        "loudness": loudness_report,
        "loudness_report_path": str(loudness_json_path),
        "verification": verify_report,
        "verify_report_path": str(verify_json_path),
    }


def estimate_word_timings_from_script(
    clean_script: str,
    *,
    speed: float = 1.15,
    base_wpm: float = 155.0,
) -> tuple[list[dict[str, Any]], float]:
    """Generate rough estimated word timings from script token lengths (used in `--plan-only` with no audio)."""
    tokens = tokenize_script(clean_script)
    if not tokens:
        return [], 0.0
    effective_wpm = max(60.0, base_wpm * max(0.5, float(speed)))
    avg_word_s = 60.0 / effective_wpm
    weights = [max(2, len(norm_token(t))) for t in tokens]
    mean_w = (sum(weights) / len(weights)) or 4.0

    words: list[dict[str, Any]] = []
    cur = 0.05
    for tok, w in zip(tokens, weights):
        dur = max(0.12, avg_word_s * (0.6 + 0.4 * (w / mean_w)))
        start = cur
        end = start + dur
        words.append({"word": tok, "start": round(start, 4), "end": round(end, 4)})
        # Add small inter-sentence gap after terminal punctuation
        if re.search(r"[.!?][\"'”’)]*$", tok):
            cur = end + 0.22 / max(0.5, float(speed))
        elif re.search(r"[,;:—–]$", tok):
            cur = end + 0.10 / max(0.5, float(speed))
        else:
            cur = end + 0.02
    total_dur = round(max(0.5, cur + 0.10), 4)
    return words, total_dur


# ---------------------------------------------------------------------------
# Manifest read/write, `render_from_manifest`, and XML consistency check
# ---------------------------------------------------------------------------


def resolve_target_resolution(
    clips: list[dict[str, Any]],
    *,
    aspect: str | None = None,
    resolution: str | None = None,
    encode_preset: str = "drive-safe",
) -> str:
    """Resolve output resolution (`WxH`).

    If neither `--resolution` nor `--aspect` is explicitly set and all clips share
    a valid resolution, use that resolution; otherwise default to `9:16` (`720x1280`).
    When `encode_preset == "drive-safe"`, dimensions are rounded to even integers.
    """
    if resolution:
        w, h = parse_resolution(resolution)
    elif aspect:
        if aspect not in _ASPECT_TO_RESOLUTION:
            raise CutError(f"Unsupported aspect {aspect!r}; choose from {sorted(_ASPECT_TO_RESOLUTION)}", exit_code=1)
        w, h = parse_resolution(_ASPECT_TO_RESOLUTION[aspect])
    else:
        clip_res = {c["resolution"] for c in clips if c.get("width", 0) > 0 and c.get("height", 0) > 0}
        if len(clip_res) == 1:
            w, h = parse_resolution(next(iter(clip_res)))
        else:
            w, h = parse_resolution(_ASPECT_TO_RESOLUTION["9:16"])
    if encode_preset == "drive-safe":
        w, h = even_dimensions(w, h)
    return f"{w}x{h}"


def build_manifest_dict(
    *,
    plan: dict[str, Any],
    clips: list[dict[str, Any]],
    project_dir: Path,
    audio_path: Path,
    words_path: Path,
    resolution: str,
    fps: int,
    words_per_chunk: int | str = 3,
    cut_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the project `manifest.yaml` dictionary compatible with `export_editor_package`."""
    clip_by_name = {c["name"]: c for c in clips}
    scenes_out: list[dict[str, Any]] = []
    for s in plan["scenes"]:
        c_info = clip_by_name.get(s["clip"])
        c_path = c_info["path"] if c_info else s["clip"]
        scenes_out.append(
            {
                "index": int(s["index"]),
                "clip_path": str(c_path),
                "start_s": float(s["start_s"]),
                "end_s": float(s["end_s"]),
                "duration_s": float(s["duration_s"]),
                "clip_trim_start_s": float(s["clip_trim_start_s"]),
                "scene_type": "clip",
                "vo_text": str(s.get("vo_text") or ""),
            }
        )

    try:
        rel_audio = str(audio_path.resolve().relative_to(project_dir.resolve()))
    except ValueError:
        rel_audio = str(audio_path.resolve())

    try:
        rel_words = str(words_path.resolve().relative_to(project_dir.resolve()))
    except ValueError:
        rel_words = str(words_path.resolve())

    manifest: dict[str, Any] = {
        "version": 1,
        "resolution": resolution,
        "fps": int(fps),
        "words_per_chunk": words_per_chunk,
        "audio_path": rel_audio,
        "words_path": rel_words,
        "scenes": scenes_out,
    }
    if cut_meta:
        manifest["cut"] = cut_meta
    return manifest


def write_cut_manifest(manifest: dict[str, Any], manifest_path: Path) -> Path:
    """Write `manifest.yaml` to disk."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return manifest_path


def _resolve_manifest_asset(raw_path: str | None, manifest_dir: Path) -> Path | None:
    if not raw_path:
        return None
    p = Path(raw_path).expanduser()
    if p.is_absolute():
        return p
    cand = (manifest_dir / p).resolve()
    if cand.exists():
        return cand
    return (Path.cwd() / p).resolve()


def render_from_manifest(
    manifest_path: str | Path,
    out_path: str | Path,
    *,
    captions: bool | None = None,
    caption_style: str | None = None,
    caption_y: float | None = None,
    caption_size: int | None = None,
    words_per_chunk: int | str | None = None,
    report_path: str | Path | None = None,
    fps: int | None = None,
    encode_preset: str | None = None,
) -> dict[str, Any]:
    """Render the final MP4 directly from a `manifest.yaml` file.

    Frame-accurate per-scene trim and duration (`-ss trim -i clip` + `tpad=stop_mode=clone`
    + `-frames:v slot_frames`), scale/crop-to-fill normalisation, voiceover mux,
    and optional caption burn. Uses `drive_safe_encode_args` by default (`encode_preset="drive-safe"`).
    """
    mpath = Path(manifest_path).expanduser().resolve()
    if not mpath.is_file():
        raise CutError(f"Manifest not found: {mpath}", exit_code=1)

    manifest = yaml.safe_load(mpath.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict) or not manifest.get("scenes"):
        raise CutError(f"Manifest {mpath} contains no scenes.", exit_code=1)

    manifest_dir = mpath.parent
    cut_cfg = manifest.get("cut") if isinstance(manifest.get("cut"), dict) else {}

    resolved_preset = str(
        encode_preset
        if encode_preset is not None
        else (cut_cfg.get("encode_preset") or manifest.get("encode_preset") or "drive-safe")
    )
    if resolved_preset not in ENCODE_PRESETS:
        raise CutError(f"Invalid encode_preset {resolved_preset!r}; choose from {ENCODE_PRESETS}", exit_code=2)

    res_str = str(manifest.get("resolution") or "720x1280")
    w, h = parse_resolution(res_str)
    if resolved_preset == "drive-safe":
        w, h = even_dimensions(w, h)
        res_str = f"{w}x{h}"

    fps_val = int(fps if fps is not None else (manifest.get("fps") or cut_cfg.get("fps") or 24))

    do_captions = captions if captions is not None else bool(cut_cfg.get("captions", True))
    c_style = caption_style or str(cut_cfg.get("caption_style") or "bangers")
    c_y = float(caption_y if caption_y is not None else cut_cfg.get("caption_y", 0.65))
    wpc = words_per_chunk if words_per_chunk is not None else manifest.get("words_per_chunk", cut_cfg.get("words_per_chunk", 3))
    c_size_raw = caption_size if caption_size is not None else cut_cfg.get("caption_size")
    resolved_c_size = int(c_size_raw) if c_size_raw is not None else max(18, round(55 * (w / 720.0)))

    audio_file = _resolve_manifest_asset(manifest.get("audio_path"), manifest_dir)
    if audio_file is None or not audio_file.is_file():
        raise CutError(f"Voiceover audio not found for manifest: {manifest.get('audio_path')}", exit_code=1)

    words_file = _resolve_manifest_asset(manifest.get("words_path"), manifest_dir)

    out_p = Path(out_path).expanduser().resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    rep_p = Path(report_path).expanduser().resolve() if report_path else (manifest_dir / "caption-report.json")

    scenes = manifest["scenes"]
    total_frames = 0

    with tempfile.TemporaryDirectory(prefix="parallax-cut-render-") as td:
        td_path = Path(td)
        seg_files: list[Path] = []
        for pos, scene in enumerate(scenes):
            clip_file = _resolve_manifest_asset(scene.get("clip_path"), manifest_dir)
            if clip_file is None or not clip_file.is_file():
                raise CutError(f"Scene {pos} clip not found: {scene.get('clip_path')}", exit_code=1)

            if scene.get("start_s") is not None and scene.get("end_s") is not None:
                s_frame = round(float(scene["start_s"]) * fps_val)
                e_frame = round(float(scene["end_s"]) * fps_val)
                slot_frames = max(1, e_frame - s_frame)
            else:
                slot_frames = max(1, round(float(scene.get("duration_s", 5.0)) * fps_val))

            slot_dur = slot_frames / float(fps_val)
            total_frames += slot_frames
            trim_start = max(0.0, float(scene.get("clip_trim_start_s") or 0.0))

            seg_out = td_path / f"seg_{pos:03d}.mp4"
            vf = (
                f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},fps={fps_val},setsar=1,"
                f"tpad=stop_mode=clone:stop_duration={slot_dur:.4f}"
            )
            res = run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{trim_start:.4f}",
                    "-i",
                    str(clip_file),
                    "-vf",
                    vf,
                    "-frames:v",
                    str(slot_frames),
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-pix_fmt",
                    "yuv420p",
                    str(seg_out),
                ],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                raise CutError(f"ffmpeg segment render failed for scene {pos}: {res.stderr[:400]}", exit_code=1)
            seg_files.append(seg_out)

        total_dur_s = total_frames / float(fps_val)
        concat_txt = td_path / "concat.txt"
        concat_txt.write_text("".join(f"file '{p}'\n" for p in seg_files), encoding="utf-8")

        base_mp4 = td_path / "base_muxed.mp4"
        mux_cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_txt),
            "-i",
            str(audio_file),
            "-filter_complex",
            f"[1:a]apad,atrim=duration={total_dur_s:.6f}[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-frames:v",
            str(total_frames),
            "-preset",
            "fast",
        ]
        if resolved_preset == "drive-safe":
            mux_cmd += drive_safe_encode_args(fps=fps_val, include_audio=True)
        else:
            mux_cmd += [
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
            ]
        mux_cmd.append(str(base_mp4))
        res_mux = run_ffmpeg(mux_cmd, capture_output=True, text=True)
        if res_mux.returncode != 0:
            raise CutError(f"ffmpeg concat/mux failed: {res_mux.stderr[:400]}", exit_code=1)

        caption_safe_ok = True
        if do_captions and words_file is not None and words_file.is_file():
            captioned_tmp = td_path / "captioned.mp4"
            burn_captions(
                video_path=str(base_mp4),
                words_json=str(words_file),
                output_path=str(captioned_tmp),
                words_per_chunk=wpc,
                fontsize=resolved_c_size,
                caption_style=c_style,
                report_path=str(rep_p),
                caption_y=c_y,
            )
            if resolved_preset == "drive-safe":
                res_fast = run_ffmpeg(
                    [
                        "ffmpeg",
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        str(captioned_tmp),
                        "-frames:v",
                        str(total_frames),
                        "-preset",
                        "fast",
                        *drive_safe_encode_args(fps=fps_val, include_audio=True),
                        str(out_p),
                    ],
                    capture_output=True,
                    text=True,
                )
            else:
                # Re-mux with +faststart to guarantee container flags
                res_fast = run_ffmpeg(
                    [
                        "ffmpeg",
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        str(captioned_tmp),
                        "-c",
                        "copy",
                        "-movflags",
                        "+faststart",
                        str(out_p),
                    ],
                    capture_output=True,
                    text=True,
                )
            if res_fast.returncode != 0:
                shutil.copy2(captioned_tmp, out_p)

            if rep_p.is_file():
                try:
                    rep_data = json.loads(rep_p.read_text(encoding="utf-8"))
                    boxes = rep_data.get("boxes") or []
                    caption_safe_ok = all(bool(b.get("inside_safe", True) and b.get("inside_frame", True)) for b in boxes)
                except Exception:
                    pass
        else:
            shutil.copy2(base_mp4, out_p)

    # Update manifest `video_path` so export_editor_package can include reference video if desired
    probed_video_dur = probe_duration(out_p) or total_dur_s
    probed_vo_dur = probe_audio_duration(audio_file) or total_dur_s

    return {
        "video_path": str(out_p),
        "video_duration_s": round(float(probed_video_dur), 4),
        "expected_duration_s": round(total_dur_s, 4),
        "voiceover_duration_s": round(float(probed_vo_dur), 4),
        "duration_diff_s": round(abs(float(probed_video_dur) - float(probed_vo_dur)), 4),
        "fps": fps_val,
        "resolution": res_str,
        "encode_preset": resolved_preset,
        "captions_burned": bool(do_captions and words_file and words_file.is_file()),
        "caption_report_path": str(rep_p) if (do_captions and rep_p.is_file()) else None,
        "caption_safe_area": "ok" if caption_safe_ok else "overflow",
    }


def check_manifest_xml_consistency(
    manifest_path: str | Path,
    xml_path: str | Path,
) -> tuple[bool, list[str]]:
    """Verify that `timeline.xml` scene durations and in-points match `manifest.yaml`."""
    mpath = Path(manifest_path).expanduser().resolve()
    xpath = Path(xml_path).expanduser().resolve()
    problems: list[str] = []

    if not mpath.is_file():
        return False, [f"Manifest not found: {mpath}"]
    if not xpath.is_file():
        return False, [f"Timeline XML not found: {xpath}"]

    manifest = yaml.safe_load(mpath.read_text(encoding="utf-8")) or {}
    scenes = manifest.get("scenes") or []
    root = ET.parse(xpath).getroot()
    fps = int(root.findtext("sequence/rate/timebase") or manifest.get("fps") or 24)
    clipitems = root.findall("sequence/media/video/track/clipitem")

    if len(clipitems) != len(scenes):
        problems.append(f"Scene count mismatch: manifest has {len(scenes)}, XML has {len(clipitems)}")
        return False, problems

    for idx, (scene, ci) in enumerate(zip(scenes, clipitems)):
        xml_start = int(ci.findtext("start") or 0)
        xml_end = int(ci.findtext("end") or 0)
        xml_dur_frames = xml_end - xml_start

        if scene.get("start_s") is not None and scene.get("end_s") is not None:
            m_start_f = round(float(scene["start_s"]) * fps)
            m_end_f = round(float(scene["end_s"]) * fps)
            m_dur_frames = m_end_f - m_start_f
        else:
            m_dur_frames = round(float(scene.get("duration_s", 0.0)) * fps)

        if xml_dur_frames != m_dur_frames:
            problems.append(
                f"Scene {idx}: manifest duration is {m_dur_frames} frames "
                f"({m_dur_frames / fps:.4f}s), XML is {xml_dur_frames} frames ({xml_dur_frames / fps:.4f}s)"
            )

        xml_in = int(ci.findtext("in") or 0)
        m_in = round(float(scene.get("clip_trim_start_s") or 0.0) * fps)
        if abs(xml_in - m_in) > 1:
            problems.append(f"Scene {idx}: manifest trim in is {m_in} frames, XML <in> is {xml_in} frames")

    return len(problems) == 0, problems


# ---------------------------------------------------------------------------
# High-level `run_cut` orchestrator
# ---------------------------------------------------------------------------


def run_cut(
    *,
    out_path: str | Path,
    script_path: str | Path | None = None,
    clips_dir: str | Path | None = None,
    map_path: str | Path | None = None,
    project_dir: str | Path | None = None,
    audio_path: str | Path | None = None,
    words_path: str | Path | None = None,
    process_audio: bool = False,
    speed: float = 1.15,
    voice: str = "nova",
    voice_model: str = "tts-mini",
    style: str | None = None,
    max_sentence_silence: float = 0.25,
    vo_chunk_seconds: float = 60.0,
    vo_gap: float = 0.25,
    vo_lufs: float = -16.0,
    vo_lufs_tolerance: float = 1.5,
    vo_normalize: bool = True,
    vo_verify: bool = True,
    vo_retries: int = 2,
    strict_vo: bool = False,
    provider: Any | None = None,
    transcriber: Any | None = None,
    fps: int = 24,
    aspect: str | None = None,
    resolution: str | None = None,
    captions: bool = True,
    caption_style: str = "bangers",
    caption_y: float = 0.65,
    caption_size: int | None = None,
    words_per_chunk: int | str = 3,
    max_clip_s: float | None = None,
    allow_hold: bool = False,
    export_editor: bool = True,
    from_manifest: str | Path | None = None,
    encode_preset: str = "drive-safe",
    plan_only: bool = False,
    force: bool = False,
    quiet: bool = False,
) -> dict[str, Any]:
    """Execute the `parallax cut` workflow end-to-end."""
    if encode_preset not in ENCODE_PRESETS:
        raise CutError(f"Invalid encode-preset {encode_preset!r}; choose from {ENCODE_PRESETS}", exit_code=2)
    out_p = Path(out_path).expanduser().resolve()
    proj_p = (
        Path(project_dir).expanduser().resolve()
        if project_dir is not None
        else out_p.parent / f"{out_p.stem}.cut"
    )
    proj_p.mkdir(parents=True, exist_ok=True)

    # Mode A: `--from-manifest FILE` — skip planning, re-render + re-export from hand-edited manifest
    if from_manifest is not None:
        mpath = Path(from_manifest).expanduser().resolve()
        if not mpath.is_file():
            raise CutError(f"Manifest file not found: {mpath}", exit_code=1)
        proj_p = mpath.parent
        render_res = render_from_manifest(
            mpath,
            out_p,
            captions=captions,
            caption_style=caption_style,
            caption_y=caption_y,
            caption_size=caption_size,
            words_per_chunk=words_per_chunk,
            encode_preset=encode_preset,
        )
        # Copy or link reference video into manifest_dir so export_editor_package can include it
        manifest_data = yaml.safe_load(mpath.read_text(encoding="utf-8")) or {}
        m_fps = int(manifest_data.get("fps") or fps)
        export_dir: Path | None = None
        consistency_str = "skipped"
        consistency_ok = True
        if export_editor:
            export_target = proj_p / "assets"
            export_editor_package(
                folder=proj_p,
                out_dir=export_target,
                manifest_path=mpath,
                fps=m_fps,
            )
            export_dir = export_target
            xml_file = export_target / "timeline.xml"
            ok, probs = check_manifest_xml_consistency(mpath, xml_file)
            consistency_ok = ok
            consistency_str = "manifest==xml ok" if ok else f"manifest!=xml ({'; '.join(probs)})"
            if not ok:
                raise CutError(f"Manifest/XML consistency check failed: {'; '.join(probs)}", exit_code=1)

        scenes_list = manifest_data.get("scenes") or []
        return {
            "status": "ok",
            "mode": "from_manifest",
            "project_dir": str(proj_p),
            "manifest_path": str(mpath),
            "voiceover_source": "manifest",
            "voiceover_duration_s": render_res["voiceover_duration_s"],
            "segments_count": len(scenes_list),
            "reuse_count": 0,
            "warnings": [],
            "video_path": render_res["video_path"],
            "video_duration_s": render_res["video_duration_s"],
            "duration_diff_s": render_res["duration_diff_s"],
            "caption_report_path": render_res["caption_report_path"],
            "caption_safe_area": render_res["caption_safe_area"],
            "editor_export_path": str(export_dir) if export_dir else None,
            "timeline_xml_path": str(export_dir / "timeline.xml") if export_dir else None,
            "consistency": consistency_str,
            "consistency_ok": consistency_ok,
            "tts_cost_usd": 0.0,
        }

    # Mode B: Standard or `--plan-only` run
    if script_path is None:
        raise CutError("--script FILE is required (unless --from-manifest is given).", exit_code=1)
    if clips_dir is None:
        raise CutError("--clips DIR is required (unless --from-manifest is given).", exit_code=1)

    spath = Path(script_path).expanduser().resolve()
    if not spath.is_file():
        raise CutError(f"Script file not found: {spath}", exit_code=1)
    raw_script = spath.read_text(encoding="utf-8")
    if not raw_script.strip():
        raise CutError(f"Script file is empty: {spath}", exit_code=1)

    cdir = Path(clips_dir).expanduser().resolve()
    clips = discover_clips(cdir, probe=True)
    target_res = resolve_target_resolution(clips, aspect=aspect, resolution=resolution, encode_preset=encode_preset)
    clip_durations = {c["name"]: float(c["duration_s"]) for c in clips}

    # 1. Resolve mapping (order: --map -> infer from names -> fail exit 2 with MAPPING NEEDED)
    mapping = resolve_mapping(
        script_text=raw_script,
        clips=clips,
        project_dir=proj_p,
        map_path=map_path,
        clips_dir=cdir,
        script_path=spath,
        out_path=out_p,
    )
    clean_script = mapping["clean_script"]
    segments = mapping["segments"]

    # 2. Voiceover / word timings (in --plan-only, never call paid TTS)
    if plan_only:
        raw_key, proc_key = compute_vo_cache_keys(
            script_text=clean_script,
            voice=voice,
            voice_model=voice_model,
            style=style,
            speed=speed,
            max_sentence_silence=max_sentence_silence,
            vo_chunk_seconds=vo_chunk_seconds,
            vo_gap=vo_gap,
            vo_lufs=vo_lufs,
            vo_normalize=vo_normalize,
        )
        cache_dir = proj_p / "cache" / "vo"
        proc_words_cache = cache_dir / f"{proc_key}.words.json"
        if audio_path is not None and words_path is not None:
            vo_info = resolve_or_generate_voiceover(
                clean_script=clean_script,
                project_dir=proj_p,
                audio_path=audio_path,
                words_path=words_path,
                process_audio=process_audio,
                speed=speed,
                max_sentence_silence=max_sentence_silence,
                vo_chunk_seconds=vo_chunk_seconds,
                vo_gap=vo_gap,
                vo_lufs=vo_lufs,
                vo_lufs_tolerance=vo_lufs_tolerance,
                vo_normalize=vo_normalize,
                vo_verify=vo_verify,
                vo_retries=vo_retries,
                strict_vo=strict_vo,
                transcriber=transcriber,
                quiet=True,
            )
            est_cost = 0.0
            timing_is_estimated = False
        elif not force and proc_words_cache.is_file() and (cache_dir / f"{proc_key}.mp3").is_file():
            vo_info = resolve_or_generate_voiceover(
                clean_script=clean_script,
                project_dir=proj_p,
                voice=voice,
                voice_model=voice_model,
                style=style,
                speed=speed,
                max_sentence_silence=max_sentence_silence,
                vo_chunk_seconds=vo_chunk_seconds,
                vo_gap=vo_gap,
                vo_lufs=vo_lufs,
                vo_lufs_tolerance=vo_lufs_tolerance,
                vo_normalize=vo_normalize,
                vo_verify=vo_verify,
                vo_retries=vo_retries,
                strict_vo=strict_vo,
                provider=provider,
                transcriber=transcriber,
                force=False,
                quiet=quiet,
            )
            est_cost = 0.0
            timing_is_estimated = False
        else:
            spec = TTS_MODELS.get(voice_model)
            est_cost = round(float(spec.estimate_tts_usd(len(clean_script)) if spec else 0.0), 6)
            est_words, est_dur = estimate_word_timings_from_script(clean_script, speed=speed)
            vo_info = {
                "source": "estimated",
                "audio_path": str(proj_p / "voiceover.mp3"),
                "words": est_words,
                "duration_s": est_dur,
                "cost_usd": 0.0,
                "estimated_tts_cost_usd": est_cost,
                "tts_calls": 0,
            }
            timing_is_estimated = True
    else:
        vo_info = resolve_or_generate_voiceover(
            clean_script=clean_script,
            project_dir=proj_p,
            audio_path=audio_path,
            words_path=words_path,
            process_audio=process_audio,
            voice=voice,
            voice_model=voice_model,
            style=style,
            speed=speed,
            max_sentence_silence=max_sentence_silence,
            vo_chunk_seconds=vo_chunk_seconds,
            vo_gap=vo_gap,
            vo_lufs=vo_lufs,
            vo_lufs_tolerance=vo_lufs_tolerance,
            vo_normalize=vo_normalize,
            vo_verify=vo_verify,
            vo_retries=vo_retries,
            strict_vo=strict_vo,
            provider=provider,
            transcriber=transcriber,
            force=force,
            quiet=quiet,
        )
        est_cost = vo_info["cost_usd"]
        timing_is_estimated = False

    # 3. Pure planning
    plan = plan_cut(
        script_text=clean_script,
        words=vo_info["words"],
        segments=segments,
        clip_durations=clip_durations,
        fps=fps,
        audio_duration=vo_info["duration_s"],
        max_clip_s=max_clip_s,
        allow_hold=allow_hold,
    )
    all_warnings = list(mapping.get("inference_log") or []) + list(plan["warnings"])
    loudness_info = vo_info.get("loudness")
    if isinstance(loudness_info, dict):
        for lw in loudness_info.get("warnings") or []:
            all_warnings.append(str(lw))
    verify_info = vo_info.get("verification")
    if isinstance(verify_info, dict):
        residuals = verify_info.get("residual_mismatches") or []
        if residuals:
            all_warnings.append(
                f"voiceover verification residual mismatches: {_format_residual_mismatch_summary(residuals)}"
            )

    # Write script-aligned words.json & captions.srt into project_dir
    script_words_path = proj_p / "words.json"
    script_words_payload = {
        "words": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in plan["script_words"]],
        "total_duration_s": plan["audio_duration_s"],
        "estimated": timing_is_estimated,
    }
    script_words_path.write_text(json.dumps(script_words_payload, indent=2), encoding="utf-8")

    captions_dir = proj_p / "captions"
    captions_dir.mkdir(parents=True, exist_ok=True)
    srt_content = _build_srt(script_words_payload["words"], words_per_chunk=words_per_chunk)
    if srt_content.strip():
        (captions_dir / "captions.srt").write_text(srt_content, encoding="utf-8")

    # Write manifest.yaml (or manifest preview in --plan-only) and plan.json
    script_sha = hashlib.sha256(" ".join(clean_script.split()).encode("utf-8")).hexdigest()[:16]
    cut_meta = {
        "script_sha": script_sha,
        "mapping_source": mapping["source"],
        "speed": speed,
        "max_sentence_silence": max_sentence_silence,
        "captions": captions,
        "caption_style": caption_style,
        "caption_y": caption_y,
        "caption_size": caption_size,
        "words_per_chunk": words_per_chunk,
        "allow_hold": allow_hold,
        "encode_preset": encode_preset,
        "estimated": timing_is_estimated,
    }
    manifest_dict = build_manifest_dict(
        plan=plan,
        clips=clips,
        project_dir=proj_p,
        audio_path=Path(vo_info["audio_path"]),
        words_path=script_words_path,
        resolution=target_res,
        fps=fps,
        words_per_chunk=words_per_chunk,
        cut_meta=cut_meta,
    )
    manifest_path = write_cut_manifest(manifest_dict, proj_p / "manifest.yaml")

    plan_report = {
        "status": "overrun" if (plan["overruns"] and not allow_hold) else ("plan_only" if plan_only else "ok"),
        "mapping_source": mapping["source"],
        "inference_log": mapping.get("inference_log", []),
        "timing_estimated": timing_is_estimated,
        "estimated_tts_cost_usd": est_cost,
        "voiceover_source": vo_info["source"],
        "voiceover_duration_s": plan["audio_duration_s"],
        "video_duration_s": plan["video_duration_s"],
        "fps": fps,
        "resolution": target_res,
        "segments_count": len(plan["scenes"]),
        "reuse_count": plan["reuse_count"],
        "unused_clips": plan["unused_clips"],
        "reused_clips": plan["reused_clips"],
        "overruns": plan["overruns"],
        "fuzzy_anchors": plan["fuzzy_anchors"],
        "unmatched_script_tokens": plan["unmatched_script_tokens"],
        "warnings": all_warnings,
        "loudness": vo_info.get("loudness"),
        "loudness_report_path": vo_info.get("loudness_report_path"),
        "verification": vo_info.get("verification"),
        "verify_report_path": vo_info.get("verify_report_path"),
        "scenes": plan["scenes"],
        "project_dir": str(proj_p),
        "manifest_path": str(manifest_path),
    }
    (proj_p / "plan.json").write_text(json.dumps(plan_report, indent=2), encoding="utf-8")

    # Overrun with no --allow-hold -> exit code 2 naming overrunning segment(s) and suggesting fix
    if plan["overruns"] and not allow_hold:
        lines = ["SLOT OVERRUN DETECTED (exit 2):"]
        for ov in plan["overruns"]:
            lines.append(
                f"  - Segment {ov['segment']} (anchor {ov['anchor']!r}, clip {ov['clip']}): "
                f"slot needs {ov['slot_s']:.2f}s but usable clip length is {ov['usable_s']:.2f}s "
                f"(overrun by {ov['excess_s']:.2f}s)."
            )
            lines.append(f"    Fix: {ov['suggestion']}")
        raise CutError("\n".join(lines), exit_code=2, report=plan_report)

    if plan_only:
        return {
            **plan_report,
            "tts_cost_usd": 0.0,
            "plan_json_path": str(proj_p / "plan.json"),
        }

    # 4. Render video FROM manifest.yaml
    render_res = render_from_manifest(
        manifest_path,
        out_p,
        captions=captions,
        caption_style=caption_style,
        caption_y=caption_y,
        caption_size=caption_size,
        words_per_chunk=words_per_chunk,
        fps=fps,
        encode_preset=encode_preset,
    )

    # 5. Export editor package from the SAME manifest into `<project>/assets/` + verify consistency
    export_dir_p: Path | None = None
    consistency_str = "skipped"
    consistency_ok = True
    if export_editor:
        export_target = proj_p / "assets"
        export_editor_package(
            folder=proj_p,
            out_dir=export_target,
            manifest_path=manifest_path,
            fps=fps,
        )
        export_dir_p = export_target
        xml_file = export_target / "timeline.xml"
        ok, probs = check_manifest_xml_consistency(manifest_path, xml_file)
        consistency_ok = ok
        consistency_str = "manifest==xml ok" if ok else f"manifest!=xml ({'; '.join(probs)})"
        if not ok:
            raise CutError(f"Manifest/XML consistency check failed: {'; '.join(probs)}", exit_code=1)

    return {
        **plan_report,
        "status": "ok",
        "video_path": render_res["video_path"],
        "video_duration_s": render_res["video_duration_s"],
        "duration_diff_s": render_res["duration_diff_s"],
        "caption_report_path": render_res["caption_report_path"],
        "caption_safe_area": render_res["caption_safe_area"],
        "editor_export_path": str(export_dir_p) if export_dir_p else None,
        "timeline_xml_path": str(export_dir_p / "timeline.xml") if export_dir_p else None,
        "consistency": consistency_str,
        "consistency_ok": consistency_ok,
        "tts_cost_usd": vo_info["cost_usd"],
        "tts_calls": vo_info.get("tts_calls", 0),
    }
