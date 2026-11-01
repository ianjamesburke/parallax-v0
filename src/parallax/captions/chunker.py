"""Caption grouping that respects authored sentence and phrase boundaries.

Fixed word counts and smart letter counts are upper bounds. Punctuation ends
chunks early; original text and word timestamps are retained unchanged.

`chunk_words` is the policy entry point used by burn and SRT export: captions
are one line of 1-2 words, 3 only when short (style `max_words`,
`three_word_max_letters`). `split_chunks_to_fit` then splits any multi-word
chunk the renderer cannot fit on one line.
"""
from __future__ import annotations

import re
from typing import Callable

from ..log import get_logger
from .styles import DEFAULT_MAX_WORDS, DEFAULT_THREE_WORD_MAX_LETTERS

log = get_logger(__name__)

# Titles and common abbreviations that normally introduce the next word.
_CONTINUING_ABBREVIATIONS = frozenset({
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.",
    "vs.", "e.g.", "i.e.", "no.", "fig.",
})
_CLOSING_MARKS = '\"\'”’»)]}'


def _ends_phrase(word: str, next_word: str = "") -> bool:
    """Small punctuation heuristic, not a language parser.

    Internal periods/commas (3.5, 1,000) are not boundaries. Honorifics and
    initials stay with their following names. Contextual abbreviations such
    as 'etc.' and 'U.S.' continue before lowercase words, but can end a
    sentence before a capitalized word.
    """
    token = word.rstrip(_CLOSING_MARKS)
    if not token:
        return False
    if token.endswith(("!", "?", ",", ";", ":", "—", "–", "…")):
        return True
    if not token.endswith("."):
        return False
    if token.endswith("..."):
        return True
    if token.lower() in _CONTINUING_ABBREVIATIONS:
        return False
    if re.fullmatch(r"[A-Z]\.", token):
        return False
    if token.lower() in {"etc.", "approx.", "a.m.", "p.m."} or re.fullmatch(r"(?:[A-Za-z]\.){2,}", token):
        following = next_word.lstrip('\"\'“‘«([{')
        return not following or not following[0].islower()
    return True


def _letters(words: list[dict]) -> int:
    """Alphanumeric characters across `words` (punctuation and spaces ignored)."""
    return sum(ch.isalnum() for w in words for ch in w["word"])


def _chunk_from(words: list[dict]) -> dict:
    return {
        "text": " ".join(word["word"] for word in words),
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "words": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in words],
    }


def _chunk_words(
    words: list[dict],
    *,
    max_words: int | None = None,
    max_letters: int | None = None,
    three_word_max_letters: int | None = None,
    carry_connectors: bool | None = None,
) -> list[dict]:
    """Greedy grouping; any limit reached ends the chunk before the next word.

    `three_word_max_letters` gates the 3rd and later words: they join only if
    the chunk's alphanumeric letters stay within the budget. Connector carry
    (trailing and/or/but moves to the next chunk) defaults on for fixed
    word-count chunking and off for letter-budget (smart) chunking.
    """
    if carry_connectors is None:
        carry_connectors = max_letters is None
    chunks: list[dict] = []
    current: list[dict] = []
    letters = 0

    for index, word in enumerate(words):
        length = len(word["word"])
        if current and ((max_words is not None and len(current) >= max_words)
                        or (max_letters is not None and letters + length > max_letters)
                        or (three_word_max_letters is not None and len(current) >= 2
                            and _letters(current) + _letters([word]) > three_word_max_letters)):
            # Keep a trailing connector with the phrase it introduces when
            # the fixed-size limit would otherwise strand it on the prior card.
            carry = []
            if carry_connectors and len(current) > 1 and current[-1]["word"].lower() in {"and", "or", "but"}:
                carry = [current.pop()]
            chunks.append(_chunk_from(current))
            current = carry
            letters = sum(len(item["word"]) for item in carry)
        current.append(word)
        letters += length
        following = words[index + 1]["word"] if index + 1 < len(words) else ""
        if _ends_phrase(word["word"], following):
            chunks.append(_chunk_from(current))
            current = []
            letters = 0
    if current:
        chunks.append(_chunk_from(current))
    return chunks


def _fixed_chunk_words(words: list[dict], max_words: int) -> list[dict]:
    """At most N words per caption, ending early at phrase punctuation."""
    if max_words < 1:
        raise ValueError("words_per_chunk must be a positive integer or 'smart'")
    return _chunk_words(words, max_words=max_words)


def _smart_chunk_words(words: list[dict], max_letters: int = 4) -> list[dict]:
    """Combine tiny adjacent words up to a letter budget within each phrase.

    Words longer than the budget stay alone. Sentence and phrase punctuation
    always ends the current chunk, even when the next short word would fit.
    """
    if max_letters < 1:
        raise ValueError("max_letters must be positive")
    return _chunk_words(words, max_letters=max_letters)


def chunk_words(words: list[dict], words_per_chunk: int | str = "smart", style: dict | None = None) -> list[dict]:
    """Group words into one-line caption chunks under the style's word policy.

    - `"smart"`: adjacent words while their letters stay ≤4 (mostly 1 word).
    - integer N: at most N words, clamped to the style's `max_words`
      (default 3). Chunks prefer 1-2 words: a 3rd word joins only when the
      chunk's letters stay within `three_word_max_letters` (default 12).

    Both modes end at phrase punctuation and never exceed `max_words`.
    """
    style = style or {}
    max_words = max(1, int(style.get("max_words", DEFAULT_MAX_WORDS)))
    three_letters = style.get("three_word_max_letters", DEFAULT_THREE_WORD_MAX_LETTERS)
    three_letters = None if three_letters is None else int(three_letters)
    if isinstance(words_per_chunk, str) and words_per_chunk.strip().lower() == "smart":
        return _chunk_words(words, max_letters=4, max_words=max_words, carry_connectors=False)
    n = int(words_per_chunk)
    if n < 1:
        raise ValueError("words_per_chunk must be a positive integer or 'smart'")
    if n > max_words:
        log.info("words_per_chunk=%d exceeds the caption style's max_words=%d; clamping to %d",
                 n, max_words, max_words)
        n = max_words
    return _chunk_words(words, max_words=n, three_word_max_letters=three_letters, carry_connectors=True)


def split_chunks_to_fit(chunks: list[dict], fits: Callable[[str], bool]) -> list[dict]:
    """Split multi-word chunks that `fits` rejects into greedy one-line runs.

    Each piece takes its start/end from its own words, and keeps its `words`
    list so karaoke highlight windows still follow the spoken word. A single
    word that does not fit is left alone (the layout shrinks it instead).
    """
    out: list[dict] = []
    for chunk in chunks:
        words = chunk.get("words") or []
        if len(words) < 2 or fits(chunk["text"]):
            out.append(chunk)
            continue
        pieces: list[dict] = []
        current: list[dict] = [words[0]]
        for word in words[1:]:
            candidate = current + [word]
            if fits(" ".join(w["word"] for w in candidate)):
                current = candidate
            else:
                pieces.append(_chunk_from(current))
                current = [word]
        pieces.append(_chunk_from(current))
        log.debug("caption chunk %r split to fit one line: %s", chunk["text"], [p["text"] for p in pieces])
        out += pieces
    return out
