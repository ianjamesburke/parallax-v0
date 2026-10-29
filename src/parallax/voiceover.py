"""Voiceover synthesis (pure TTS).

`generate_voiceover` calls `openrouter.generate_tts` (always Gemini TTS via
OpenRouter) and runs `_trim_long_pauses` to surgically cut overlong
inter-word silence. Speed adjustment is no longer the voiceover module's
concern — it lives in `audio.speedup` and runs as `stage_speed_adjust`
after this stage.

`_mock_voiceover` produces a deterministic silent stand-in for
PARALLAX_TEST_MODE.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import time
import shutil
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .ffmpeg_utils import run_ffmpeg
from .log import get_logger
from .models import TTS_MODELS
from .shim import is_test_mode, output_dir

log = get_logger(__name__)

SOCIAL_SEED_VOICE_PROMPT = "A fast documentary narrator revealing a secret: fast-paced social-media-ad delivery, rapid with minimal pauses between lines, highly expressive animated storyteller building intrigue, deep, low, warm, slightly gravelly male voice, punchy emphasis on key words."

_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
)
_TENS = (
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
)
_ORDINALS_EXACT = {
    1: "first",
    2: "second",
    3: "third",
    4: "fourth",
    5: "fifth",
    6: "sixth",
    7: "seventh",
    8: "eighth",
    9: "ninth",
    10: "tenth",
    11: "eleventh",
    12: "twelfth",
    13: "thirteenth",
    14: "fourteenth",
    15: "fifteenth",
    16: "sixteenth",
    17: "seventeenth",
    18: "eighteenth",
    19: "nineteenth",
    20: "twentieth",
    30: "thirtieth",
    40: "fortieth",
    50: "fiftieth",
    60: "sixtieth",
    70: "seventieth",
    80: "eightieth",
    90: "ninetieth",
}
_SPOKEN_NUM_WORD_SET = (
    set(_ONES)
    | set(_TENS[2:])
    | set(_ORDINALS_EXACT.values())
    | {"hundred", "thousand", "million", "billion", "hundredth", "thousandth", "millionth"}
)


def int_to_words(n: int) -> list[str]:
    """Expand a non-negative integer into English number words (e.g. 97 -> ['ninety', 'seven'])."""
    if n < 0:
        return ["minus"] + int_to_words(-n)
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        tens_w = _TENS[n // 10]
        rem = n % 10
        return [tens_w] if rem == 0 else [tens_w, _ONES[rem]]
    if n < 1000:
        rem = n % 100
        head = [_ONES[n // 100], "hundred"]
        return head if rem == 0 else head + int_to_words(rem)
    for scale_val, scale_word in (
        (1_000_000_000, "billion"),
        (1_000_000, "million"),
        (1_000, "thousand"),
    ):
        if n >= scale_val:
            hi, rem = divmod(n, scale_val)
            head = int_to_words(hi) + [scale_word]
            return head if rem == 0 else head + int_to_words(rem)
    return [str(n)]


def ordinal_to_words(n: int) -> list[str]:
    """Expand a positive integer ordinal into spoken words (e.g. 21 -> ['twenty', 'first'])."""
    if n in _ORDINALS_EXACT:
        return [_ORDINALS_EXACT[n]]
    if 20 < n < 100:
        tens_val = (n // 10) * 10
        ones_val = n % 10
        if ones_val == 0:
            return [_ORDINALS_EXACT[tens_val]]
        return [_TENS[n // 10], _ORDINALS_EXACT[ones_val]]
    words = int_to_words(n)
    if not words:
        return [f"{n}th"]
    last = words[-1]
    if last == "hundred":
        return words[:-1] + ["hundredth"]
    if last == "thousand":
        return words[:-1] + ["thousandth"]
    if last == "million":
        return words[:-1] + ["millionth"]
    for k, v in _ORDINALS_EXACT.items():
        if _ONES[k] == last if k < len(_ONES) else (_TENS[k // 10] == last):
            return words[:-1] + [v]
    return words[:-1] + [last + "th"]


def _number_str_to_words(num_str: str, *, is_year_candidate: bool = False) -> list[str]:
    has_comma = "," in num_str
    clean = num_str.replace(",", "")
    if "." in clean:
        whole_s, frac_s = clean.split(".", 1)
        whole_words = int_to_words(int(whole_s)) if whole_s else ["zero"]
        frac_words = [_ONES[int(ch)] for ch in frac_s if ch.isdigit()]
        return whole_words + ["point"] + frac_words
    n = int(clean)
    # 4-digit un-comma'd numbers in 1100..1999 or 2010..2099 are typically spoken as year pairs
    if is_year_candidate and not has_comma and ((1100 <= n <= 1999) or (2010 <= n <= 2099)):
        hi, lo = divmod(n, 100)
        if lo == 0:
            return int_to_words(hi) + ["hundred"]
        if lo < 10:
            return int_to_words(hi) + ["oh"] + int_to_words(lo)
        return int_to_words(hi) + int_to_words(lo)
    return int_to_words(n)


def expand_numbers_and_symbols(text: str) -> str:
    """Expand numerals, percentages, currency, ordinals, and era markers to spoken words.

    Examples:
      - ``"97%"`` -> ``"ninety seven percent"``
      - ``"$5"`` -> ``"five dollars"``, ``"$1"`` -> ``"one dollar"``
      - ``"1,000"`` -> ``"one thousand"``
      - ``"45 AD"`` / ``"45 A.D."`` -> ``"forty five ad"``
      - ``"21st"`` -> ``"twenty first"``
    """
    s = text.replace("—", " ").replace("–", " ")
    # Normalize dotted era markers A.D. / B.C. / B.C.E. / C.E.
    s = re.sub(r"\bA\.D\.(?!\w)", "AD", s, flags=re.IGNORECASE)
    s = re.sub(r"\bB\.C\.E\.(?!\w)", "BCE", s, flags=re.IGNORECASE)
    s = re.sub(r"\bB\.C\.(?!\w)", "BC", s, flags=re.IGNORECASE)
    s = re.sub(r"\bC\.E\.(?!\w)", "CE", s, flags=re.IGNORECASE)

    # Split hyphens between alphabetic characters ("ninety-seven" -> "ninety seven")
    s = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", " ", s)

    # Currency: $5, $1,000, $2.50
    def _repl_currency(m: re.Match[str]) -> str:
        raw_num = m.group(1)
        words = _number_str_to_words(raw_num, is_year_candidate=False)
        clean_num = raw_num.replace(",", "")
        unit = "dollar" if clean_num in ("1", "1.0", "1.00") else "dollars"
        return " ".join(words + [unit])

    s = re.sub(r"\$(\d[\d,]*(?:\.\d+)?)", _repl_currency, s)

    # Percentages: 97%, 99.5 %, 20%
    def _repl_percent(m: re.Match[str]) -> str:
        raw_num = m.group(1)
        words = _number_str_to_words(raw_num, is_year_candidate=False)
        return " ".join(words + ["percent"])

    s = re.sub(r"\b(\d[\d,]*(?:\.\d+)?)\s*%", _repl_percent, s)

    # Ordinals: 1st, 2nd, 3rd, 4th, 21st
    def _repl_ordinal(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return " ".join(ordinal_to_words(n))

    s = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", _repl_ordinal, s, flags=re.IGNORECASE)

    # Cardinal integers / decimals / years
    def _repl_number(m: re.Match[str]) -> str:
        raw_num = m.group(0)
        words = _number_str_to_words(raw_num, is_year_candidate=True)
        return " ".join(words)

    s = re.sub(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\b", _repl_number, s)
    return s


def normalize_text_for_verify(text: str) -> list[str]:
    """Normalize arbitrary script or transcript text into canonical lowercase words for verification."""
    expanded = expand_numbers_and_symbols(text)
    raw_words = re.findall(r"[a-z0-9]+", expanded.lower())
    # Coalesce "per", "cent" -> "percent", and drop filler "and" between number words
    coalesced: list[str] = []
    i = 0
    n = len(raw_words)
    while i < n:
        w = raw_words[i]
        if w == "per" and i + 1 < n and raw_words[i + 1] == "cent":
            coalesced.append("percent")
            i += 2
            continue
        if (
            w == "a"
            and i + 1 < n
            and raw_words[i + 1] == "d"
            and coalesced
            and coalesced[-1] in _SPOKEN_NUM_WORD_SET
        ):
            coalesced.append("ad")
            i += 2
            continue
        if (
            w == "b"
            and i + 1 < n
            and raw_words[i + 1] == "c"
            and coalesced
            and coalesced[-1] in _SPOKEN_NUM_WORD_SET
        ):
            coalesced.append("bc")
            i += 2
            continue
        if (
            w == "and"
            and coalesced
            and coalesced[-1] in {"hundred", "thousand", "million", "billion"}
            and i + 1 < n
            and raw_words[i + 1] in _SPOKEN_NUM_WORD_SET
        ):
            i += 1
            continue
        coalesced.append(w)
        i += 1
    return coalesced



def apply_pronunciations(text: str, pronunciations: dict[str, str]) -> str:
    """Substitute phonetic spellings before TTS. Word-boundary, case-insensitive."""
    for word, phonetic in pronunciations.items():
        text = re.sub(r'\b' + re.escape(word) + r'\b', phonetic, text, flags=re.IGNORECASE)
    return text


def _restore_pronunciations(
    words: list[dict], pronunciations: dict[str, str]
) -> list[dict]:
    """Restore original spellings in aligned word list after forced alignment."""
    if not pronunciations:
        return list(words)

    def _norm(s: str) -> str:
        return re.sub(r'[-]', '', s).lower().strip(".,!?;:")

    phonetic_map = {_norm(v): k for k, v in pronunciations.items()}
    result = []
    for w in words:
        n = _norm(w["word"])
        original = phonetic_map.get(n)
        result.append({**w, "word": original} if original else w)
    return result


def generate_voiceover_dict(
    text: str,
    voice: str = "nova",
    out_dir: str | None = None,
    style: str | None = None,
    style_hint: str | None = None,
    voice_model: str = "tts-mini",
    pronunciations: dict[str, str] | None = None,
    trim_pauses: bool | float = True,
    verify: bool = False,
    voice_prompt: str | None = None,
) -> dict:
    """Generate voiceover and optionally cap overlong inter-word silences.

    Calls `openrouter.generate_tts` (always Gemini TTS via OpenRouter),
    then optionally caps gaps with bilateral lead-in and crossfades. A true
    ``trim_pauses`` caps gaps at 0.75s; a float sets the cap (minimum 0.4s).
    Standalone CLI synthesis opts out by default to preserve provider audio.
    Speed adjustment is
    handled separately by `audio.speedup` / `stage_speed_adjust`.

    `voice` is an OpenAI voice name (e.g. 'nova', 'shimmer', 'alloy'). See
    `parallax models show tts-mini` for the full list.

    `style` accepts presets from `gemini_tts.STYLE_PRESETS`
    (`rapid_fire`, `fast`, `calm`, `natural`); `style_hint` is freeform.

    Returns dict: {audio_path, words_path, words, total_duration_s}.
    """
    dest = Path(out_dir or str(output_dir()))
    dest.mkdir(parents=True, exist_ok=True)

    if isinstance(trim_pauses, float) and not isinstance(trim_pauses, bool) and trim_pauses < 0.4:
        raise ValueError("trim_pauses gap cap must be at least 0.4s to retain word lead-in")

    pronunciations = pronunciations or {}

    if is_test_mode():
        return json.loads(_mock_voiceover(text, dest))

    from . import openrouter

    t0 = time.monotonic()
    log.info(
        "voiceover: voice=%s voice_model=%s chars=%d style=%s",
        voice, voice_model, len(text), style or style_hint or "default",
    )

    tts_text = apply_pronunciations(text, pronunciations) if pronunciations else text
    if pronunciations and tts_text != text:
        log.info("voiceover: applying %d pronunciation substitution(s)", len(pronunciations))

    if voice_model == "seed-audio" and not voice_prompt:
        raise ValueError("seed-audio requires a meaningful voice_prompt")
    if voice_model == "elevenlabs":
        from . import elevenlabs as _elevenlabs
        raw_path, words_with_ends, raw_audio_duration = _elevenlabs.generate_tts(
            text=tts_text,
            voice=voice,
            out_dir=dest,
        )
    else:
        raw_path, words_with_ends, raw_audio_duration = openrouter.generate_tts(
            text=(f'{voice_prompt} He says: "{tts_text}"' if voice_model == "seed-audio" else tts_text),
            alias=voice_model,
            voice=voice,
            out_dir=dest,
            style=style,
            style_hint=style_hint,
        )

    if verify:
        from .whisper_backend import transcribe_wav
        heard = transcribe_wav(str(raw_path), label="TTS verification")
        wanted, got = normalize_text_for_verify(text), normalize_text_for_verify(" ".join(x["word"] for x in heard))
        score = SequenceMatcher(None, wanted, got).ratio()
        if score < .85 or len(got) > max(4, len(wanted) * 1.5):
            keep = Path(os.environ.get("PARALLAX_REJECT_DIR") or dest)
            keep.mkdir(parents=True, exist_ok=True)
            rejected = keep / f"{Path(raw_path).stem}.rejected{Path(raw_path).suffix}"
            shutil.move(str(raw_path), rejected)
            excerpt = " ".join(x["word"] for x in heard[:30])
            raise RuntimeError(f"TTS model did not read the script (word similarity {score:.2f}). Transcript: {excerpt!r}. Bad audio kept at {rejected}. Try dropping --style or another voice model; use --no-verify to override.")
    import shutil as _shutil
    raw_suffix = Path(raw_path).suffix or ".mp3"
    audio_path = dest / f"voiceover{raw_suffix}"
    trim_gap_count = 0
    trim_removed_s = 0.0
    if trim_pauses is False:
        log.info("voiceover: trim_pauses=false — skipping silence removal")
        _shutil.copy2(raw_path, audio_path)
        words_trimmed = list(words_with_ends)
        duration = max(words_trimmed[-1]["end"], raw_audio_duration) if words_trimmed else raw_audio_duration
    elif isinstance(trim_pauses, float) and not isinstance(trim_pauses, bool):
        words_trimmed, duration, trim_gap_count, trim_removed_s = _trim_long_pauses(
            Path(raw_path), list(words_with_ends), audio_path, max_gap_s=trim_pauses,
        )
    else:
        words_trimmed, duration, trim_gap_count, trim_removed_s = _trim_long_pauses(
            Path(raw_path), list(words_with_ends), audio_path,
        )
    words_trimmed = _restore_pronunciations(words_trimmed, pronunciations)

    if Path(raw_path).resolve() != audio_path.resolve():
        Path(raw_path).unlink(missing_ok=True)

    words_path = dest / "vo_words.json"
    words_path.write_text(json.dumps({
        "words": words_trimmed,
        "total_duration_s": duration,
    }, indent=2))

    elapsed = int((time.monotonic() - t0) * 1000)
    log.info("voiceover: done duration=%.2fs elapsed=%dms", duration, elapsed)

    return {
        "raw_audio_path": str(audio_path),
        "audio_path": str(audio_path),
        "words_path": str(words_path),
        "words": words_trimmed,
        "total_duration_s": duration,
        "trim_pauses_gaps": trim_gap_count,
        "trim_pauses_removed_s": round(trim_removed_s, 3),
    }


def generate_voiceover(
    text: str,
    voice: str = "nova",
    out_dir: str | None = None,
    style: str | None = None,
    style_hint: str | None = None,
    voice_model: str = "tts-mini",
    pronunciations: dict[str, str] | None = None,
    trim_pauses: bool | float = True,
    voice_prompt: str | None = None,
) -> str:
    """JSON-string wrapper around generate_voiceover_dict. Kept for CLI/external callers.

    Returns JSON: {audio_path, words_path, words, total_duration_s}.
    """
    return json.dumps(generate_voiceover_dict(
        text=text,
        voice=voice,
        out_dir=out_dir,
        style=style,
        style_hint=style_hint,
        voice_model=voice_model,
        pronunciations=pronunciations,
        trim_pauses=trim_pauses,
        verify=False,  # legacy programmatic wrapper preserves its historical no-local-model contract
        voice_prompt=voice_prompt,
    ))


def _trim_long_pauses(
    audio_path: Path,
    words: list[dict],
    out_path: Path,
    max_gap_s: float = 0.75,
) -> tuple[list[dict], float, int, float]:
    """Cap long gaps with lead-in on both sides and a silent crossfade.

    Alignment can place a word's start after its first audible phoneme, so
    joining at the timestamp itself can clip that phoneme. ``cap_pauses``
    retains half of ``max_gap_s`` before the next word (at least 0.2s),
    crossfades inside the retained quiet region, and adjusts word times for
    both the removed gap and crossfade overlap.
    """
    import shutil
    import tempfile

    if max_gap_s < 0.4:
        raise ValueError("trim_pauses gap cap must be at least 0.4s to retain word lead-in")

    probe = run_ffmpeg(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True,
    )
    total_dur = float(probe.stdout.strip()) if probe.stdout.strip() else (words[-1]["end"] if words else 0.0)
    if not any(b["start"] - a["end"] > max_gap_s for a, b in zip(words, words[1:])):
        shutil.copy2(audio_path, out_path)
        return list(words), max(words[-1]["end"], total_dur) if words else total_dur, 0, 0.0

    from .audio import cap_pauses
    with tempfile.TemporaryDirectory() as temp_dir:
        capped_wav = Path(temp_dir) / "capped.wav"
        result = cap_pauses(
            str(audio_path), str(capped_wav), max_gap_s=max_gap_s,
            crossfade_s=0.05, words=words,
        )
        encode = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                  "-i", str(capped_wav)]
        if out_path.suffix.lower() == ".mp3":
            encode += ["-c:a", "libmp3lame", "-b:a", "128k"]
        encode.append(str(out_path))
        run_ffmpeg(encode, check=True, capture_output=True, text=True)

    adjusted = result["adjusted_words"]
    gaps = result["gaps_trimmed"]
    total_removed = result["seconds_removed"]
    final_probe = run_ffmpeg(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(out_path)],
        capture_output=True, text=True,
    )
    new_dur = float(final_probe.stdout.strip()) if final_probe.stdout.strip() else result["new_duration_s"]
    log.info("_trim_long_pauses: %d gaps safely capped (%.2fs removed), duration %.2fs→%.2fs",
             gaps, total_removed, total_dur, new_dur)
    from . import runlog
    runlog.event(
        "audio.trim_pauses",
        level="DEBUG",
        gap_count=gaps,
        seconds_removed=round(total_removed, 3),
        duration_before_s=round(total_dur, 3),
        duration_after_s=round(new_dur, 3),
    )
    return adjusted, new_dur, gaps, total_removed


def mock_words(text: str) -> list[dict]:
    """Deterministic synthetic per-word timings for PARALLAX_TEST_MODE.

    Stands in for forced alignment, which cannot run on the silent stub audio.
    """
    words: list[dict] = []
    t = 0.0
    for w in re.sub(r'\[[^\]]*\]', '', text).split():
        dur = max(0.3, len(w) * 0.06)
        words.append({"word": w, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur + 0.05
    return words


def _mock_voiceover(text: str, dest: Path) -> str:
    words = mock_words(text)
    total = words[-1]["end"] if words else 0.0

    # Silence mp3
    audio_path = dest / "voiceover.mp3"
    run_ffmpeg(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
         "-t", str(total), "-c:a", "libmp3lame", str(audio_path)],
        capture_output=True,
    )
    words_path = dest / "vo_words.json"
    words_path.write_text(json.dumps({"words": words, "total_duration_s": total}, indent=2))
    return json.dumps({
        "raw_audio_path": str(audio_path),
        "audio_path": str(audio_path),
        "words_path": str(words_path),
        "words": words,
        "total_duration_s": total,
    })


# ---------------------------------------------------------------------------
# VoiceProvider protocol & implementations (OpenRouter/Gemini + ElevenLabs)
# ---------------------------------------------------------------------------


@dataclass
class VoiceSynthesisResult:
    """Result of `VoiceProvider.synthesize(text, voice, style)`.

    Supports attribute access (`.audio_path`, `.words`, `.duration_s`, `.cost_usd`),
    2-tuple unpacking (`audio_path, words = provider.synthesize(...)`), and dict-style
    lookup (`res["audio_path"]`, `res["words"]`).
    """

    audio_path: str
    words: list[dict[str, Any]] = field(default_factory=list)
    duration_s: float = 0.0
    cost_usd: float = 0.0

    def __iter__(self):
        yield self.audio_path
        yield self.words

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return (self.audio_path, self.words, self.duration_s)[key]
        if key in ("audio_path", "raw_audio_path"):
            return self.audio_path
        if key == "words":
            return self.words
        if key in ("duration_s", "total_duration_s"):
            return self.duration_s
        if key == "cost_usd":
            return self.cost_usd
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


@runtime_checkable
class VoiceProvider(Protocol):
    """Protocol for TTS voice synthesis providers used by `parallax cut`."""

    def synthesize(
        self,
        text: str,
        voice: str = "nova",
        style: str | None = None,
        *,
        out_dir: str | Path | None = None,
    ) -> VoiceSynthesisResult | tuple[Any, ...] | dict[str, Any]:
        """Synthesize `text` and return raw audio path + word timings."""
        ...


class OpenRouterVoiceProvider:
    """VoiceProvider implementation backed by OpenRouter / Gemini TTS (`generate_voiceover_dict`)."""

    def __init__(
        self,
        voice_model: str = "tts-mini",
        *,
        style_hint: str | None = None,
        voice_prompt: str | None = None,
        pronunciations: dict[str, str] | None = None,
    ) -> None:
        self.voice_model = voice_model
        self.style_hint = style_hint
        self.voice_prompt = voice_prompt
        self.pronunciations = pronunciations

    def synthesize(
        self,
        text: str,
        voice: str = "nova",
        style: str | None = None,
        *,
        out_dir: str | Path | None = None,
    ) -> VoiceSynthesisResult:
        dest = Path(out_dir or str(output_dir()))
        dest.mkdir(parents=True, exist_ok=True)
        spec = TTS_MODELS.get(self.voice_model)
        est_cost = float(spec.estimate_tts_usd(len(text)) if spec else 0.0)
        vo_res = generate_voiceover_dict(
            text=text,
            voice=voice,
            out_dir=str(dest),
            style=style,
            style_hint=self.style_hint,
            voice_model=self.voice_model,
            pronunciations=self.pronunciations,
            trim_pauses=False,
            verify=False,
            voice_prompt=self.voice_prompt,
        )
        raw_src = str(vo_res.get("raw_audio_path") or vo_res["audio_path"])
        raw_words = list(vo_res.get("words") or [])
        raw_dur = float(vo_res.get("total_duration_s") or (raw_words[-1]["end"] if raw_words else 0.0))
        return VoiceSynthesisResult(
            audio_path=raw_src,
            words=raw_words,
            duration_s=raw_dur,
            cost_usd=round(est_cost, 6),
        )


class ElevenLabsVoiceProvider:
    """VoiceProvider implementation wrapping `parallax.elevenlabs.generate_tts`."""

    def __init__(self, model: str = "eleven_multilingual_v2") -> None:
        self.voice_model = "elevenlabs"
        self.model = model

    def synthesize(
        self,
        text: str,
        voice: str = "nova",
        style: str | None = None,
        *,
        out_dir: str | Path | None = None,
    ) -> VoiceSynthesisResult:
        dest = Path(out_dir or str(output_dir()))
        dest.mkdir(parents=True, exist_ok=True)
        spec = TTS_MODELS.get("elevenlabs")
        est_cost = float(spec.estimate_tts_usd(len(text)) if spec else 0.0)
        if is_test_mode():
            mock_data = json.loads(_mock_voiceover(text, dest))
            return VoiceSynthesisResult(
                audio_path=str(mock_data["audio_path"]),
                words=list(mock_data["words"]),
                duration_s=float(mock_data["total_duration_s"]),
                cost_usd=0.0,
            )
        from . import elevenlabs as _elevenlabs

        raw_path, words, dur = _elevenlabs.generate_tts(
            text=text,
            voice=voice,
            out_dir=dest,
            model=self.model,
        )
        return VoiceSynthesisResult(
            audio_path=str(raw_path),
            words=list(words),
            duration_s=float(dur),
            cost_usd=round(est_cost, 6),
        )


def get_voice_provider(
    voice_model: str = "tts-mini",
    *,
    style_hint: str | None = None,
    voice_prompt: str | None = None,
    pronunciations: dict[str, str] | None = None,
) -> VoiceProvider:
    """Return the appropriate `VoiceProvider` for `voice_model`."""
    if voice_model == "elevenlabs":
        return ElevenLabsVoiceProvider()
    return OpenRouterVoiceProvider(
        voice_model=voice_model,
        style_hint=style_hint,
        voice_prompt=voice_prompt,
        pronunciations=pronunciations,
    )


def coerce_synthesis_result(
    raw_res: Any,
    *,
    default_cost_usd: float = 0.0,
) -> VoiceSynthesisResult:
    """Normalize any `VoiceProvider.synthesize` return value (VoiceSynthesisResult, dict, or tuple)."""
    if isinstance(raw_res, VoiceSynthesisResult):
        if raw_res.cost_usd == 0.0 and default_cost_usd > 0.0:
            return VoiceSynthesisResult(
                audio_path=raw_res.audio_path,
                words=raw_res.words,
                duration_s=raw_res.duration_s,
                cost_usd=default_cost_usd,
            )
        return raw_res
    if isinstance(raw_res, dict):
        apath = str(raw_res.get("raw_audio_path") or raw_res.get("audio_path") or "")
        words = list(raw_res.get("words") or [])
        dur = float(raw_res.get("duration_s") or raw_res.get("total_duration_s") or (words[-1]["end"] if words else 0.0))
        cost = float(raw_res.get("cost_usd", default_cost_usd))
        return VoiceSynthesisResult(audio_path=apath, words=words, duration_s=dur, cost_usd=cost)
    if isinstance(raw_res, (tuple, list)):
        apath = str(raw_res[0])
        words = list(raw_res[1]) if len(raw_res) > 1 else []
        dur = float(raw_res[2]) if len(raw_res) > 2 else float(words[-1]["end"] if words else 0.0)
        cost = float(raw_res[3]) if len(raw_res) > 3 else default_cost_usd
        return VoiceSynthesisResult(audio_path=apath, words=words, duration_s=dur, cost_usd=cost)
    raise TypeError(f"Unsupported VoiceProvider.synthesize return type: {type(raw_res).__name__}")

